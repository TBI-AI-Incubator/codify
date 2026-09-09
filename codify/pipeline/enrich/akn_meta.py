"""Bring the emitted `<meta>` and enacting formula within the AKN 3.0 schema, and stop
the day we ran being recorded as a date the law has.

Three defects, all ours: `FRBRWork/FRBRdate` carries a bare year, `<formula>` carries
a schema-invalid `@source`, and `FRBRExpression/FRBRdate` carries `date.today()`,
which the expression URI is then rebuilt from. Pure and idempotent, so the ingest pass
and the corpus backfill share it.
"""

from __future__ import annotations

import re
from datetime import date
from typing import cast

import structlog
from lxml import etree

from codify.akn import AKN_NS
from codify.frbr import EXPRESSION_URI_DATE

logger = structlog.get_logger()

_BARE_YEAR = re.compile(r"^\d{4}$")

# `codify.frbr.UNKNOWN_YEAR` as it lands in `FRBRdate`, which is an xs:date and
# cannot hold year zero.
_UNRESOLVED_YEAR = 1

# The provenance marker on a config-injected enacting formula. `@source` is
# refused by the schema; `@refersTo` carries the same value legally, and
# `validator._check_fabricated_formula` reads it back.
FORMULA_PROVENANCE = "#codify"


def normalise_akn_meta(
    akn_xml: str, *, work_date: date | str | None = None, expression_undated: bool = False
) -> str:
    """Give every FRBR date a value the schema accepts and the law supports, and move the
    formula marker off `@source`.

    A bare work year expands to 1 January where `work_date` agrees, that being the most a
    year supports; an empty one only `work_date` can fill. `expression_undated` means the
    lane read no expression date, so those take the work's date; a lane whose source
    states one leaves this alone, as does the backfill, which must not invent a work date.
    """
    root = etree.fromstring(akn_xml.encode("utf-8"))
    _normalise(root, work_date=work_date, expression_undated=expression_undated)
    return cast(str, etree.tostring(root, pretty_print=True, encoding="unicode"))


def normalise_akn_meta_if_changed(
    akn_xml: str, *, work_date: date | str | None = None, expression_undated: bool = False
) -> str | None:
    """The normalised document, or None when nothing needed changing.

    Both sides go through the same serialiser, so a document that merely differs
    from lxml's layout never reads as a change and never earns a stored rewrite.
    """
    root = etree.fromstring(akn_xml.encode("utf-8"))
    before = etree.tostring(root, pretty_print=True, encoding="unicode")
    _normalise(root, work_date=work_date, expression_undated=expression_undated)
    after = etree.tostring(root, pretty_print=True, encoding="unicode")
    return cast(str, after) if after != before else None


def _normalise(
    root: etree._Element, *, work_date: date | str | None, expression_undated: bool
) -> None:
    known = _as_date(work_date)

    for el in root.iter(f"{{{AKN_NS}}}FRBRdate"):
        raw = (el.get("date") or "").strip()
        if not raw:
            if known is None:
                logger.warning("frbr_date_empty_and_unresolvable", name=el.get("name") or "")
                continue
            el.set("date", known.isoformat())
            continue
        if not _BARE_YEAR.match(raw):
            continue
        if known is not None and known.year == int(raw):
            el.set("date", known.isoformat())
            continue
        el.set("date", f"{raw}-01-01")
        logger.info("frbr_date_defaulted_to_january", year=raw, name=el.get("name") or "")

    if expression_undated:
        # Every `<identification>`, so an attachment's own block moves with the
        # document's rather than keeping the run date on a nested annex.
        for ident in root.iter(f"{{{AKN_NS}}}identification"):
            _date_expression_from_work(ident)

    for fml in root.iter(f"{{{AKN_NS}}}formula"):
        if fml.get("source") != FORMULA_PROVENANCE:
            continue
        del fml.attrib["source"]
        fml.set("refersTo", FORMULA_PROVENANCE)


def _date_expression_from_work(ident: etree._Element) -> None:
    """Date an expression the source never dated from its own work, after the loop above has
        made that date full.

        The date element and the URI's `@date` segment move together, or the row disagrees
        with itself: `document_to_rows` reads `versions.expression_date` off the element and
    `versions.expression_uri` off the URI. Substituting in place keeps any
        component tail and format suffix. `FRBRManifestation/FRBRdate` stays, recording when
        the file was produced, though its URI moves. A work whose year never resolved is left
        alone.
    """
    work_el = _sole_date(ident, "FRBRWork")
    if work_el is None:
        return
    stated = (work_el.get("date") or "").strip()
    parsed = _as_date(stated)
    if parsed is None or parsed.year == _UNRESOLVED_YEAR:
        logger.warning("frbr_expression_left_on_run_date", reason="work_date_unusable", was=stated)
        return

    date_el = _sole_date(ident, "FRBRExpression")
    if date_el is None:
        return
    date_el.set("date", stated)
    # Cobalt writes `name="Generation"` at every level from one value. This date
    # is now the work's, so "Generation" would claim the expression was produced
    # on the day the law was made.
    date_el.set("name", "Original")

    for block in ("FRBRExpression", "FRBRManifestation"):
        parent = ident.find(f"{{{AKN_NS}}}{block}")
        if parent is None:
            continue
        for name in ("FRBRthis", "FRBRuri"):
            el = parent.find(f"{{{AKN_NS}}}{name}")
            if el is None:
                continue
            was = el.get("value") or ""
            if not EXPRESSION_URI_DATE.search(was):
                # No `@date` segment to move, and adding one would mint an identity no
                # row holds, so the mismatch is logged rather than invented. Tested
                # directly, since an unchanged substitution would also match every URI
                # already reading the right date, which is all of them on a re-run.
                logger.warning("frbr_uri_carries_no_date", block=block, name=name, uri=was)
                continue
            el.set("value", EXPRESSION_URI_DATE.sub(f"@{stated}", was, count=1))


def _sole_date(ident: etree._Element, block: str) -> etree._Element | None:
    """The block's one `FRBRdate`, or None where it has none or several. `FRBRdate` is
    `maxOccurs="unbounded"`, so taking the first in document order can take a generation
    date and stamp it onto the expression, worse than the defect this fixes. Ambiguity is
    refused and logged.
    """
    parent = ident.find(f"{{{AKN_NS}}}{block}")
    if parent is None:
        logger.warning("frbr_expression_left_on_run_date", reason="block_missing", block=block)
        return None
    found = parent.findall(f"{{{AKN_NS}}}FRBRdate")
    if len(found) != 1:
        logger.warning(
            "frbr_expression_left_on_run_date",
            reason="no_date" if not found else "ambiguous_dates",
            block=block,
            count=len(found),
        )
        return None
    return found[0]


def _as_date(value: date | str | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
