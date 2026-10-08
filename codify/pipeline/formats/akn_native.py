"""Native authoritative AKN ingest, deterministic, no LLM, no enrichment.

For publishers that serve Akoma Ntoso themselves (legislation.gov.uk,
Laws.Africa). The document arrives structured and reference-marked, so the
pipeline's job is normalisation, not extraction: canonicalise the FRBR URIs
(publisher scheme → our `/akn/...`) and synthesise eIds for the rare
unnumbered elements that would violate the provisions eId/wId constraints.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import cast

import structlog
from lxml import etree

from codify.akn._parser import carried_ids
from codify.akn._schema import AKN_NS, parse_xml
from codify.akn.document import Document
from codify.akn.eids import ensure_unique_eids
from codify.akn.io import parse_akn
from codify.akn.vocabulary import TAG_TO_KIND
from codify.frbr import build_frbr_expression_uri, parse_source_ref
from codify.pipeline.events import Complete, Failed, IngestionEvent, Parsed, ValidationIssued

logger = structlog.get_logger()


class _EidPool:
    """Every id the document holds. A minted id is claimed here, so none repeats."""

    def __init__(self, taken: Iterable[str]) -> None:
        self.taken = set(taken)
        self._last: dict[str, int] = {}

    def mint(self, stem: str) -> str:
        """`stem` + the next free number: `codify-synth-3`, `att_1__paragraph_2`."""
        n = self._last.get(stem, 0)
        while f"{stem}{n + 1}" in self.taken:
            n += 1
        self._last[stem] = n + 1
        self.taken.add(f"{stem}{n + 1}")
        return f"{stem}{n + 1}"

    def claim(self, eid: str) -> str:
        """`eid` if free, else a numbered variant of it."""
        if eid in self.taken:
            return self.mint(f"{eid}_")
        self.taken.add(eid)
        return eid


def _scoped_stem(parent: str, kind: str) -> str:
    return f"{parent}__{kind}_" if parent else f"{kind}_"


def normalise_native_akn(doc: Document) -> tuple[Document, int]:
    """Canonicalise the document's FRBR URIs and fill missing eIds in place,
    in the body and in attachments (annexes, schedules).

    The work URI is derived from the document's own FRBRWork value via the
    core source-href tables, so any publisher `parse_source_ref` understands
    normalises without caller-supplied identifiers. Returns the document and
    the count of synthesised eIds. Ids set here live on the model only; `ingest`
    mints them in the XML first, so the stored document carries them too.
    """
    parsed = parse_source_ref(doc.frbr_work_uri)
    if parsed is not None:
        work, _ = parsed
        if work != doc.frbr_work_uri:
            doc.frbr_work_uri = work
            doc.frbr_expression_uri = build_frbr_expression_uri(
                work, doc.language, doc.expression_date
            )
    synth = 0
    seen: set[str] = set()
    pool = _EidPool(eid for top in (*doc.body, *doc.attachments) for eid in _eids(top))

    def _fill(el: object, parent: str | None = None, kind: str = "att") -> None:
        # `parent` is None in the body, whose gaps get a document-wide counter.
        nonlocal synth
        eid = getattr(el, "akn_eid", None)
        if not eid:
            synth += 1
            eid = pool.mint("codify-synth-" if parent is None else _scoped_stem(parent, kind))
        elif eid in seen:
            # Publishers occasionally repeat an eId; the first keeps it.
            eid = pool.mint(f"{eid}-dup")
        seen.add(eid)
        el.akn_eid = eid  # type: ignore[attr-defined]
        if not getattr(el, "akn_wid", None):  # keep real source wIds
            el.akn_wid = eid  # type: ignore[attr-defined]
        for child in getattr(el, "children", []) or []:
            _fill(child, eid if parent is not None else None, str(child.akn_type).lower())

    for top in doc.body:
        _fill(top)
    for att in doc.attachments:
        _fill(att, "")
    return doc, synth


def _eids(el: object) -> Iterator[str]:
    if eid := getattr(el, "akn_eid", None):
        yield eid
    for child in getattr(el, "children", []) or []:
        yield from _eids(child)


_NOT_UNITS = frozenset({"num", "heading", "content", "intro", "wrapUp"})


def mint_missing_eids(xml: str) -> tuple[str, int]:
    """Give every element the parser turns into a provision an `eId`, in the XML.

    The walk is the parser's own: through unknown wrappers, skipping `num` and
    `content`. Body gaps take `codify-synth-N`; attachment gaps take the parent's
    id plus the element's type (`att_1__paragraph_2`). Returns the XML and the
    number minted.
    """
    root = parse_xml(xml)
    doc_root = next(iter(root), None)
    if doc_root is None:
        return xml, 0
    pool = _EidPool(carried_ids(root))
    minted = 0

    def visit(parent: etree._Element, scope: str | None) -> None:
        nonlocal minted
        for child in parent:
            if not isinstance(child.tag, str):
                continue
            tag = etree.QName(child).localname
            if tag in _NOT_UNITS:
                continue
            if tag not in TAG_TO_KIND:
                visit(child, scope)
                continue
            eid = child.get("eId")
            if not eid:
                minted += 1
                eid = pool.mint(
                    "codify-synth-" if scope is None else _scoped_stem(scope, tag.lower())
                )
                child.set("eId", eid)
            visit(child, None if scope is None else eid)

    body = doc_root.find(f"{{{AKN_NS}}}body")
    if body is not None:
        visit(body, None)
    container = doc_root.find(f"{{{AKN_NS}}}attachments")
    for i, att in enumerate(
        container.findall(f"{{{AKN_NS}}}attachment") if container is not None else []
    ):
        eid = att.get("eId")
        if not eid:
            minted += 1
            eid = pool.claim(f"att_{i + 1}")
            att.set("eId", eid)
        for tag in ("mainBody", "body"):
            for inner in att.iter(f"{{{AKN_NS}}}{tag}"):
                visit(inner, eid)
    if not minted:
        return xml, 0
    return cast(str, etree.tostring(root, encoding="unicode")), minted


def canonicalise_identification(xml: str, work: str, expression: str | None) -> str:
    """Rewrite the top-level FRBRWork and FRBRExpression `uri` values to ours,
    so the stored XML names the same work the row does; `this` keeps any
    component suffix it carried. Nested component identifications and the
    manifestation stay the publisher's."""
    root = parse_xml(xml)
    identification = root.find("./{*}*/{*}meta/{*}identification")
    if identification is None:
        return xml
    for level, value in (("FRBRWork", work), ("FRBRExpression", expression)):
        node = identification.find(f"{{*}}{level}")
        if node is None or not value:
            continue
        uri = node.find("{*}FRBRuri")
        this = node.find("{*}FRBRthis")
        old = uri.get("value", "") if uri is not None else ""
        if uri is not None:
            uri.set("value", value)
        if this is not None:
            old_this = this.get("value", "")
            suffix = old_this[len(old) :] if old and old_this.startswith(old) else ""
            this.set("value", value + suffix)
    return cast(str, etree.tostring(root, encoding="unicode"))


