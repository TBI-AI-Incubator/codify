"""Turning a declared ambiguity into a question with a closed answer set.

`anchors.py` records where it chose between readings but not what the
alternatives were, because at the point of declaring it has already given up.
This reconstructs them from the jurisdiction's own hierarchy, so the offered set
is what the document class permits rather than what the model can imagine.
"""

from __future__ import annotations

from codify.adjudicate.verdict import ADJUDICABLE_REASONS, AdjudicationRequest
from codify.jurisdictions import JurisdictionConfig
from codify.quality.invariants import AmbiguitySpan

# Lines either side of the span, for a model that needs to see whether the line
# sits at a boundary or mid-sentence. Enough to judge, short enough to stay cheap.
CONTEXT_LINES = 6


def is_adjudicable(span: AmbiguitySpan) -> bool:
    """Whether a span poses a question with a closed answer.

    Most spans do not. A `duplicate_number` is a numbering decision the scanner
    already made and can defend, and a `numbering_gap` names an absence rather
    than a choice. Only the unclaimed-marker family asks something a reader of
    the page could settle.
    """
    return str(span.detail.get("reason", "")) in ADJUDICABLE_REASONS


def candidate_kinds(config: JurisdictionConfig | None, doctype: str) -> tuple[str, ...]:
    """The hierarchy levels this document class declares, outermost first.

    Empty when the class is unknown, which makes the request unanswerable rather
    than answerable with a guess: a model offered no kinds can only say no
    anchor, which is the right refusal.
    """
    if config is None:
        return ()
    doc_class = config.document_classes.get(doctype)
    if doc_class is None:
        return ()
    return tuple(dict.fromkeys(e.akn_element for e in doc_class.hierarchy))


def build_request(
    span: AmbiguitySpan,
    text: str,
    *,
    config: JurisdictionConfig | None,
    country: str,
    doctype: str,
    preceding_eid: str = "",
    following_eid: str = "",
    page_no: int | None = None,
    object_key: str = "",
) -> AdjudicationRequest:
    """One span plus the window around it. Offsets index the normalised text the
    scanner was given, which is what `AmbiguitySpan` documents them against."""
    kinds = candidate_kinds(config, doctype)
    if not kinds:
        raise ValueError(
            f"{country}/{doctype} declares no hierarchy, so there is nothing to choose "
            "between; record the span as unadjudicable rather than asking"
        )
    # `split`, not `splitlines`: the offset arithmetic counts newlines, and
    # `splitlines` also breaks on CR, form feed and the Unicode separators, any
    # one of which would slide every index after it and hand back a neighbour.
    lines = text.split("\n")
    line_no = text.count("\n", 0, span.start)
    line = lines[line_no] if line_no < len(lines) else ""
    if text[span.start : span.end] not in line:
        raise ValueError(
            f"span [{span.start}:{span.end}] does not index this text; pass the same "
            "normalised text the scanner was given"
        )
    return AdjudicationRequest(
        line=line,
        reason=str(span.detail.get("reason", "")),
        candidate_kinds=kinds,
        before=tuple(lines[max(0, line_no - CONTEXT_LINES) : line_no]),
        after=tuple(lines[line_no + 1 : line_no + 1 + CONTEXT_LINES]),
        preceding_eid=preceding_eid,
        following_eid=following_eid,
        page_no=page_no,
        object_key=object_key,
        country=country,
        doctype=doctype,
        detail=dict(span.detail),
    )


__all__ = ["CONTEXT_LINES", "build_request", "candidate_kinds", "is_adjudicable"]
