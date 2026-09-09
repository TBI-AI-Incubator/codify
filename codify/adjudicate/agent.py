"""The adjudicator: reads the page the regexes could not, and picks from a set.

Mirrors the repair agent's shape deliberately. The page-render boundary is
injected so this module carries no MinIO or DBOS I/O and can be exercised
offline against a stub renderer and a Pydantic AI TestModel.

What makes this safe is not the prompt, it is `validate_choice`: the model's
answer is rejected unless it names a hierarchy level the jurisdiction declares.
A refusal is a first-class answer and the instructions say so, because the
population this runs on is mostly prose that merely looks like a heading.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from pydantic_ai import Agent, BinaryContent, ModelRetry, PromptedOutput, RunContext
from pydantic_ai.models import Model

from codify.adjudicate.verdict import Adjudication, AdjudicationRequest, validate_choice

logger = structlog.get_logger()

# (object_key, page_no) -> PNG bytes of the rendered source page.
RenderPage = Callable[[str, int], Awaitable[bytes]]

_INSTRUCTIONS = """\
You decide one question about one line of a legal document: is this line a
structural marker that opens a numbered unit, and if so which level and number?

The scanner already tried and could not settle it. Its regexes match a keyword
followed by a number; this line either carries a keyword they did not recognise,
often because the scan mangled it, or it is ordinary prose that happens to open
the way a heading does.

Answer with a kind from the offered candidates, or null for "no anchor here".

Rules, in order of importance:
- Refusing is correct far more often than choosing. Most of these lines are
  cross-references ("under Article 5 of this Law"), sentence openings, or
  running headers. If the line does not OPEN a unit whose text follows, say null.
- Never name a kind that was not offered. The offered set is what this document
  class declares; anything else invents a hierarchy the document does not have.
- Read the rendered page when the extracted line is garbled. The letters may be
  corrupt where the layout is not: indentation, position and what follows the
  line often settle it when the characters cannot.
- The number is what is printed, with its enumerator punctuation as printed.
  Give null when the unit genuinely carries no number.
- Your reason is what a reviewer reads instead of the page. Cite what decided
  it, not what you concluded: "the line sits alone above indented body text and
  the keyword matches the mangled form of مادة" beats "this is an article".
"""


def build_adjudicator(
    model: Model, render_page: RenderPage
) -> Agent[AdjudicationRequest, Adjudication]:
    agent = Agent(
        model,
        deps_type=AdjudicationRequest,
        # Prompted rather than tool output, for the same reason the repair agent
        # uses it: reasoning models via the LiteLLM gateway cannot emit the
        # structured output as a tool call, and it leaves the tool slot free.
        output_type=PromptedOutput([Adjudication]),
        instructions=_INSTRUCTIONS,
        name="anchor-adjudicator",
        retries=2,
    )

    @agent.instructions
    def _the_question(ctx: RunContext[AdjudicationRequest]) -> str:
        d = ctx.deps
        offered = ", ".join(d.candidate_kinds) or "(none declared: you can only answer null)"
        between = (
            f" It would sit between {d.preceding_eid or 'the start'} and "
            f"{d.following_eid or 'the end'}."
        )
        return (
            f"The line, verbatim: {d.line!r}\n"
            f"Why the scanner could not settle it: {d.reason}\n"
            f"Candidate kinds: {offered}.{between}"
        )

    @agent.tool
    def surrounding_lines(ctx: RunContext[AdjudicationRequest]) -> str:
        """The lines either side of the one in question, as extracted."""
        d = ctx.deps
        before = "\n".join(d.before) or "(nothing before)"
        after = "\n".join(d.after) or "(nothing after)"
        return f"--- before ---\n{before}\n--- the line ---\n{d.line}\n--- after ---\n{after}"

    @agent.tool
    async def view_source_page(ctx: RunContext[AdjudicationRequest]) -> BinaryContent | str:
        """The rendered scan of the page this line came from. Read it when the
        extracted characters are corrupt: the layout survives what the text did
        not."""
        d = ctx.deps
        if d.page_no is None or not d.object_key:
            # Logged, not silent: a caller that never wired page mapping would
            # otherwise run the whole corpus text-only and read as healthy.
            logger.warning("adjudicator_page_unmapped", line=d.line[:60], country=d.country)
            return "no source page is mapped for this line; decide on the text alone"
        try:
            png = await render_page(d.object_key, d.page_no)
        except Exception as exc:  # noqa: BLE001
            # Degrade one adjudication rather than abort a corpus pass, but a
            # systemic failure (bad bucket, missing poppler) must be diagnosable:
            # without this line every span returns a plausible text-only verdict.
            logger.warning(
                "adjudicator_page_unavailable",
                object_key=d.object_key,
                page_no=d.page_no,
                error=type(exc).__name__,
            )
            return f"the page could not be rendered ({type(exc).__name__}); decide on the text"
        return BinaryContent(data=png, media_type="image/png")

    @agent.output_validator
    def _within_the_offered_set(
        ctx: RunContext[AdjudicationRequest], verdict: Adjudication
    ) -> Adjudication:
        try:
            return validate_choice(verdict, ctx.deps)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    return agent


__all__ = ["RenderPage", "build_adjudicator"]
