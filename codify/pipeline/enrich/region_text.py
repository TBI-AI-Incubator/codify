"""Region-aware structure text: what the structurer should see.

`combine_page_texts` gives every consumer the full page markdown, and metadata
plus the validator want that superset. The structurer must not weave furniture
into provisions, so this filters the combined text: `header` and `footer` lines
are dropped and `footnote` lines are relocated to the foot as standalone
paragraphs the body-fill emits separably, so the footnote lift catches them
deterministically. Regions decide which lines move; the kept text stays the
authoritative markdown. `aside`, `body` and `unknown` lines are never touched.

A region acts only on the page it was observed on. A heading that straddles a
page break is emitted twice, once as a catchword at the foot of the page and
again as the real heading overleaf; matching the catchword document-wide deleted
both, and with them whole chapters and articles.

One text-level filter serves both ingest lanes (the in-process PDF path and the
durable API structure step), so they cannot drift. Lives apart from `ocr.py`
because it depends on `regions`, which depends on `ocr`; that avoids the cycle.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Sequence

import structlog

from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import opens_attachment
from codify.pipeline.enrich.ocr import PageSpan
from codify.pipeline.enrich.regions import OVERLAP_FLOOR, Region, overlap, words

# A one-token region ("٣" in a footer) would spuriously match a short body line,
# so only whole multi-word blocks decide a drop or relocation. This also keeps a
# bare inline marker inside a provision from ever matching a footnote block.
_MIN_REGION_WORDS = 2


def _matches(line: str, region_text: str) -> bool:
    """The line IS this block, not merely contained in a longer one. Requiring
    overlap both ways stops a short body line whose words are a subset of a
    running footer from being dropped; that biases towards leaving furniture in,
    which the footnote lift and the aside pass then recover."""
    return (
        overlap(line, region_text) >= OVERLAP_FLOOR and overlap(region_text, line) >= OVERLAP_FLOOR
    )


def _acted(regions: dict[int, list[Region]]) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    """Furniture and footnote block texts worth acting on, keyed by their page."""
    drop: dict[int, list[str]] = {}
    tail: dict[int, list[str]] = {}
    for page in regions.values():
        for region in page:
            if len(words(region.text)) < _MIN_REGION_WORDS:
                continue
            if region.kind == "furniture":
                drop.setdefault(region.page_number, []).append(region.text)
            elif region.kind == "footnote":
                tail.setdefault(region.page_number, []).append(region.text)
    return drop, tail


def _blocks(by_page: dict[int, list[str]], page: int | None) -> list[str]:
    """This page's blocks; every page's when the line cannot be placed, which is
    the pre-span behaviour and the only reading available without page spans."""
    if page is None:
        return [block for blocks in by_page.values() for block in blocks]
    return by_page.get(page, [])


def _page_of(starts: list[int], pages: list[int], offset: int) -> int | None:
    """The page whose span holds this offset; the nearest page at the edges."""
    if not starts:
        return None
    return pages[max(bisect_right(starts, offset) - 1, 0)]


logger = structlog.get_logger()

# Layout pixels within which furniture blocks share a printed row.
_ROW_TOLERANCE = 15
# Longer furniture-typed text is not a running head; its numbers do not count.
_RUNNING_HEAD_MAX_WORDS = 8
# A page number this long is furniture wherever the text layer sets it; a shorter one only at
# the head or foot of its page, where a table value that equals it would not sit.
_PAGE_NUMBER_DIGITS = 3
# A footnote opens on its marker, whatever the engine typed the block.
_NOTE_OPENER = re.compile(r"^\s*[(（]\s*[\w*]{1,3}\s*[)）]")
# The text lines of a page's notes hold about as many words as their layout blocks, and
# share some of them: less than OVERLAP_FLOOR, as a garbled scan of a note is.
_NOTE_MASS_LOW, _NOTE_MASS_HIGH = 0.85, 1.3
_NOTE_AGREE_FLOOR = 0.3
# How the engine files a note, by the kind the region came to and the type it gave the block.
_NOTE_FILING = frozenset({("footnote", "references"), ("furniture", "footer")})


def _recurring_furniture(country: str) -> bool:
    config = load_config(country) if country else None
    return bool(config and config.structuring and config.structuring.recurring_furniture)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w.lower() for w in words(text))


def _digits(found: frozenset[str] | set[str]) -> str:
    return " ".join(sorted(found))


def _same_block(a: frozenset[str], b: frozenset[str]) -> bool:
    shared = len(a & b)
    return shared / len(a) >= OVERLAP_FLOOR and shared / len(b) >= OVERLAP_FLOOR


def _notes_of(regs: list[Region]) -> list[str]:
    """Blocks opening on a footnote marker that the engine filed as a reference or a footer.
    A text block at the foot that opens on a marker is a provision, however its place on the
    page reads, and a head that opens on one is a list item the engine took for a head."""
    return [
        r.text
        for r in regs
        if _NOTE_OPENER.match(r.text) and (r.kind, r.block_type) in _NOTE_FILING
    ]


def _heads(
    entries: list[tuple[Region, frozenset[str]]], country: str
) -> list[tuple[str, frozenset[str], list[str]]]:
    """Each furniture block as the engine cut it, and each row of blocks joined, with the
    blocks each is made of. A caption that opens an attachment is not part of a row, whatever
    it is printed beside. Matching reads the words, not their order."""
    rows: list[list[Region]] = []
    for region, _ in sorted(entries, key=lambda e: e[0].top):
        if opens_attachment(region.text, country):
            continue
        if rows and abs(region.top - rows[-1][0].top) <= _ROW_TOLERANCE:
            rows[-1].append(region)
        else:
            rows.append([region])
    heads = [(region.text, content, [region.text]) for region, content in entries]
    for row in rows:
        if len(row) > 1:
            parts = [r.text for r in row]
            joined = " ".join(parts)
            heads.append((joined, _tokens(joined), parts))
    return heads


def _recurring_candidates(
    regions: dict[int, list[Region]], country: str
) -> tuple[dict[int, list[str]], dict[int, Counter[str]], dict[int, list[str]]]:
    """Per page: short furniture another page repeats, as one block or as a row of them, the
    bare numbers its blocks hold and how many of each, and furniture or footnote blocks opening
    on a marker, returned apart as notes. A caption that opens an attachment is not a running
    head, nor evidence that another block is one."""
    notes = {page: _notes_of(regs) for page, regs in regions.items()}
    blocks = {
        page: [
            (r, _tokens(r.text))
            for r in regs
            if r.kind == "furniture" and not _NOTE_OPENER.match(r.text)
        ]
        for page, regs in regions.items()
    }
    heads = {page: _heads(entries, country) for page, entries in blocks.items()}
    # What a candidate is compared with: a caption is no evidence that a block recurs.
    evidence = {
        page: [
            content for text, content, _ in found if content and not opens_attachment(text, country)
        ]
        for page, found in heads.items()
    }
    texts: dict[int, list[str]] = {}
    numbers: dict[int, Counter[str]] = {}
    for page, entries in blocks.items():
        for region, content in entries:
            if content and all(t.isdigit() for t in content):
                numbers.setdefault(page, Counter())[_digits(content)] += 1
        candidates: list[str] = []
        for text, content, parts in heads[page]:
            if (
                not content
                or all(t.isdigit() for t in content)
                or opens_attachment(text, country)
                or sum(not t.isdigit() for t in content) > _RUNNING_HEAD_MAX_WORDS
            ):
                continue
            if any(
                _same_block(content, other)
                for q, others in evidence.items()
                if q != page
                for other in others
            ):
                candidates += [text, *parts]
        if candidates:
            texts[page] = candidates
    return texts, numbers, {page: found for page, found in notes.items() if found}


def _count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _settle_bare_numbers(
    lines: list[str],
    verdicts: list[tuple[int | None, str]],
    numbers: dict[int, Counter[str]],
) -> None:
    """A short bare number is furniture only in the band at the head or foot of its page; one
    among the page's text, a table value that equals the page number, stays. A page loses no
    more lines of a number than its layout holds blocks of it, the outermost first."""
    on_page: dict[int | None, list[int]] = {}
    for i, (page, _) in enumerate(verdicts):
        on_page.setdefault(page, []).append(i)
    for page, idxs in on_page.items():
        text = [i for i in idxs if verdicts[i][1] in ("keep", "tail") and lines[i].strip()]
        first, last = (text[0], text[-1]) if text else (len(lines), -1)
        owed = Counter(numbers.get(page or 0, {}))
        # Outermost first: nearest the head or the foot of the page.
        for i in sorted(
            (i for i in idxs if verdicts[i][1] == "bare"),
            key=lambda i: min(i - idxs[0], idxs[-1] - i),
        ):
            key = _digits(words(lines[i]))
            long = max(len(t) for t in words(lines[i])) >= _PAGE_NUMBER_DIGITS
            furniture = (long or i < first or i > last) and owed[key] > 0
            owed[key] -= furniture
            verdicts[i] = (verdicts[i][0], "drop" if furniture else "keep")


def _agree(a: str, b: str) -> bool:
    return overlap(a, b) >= _NOTE_AGREE_FLOOR and overlap(b, a) >= _NOTE_AGREE_FLOOR


def _note_runs(
    lines: list[str], verdicts: list[tuple[int | None, str]], notes: dict[int, list[str]]
) -> set[int]:
    """Each page's footnote run, counted back from the foot until it is as long as the
    notes, opens on a marker and says the same; else none, and the notes stay put."""
    by_page: dict[int, list[int]] = {}
    for i, (page, verdict) in enumerate(verdicts):
        if page is not None and verdict == "keep":
            by_page.setdefault(page, []).append(i)
    found: set[int] = set()
    for page, blocks in notes.items():
        mass = sum(_count(block) for block in blocks)
        held = 0
        placed = False
        run: list[int] = []
        for i in reversed(by_page.get(page, [])):
            held += _count(lines[i])
            run.append(i)
            if held > _NOTE_MASS_HIGH * mass:
                break
            if held >= _NOTE_MASS_LOW * mass and _NOTE_OPENER.match(lines[i]):
                if _agree(" ".join(lines[k] for k in run), " ".join(blocks)):
                    found.update(run)
                    placed = True
                break
        if not placed:
            logger.info("footnote_run_unplaced", page=page, notes=len(blocks))
    return found


def combine_text_for_structure(
    text: str,
    regions: dict[int, list[Region]],
    page_spans: Sequence[PageSpan] | None = None,
    *,
    country: str = "",
) -> str:
    """Filter already-combined page text for the structurer: drop header/footer
    lines, relocate footnote lines to the foot. Byte-identical to the input when
    no page carries regions (the born-digital or vision route).

    ``page_spans`` place each line on its page so a region acts only there.
    Without them every region acts on the whole document.

    ``country`` applies `recurring_furniture`: furniture is what repeats across pages, a
    one-off header or footer block stays, and a note the engine filed as a footer or reference
    is found in the text and relocated to the foot."""
    drop, tail = _acted(regions)
    numbers: dict[int, Counter[str]] = {}
    notes: dict[int, list[str]] = {}
    recurring = _recurring_furniture(country)
    if recurring:
        drop, numbers, notes = _recurring_candidates(regions, country)
        if page_spans:  # a run is read page by page; without spans notes move line by line
            tail = {
                page: [t for t in blocks if not _NOTE_OPENER.match(t)]
                for page, blocks in tail.items()
            }
        else:
            notes = {}
    # Unreadable-page markers pass through: the structurer lifts them out of the
    # provisions and places each as an editorial remark.
    if not drop and not tail and not numbers and not notes:
        return text

    ordered = sorted(page_spans or (), key=lambda span: span.start)
    starts = [span.start for span in ordered]
    pages = [span.page for span in ordered]

    lines = text.split("\n")
    verdicts: list[tuple[int | None, str]] = []
    captions: dict[str, int | None] = {}  # a caption's text, and the page it last printed on
    # Offsets index the text as given, which is what the spans were built over.
    offset = 0
    for line in lines:
        start, offset = offset, offset + len(line) + 1
        page = _page_of(starts, pages, start)
        found = words(line)
        if len(found) < _MIN_REGION_WORDS:
            # Too short to attribute, unless it may be its page's bare furniture number.
            bare = bool(found) and all(t.isdigit() for t in found)
            maybe = bare and _digits(found) in numbers.get(page or 0, ())
            verdicts.append((page, "bare" if maybe else "keep"))
        elif recurring and opens_attachment(line.strip(), country):
            # A caption is never furniture, nor a note; a running head that prints the page
            # before's again is.
            key = " ".join(line.split()).upper()
            before = captions.get(key)
            carried = key in captions and (page is None or before is None or page - before <= 1)
            again = carried and any(_matches(line, block) for block in _blocks(drop, page))
            captions[key] = page
            verdicts.append((page, "drop" if again else "keep"))
        elif any(_matches(line, block) for block in _blocks(drop, page)):
            verdicts.append((page, "drop"))  # header/footer furniture
        elif any(_matches(line, block) for block in _blocks(tail, page)):
            verdicts.append((page, "tail"))  # footnote to the foot
        else:
            verdicts.append((page, "keep"))
    _settle_bare_numbers(lines, verdicts, numbers)
    in_run = _note_runs(lines, verdicts, notes)

    kept: list[str] = []
    relocated: list[str] = []
    for i, line in enumerate(lines):
        verdict = verdicts[i][1]
        if verdict == "drop":
            continue
        if verdict == "tail":
            relocated.append(line.strip())
        elif i in in_run:
            if not line.strip():
                continue
            # A run opens on a marker, so a line without one continues the note before it.
            if _NOTE_OPENER.match(line):
                relocated.append(line.strip())
            else:
                relocated[-1] += " " + line.strip()
        else:
            kept.append(line)

    body = "\n".join(kept).rstrip()
    if not relocated:
        return body
    # Each footnote as its own blank-delimited paragraph past the last body line,
    # so the trailing window emits it as a standalone <p> the lift can move.
    return body + "\n\n" + "\n\n".join(relocated)