async def ingest(
    source: Path | str,
    jurisdiction_code: str,
) -> AsyncIterator[IngestionEvent]:
    """Read published AKN, normalise, validate, Complete. Fully deterministic."""
    from codify.pipeline.enrich.validator import validate_akn

    try:
        path = source if isinstance(source, Path) else Path(source)
        xml = path.read_text(encoding="utf-8")
        yield Parsed(akn_xml_len=len(xml))

        # Ids are minted in the XML so the stored document, its rows and a later
        # re-parse all carry the same ones.
        xml, minted = mint_missing_eids(xml)

        # Publishers reuse eIds (legislation.gov.uk repeats term-* on every
        # inline mention); persist a uniqueness-guaranteed copy so the stored
        # AKN is valid and importable.
        xml, deduped = ensure_unique_eids(xml)

        document = parse_akn(xml)
        published_work = document.frbr_work_uri
        document, synth = normalise_native_akn(document)
        synth += minted
        if synth:
            yield ValidationIssued(issue={"synthesised_eids": synth})
        if document.frbr_work_uri != published_work:
            xml = canonicalise_identification(
                xml, document.frbr_work_uri, document.frbr_expression_uri
            )
        if deduped:
            yield ValidationIssued(issue={"deduped_eids": deduped})

        try:
            for issue in validate_akn(xml, provenance="native"):
                yield ValidationIssued(issue=issue)
        except Exception as exc:  # noqa: BLE001
            logger.warning("validator_failed", error=str(exc))
            yield Failed(stage="validator", error=f"{type(exc).__name__}: {exc}")
            return

        logger.info(
            "akn_native_ingested",
            jurisdiction=jurisdiction_code,
            frbr=document.frbr_work_uri,
            synthesised_eids=synth,
        )
        yield Complete(document=document, akn_xml=xml)
    except Exception as exc:  # noqa: BLE001
        yield Failed(stage="akn_native", error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "canonicalise_identification",
    "ingest",
    "mint_missing_eids",
    "normalise_native_akn",
]
