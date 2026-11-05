"""Guidance and administrative decisions through the text lane, on fictional Atlantis."""

from __future__ import annotations

import re
from typing import Any

import pytest
from lxml import etree

from codify.akn._schema import NS, main_body, validate_akn
from codify.akn.bluebell import akn_xml_to_bluebell
from codify.akn.document import Document
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.conclusions import find_displaced_attestation, lift_attestation
from codify.pipeline.enrich.enacting import emit_enacting_formula
from codify.pipeline.enrich.inline_markup import emit_inline_markup
from codify.pipeline.enrich.metadata import ExtractedMetadata
from codify.pipeline.enrich.scaffold import BodyBlock, BodyFillResponse
from codify.pipeline.events import Complete
from codify.pipeline.formats.pdf import ingest_text

FOI_ACT = "the Freedom of Information Act, 2001"

RESOLUTION = f"""RESOLUTION No. 4 of 2024
of the Harbour District Council

Having regard to {FOI_ACT},

Whereas requests for information held by the Council should be answered promptly,

HEREBY RESOLVES:

Section 1 - Time limit
Requests for information held by the Council shall be answered within ten working days.

Section 2 - Commencement
This Resolution takes effect on the day of its publication.
"""

DETERMINATION = f"""DETERMINATION No. 9 of 2024
of the Harbour Master

Having regard to {FOI_ACT},

The Harbour Master HEREBY DETERMINES:

Section 1
The request received on 2 May 2024 is refused, as {FOI_ACT} permits.
"""

GUIDANCE = f"""GUIDANCE NOTE No. 2 of 2024
Answering requests for information

PART 1 - INTRODUCTION

Section 1 - Purpose
This note explains how a public authority answers a request made under {FOI_ACT}.

PART 2 - TIME LIMITS

Section 2 - Twenty working days
A request of the kind section 1 describes is answered within twenty working days.
"""

# Each class's source text, and the title, date and number the model reads off it.
SOURCES = {
    "resolution": (RESOLUTION, "Resolution No. 4 of 2024", "2024-03-05", "4"),
    "determination": (DETERMINATION, "Determination No. 9 of 2024", "2024-05-20", "9"),
    "guidance": (GUIDANCE, "Guidance Note No. 2 of 2024", "2024-06-01", "2"),
}


class FakeLLM:
    """Answers metadata, body-fill and external citations from the source; offline otherwise."""

    _EID_RE = re.compile(r"eid=(\S+)")

    def __init__(self, source: str, title: str, date: str, number: str) -> None:
        self._source = source
        self._metadata = ExtractedMetadata(title=title, date=date, number=number, language="eng")

    def _body(self, eid: str) -> list[str]:
        """The source lines under the section the eId numbers, as body-fill returns them."""
        kind, _, num = eid.rsplit("__", 1)[-1].partition("_")
        block = re.search(rf"^Section {num}\b.*\n((?:.+\n?)+)", self._source, re.M)
        return block.group(1).strip().splitlines() if block and kind == "sec" else []

    async def chat_schema(self, prompt: str, schema: Any, system: Any = None, model: Any = None):
        if schema is ExtractedMetadata:
            return self._metadata
        if schema is BodyFillResponse:
            eids = self._EID_RE.findall(prompt)
            return BodyFillResponse(bodies=[BodyBlock(eid=e, lines=self._body(e)) for e in eids])
        raise RuntimeError("offline")

    async def chat_json(self, prompt: str, **_: Any) -> dict[str, Any]:
        if "external legislation" not in prompt:
            raise RuntimeError("offline")
        hit = FOI_ACT in prompt
        return {"refs": [{"text": FOI_ACT, "year": "2001", "number": "3", "kind": "act"}] * hit}

    def __getattr__(self, name: str) -> Any:
        async def _offline(*_a: object, **_k: object) -> Any:
            raise RuntimeError("offline")

        return _offline


async def ingest(doctype: str, locality: str | None = None) -> Complete:
    source, title, date, number = SOURCES[doctype]
    llm: Any = FakeLLM(source, title, date, number)
    events = [
        e
        async for e in ingest_text(
            source, "xa", llm=llm, name=doctype, doctype=doctype, locality=locality
        )
    ]
    done = [e for e in events if isinstance(e, Complete)]
    assert done, [type(e).__name__ for e in events]
    return done[0]


def _main(akn: str) -> etree._Element:
    return etree.fromstring(akn.encode())[0]


def _hrefs(el: etree._Element) -> list[str]:
    return [str(r.get("href")) for r in el.iter(f"{{{NS['akn']}}}ref")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("doctype", "locality", "work"),
    [
        ("resolution", "c01", "/akn/xa-c01/statement/resolution/2024/4"),
        ("determination", None, "/akn/xa/statement/determination/2024/9"),
    ],
)
async def test_a_decision_keeps_recitals_formula_and_operative_body_apart(
    doctype: str, locality: str | None, work: str
) -> None:
    done = await ingest(doctype, locality)
    validate_akn(done.akn_xml, strict=True)
    main = _main(done.akn_xml)
    assert etree.QName(main).localname == "statement"
    doc: Document = done.document
    date = SOURCES[doctype][2]
    assert (doc.frbr_work_uri, doc.frbr_expression_uri) == (work, f"{work}/eng@{date}")

    preamble = main.find("akn:preamble", NS)
    assert preamble is not None
    assert [etree.QName(c).localname for c in preamble] == ["recitals", "formula"]
    formula = preamble.find("akn:formula", NS)
    assert formula is not None and formula.get("name") == "enactingFormula"
    assert "HEREBY" in "".join(formula.itertext())
    assert [r.akn_eid for r in doc.recitals][0] == "recitals__rec_1"
    cited = doc.recitals[0].references
    assert [getattr(r, "target_uri", None) for r in cited] == ["/akn/xa/act/2001/3"]

    body = main.find("akn:mainBody", NS)
    assert body is not None
    assert "HEREBY" not in "".join(body.itertext())
    assert [(s.akn_eid, s.akn_wid) for s in doc.body][0] == ("sec_1", "sec_1")
    assert "Having regard to" in akn_xml_to_bluebell(done.akn_xml)


@pytest.mark.asyncio
async def test_guidance_keeps_its_sections_and_cites_both_ways() -> None:
    done = await ingest("guidance")
    validate_akn(done.akn_xml, strict=True)
    main = _main(done.akn_xml)
    assert etree.QName(main).localname == "doc"
    assert done.document.frbr_work_uri == "/akn/xa/doc/guidance/2024/2"
    assert main.find("akn:preamble", NS) is None
    body = main.find("akn:mainBody", NS)
    assert body is not None
    eids = [e.get("eId") for e in body.iter() if e.get("eId") and e.tag.endswith("section")]
    assert eids == ["part_1__sec_1", "part_2__sec_2"]
    assert _hrefs(body) == ["/akn/xa/act/2001/3", "#part_1__sec_1"]


@pytest.mark.asyncio
async def test_a_reingest_mints_the_same_identity_and_ids() -> None:
    first, second = await ingest("resolution", "c01"), await ingest("resolution", "c01")

    def ids(done: Complete) -> list[str]:
        return [e.get("eId") for e in _main(done.akn_xml).iter() if e.get("eId")]

    assert first.document.frbr_work_uri == second.document.frbr_work_uri
    assert ids(first) == ids(second)


def _resolution(bluebell: str) -> str:
    return parse_to_akn(bluebell, "xa", doctype="resolution", date="2024-03-05", number="4")


def test_a_formula_opening_the_preamble_leaves_what_follows_it_alone_on_every_run() -> None:
    akn = _resolution(
        "PREAMBLE\n\n  HEREBY RESOLVES:\n\n  Adopted in open session.\n\n"
        "BODY\n\nSECTION 1\n\n  A.\n"
    )
    once = emit_enacting_formula(akn, "xa", "resolution", None)
    preamble = _main(once).find("akn:preamble", NS)
    assert preamble is not None
    assert [etree.QName(c).localname for c in preamble] == ["formula", "p"]
    assert emit_enacting_formula(once, "xa", "resolution", None) == once


def test_a_decisions_attestation_lifts_out_of_its_operative_body() -> None:
    akn = _resolution(
        "BODY\n\nSECTION 1\n\n  It takes effect.\n\n  Done at the Hall on 5 March 2024.\n"
    )
    root = etree.fromstring(akn.encode())
    container, start = find_displaced_attestation(root, ["Done at"])
    assert container is not None and start is not None
    assert lift_attestation(root, container, start) == 1
    validate_akn(root, strict=True)
    assert [etree.QName(c).localname for c in root[0]][-2:] == ["mainBody", "conclusions"]


def test_the_main_body_is_the_body_before_any_attachment() -> None:
    statement = etree.fromstring(_resolution("BODY\n\nSECTION 1\n\n  A.\n").encode())
    assert etree.QName(main_body(statement)).localname == "mainBody"
    act = etree.fromstring(
        parse_to_akn(
            "BODY\n\nSECTION 1\n\n  A.\n\nSCHEDULE\n\n  SECTION 1\n\n    B.\n", "xa"
        ).encode()
    )
    body = main_body(act)
    assert body is not None and body.getparent() is act[0]


def _q(tag: str) -> str:
    return f"{{{NS['akn']}}}{tag}"


@pytest.mark.asyncio
async def test_an_attached_act_is_not_the_decisions_body() -> None:
    root = etree.fromstring(
        _resolution(
            "PREAMBLE\n\n  Having regard to Assembly Act No. 3,\n\nBODY\n\nSECTION 1\n\n  A.\n"
        ).encode()
    )
    attachment = etree.SubElement(etree.SubElement(root[0], _q("attachments")), _q("attachment"))
    attached = etree.SubElement(etree.SubElement(attachment, _q("act")), _q("body"))
    etree.SubElement(attached, _q("p")).text = "Attached."

    body = main_body(root)
    assert body is not None and body.getparent() is root[0]
    marked = await emit_inline_markup(etree.tostring(root, encoding="unicode"), "xa", "resolution")
    preamble = _main(marked).find("akn:preamble", NS)
    assert preamble is not None and _hrefs(preamble) == ["/akn/xa/act/0001/3"]


def test_a_table_in_the_preamble_keeps_its_place_among_the_recitals() -> None:
    akn = _resolution(
        "PREAMBLE\n\n  Having regard to the Act,\n\n  TABLE\n    TR\n      TC\n        Fee\n\n"
        "  Whereas fees apply,\n\n  HEREBY RESOLVES:\n\nBODY\n\nSECTION 1\n\n  A.\n"
    )
    out = emit_enacting_formula(akn, "xa", "resolution", None)
    validate_akn(out, strict=True)
    recitals = _main(out).find("akn:preamble/akn:recitals", NS)
    assert recitals is not None
    assert [etree.QName(r[0]).localname for r in recitals] == ["p", "table", "p"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule", "target"),
    [
        (
            "  SECTION 1\n\n    Form A.\n\n  SECTION 2\n\n    As section 1 requires.\n",
            "#att_1__sec_1",
        ),
        ("  SECTION 2\n\n    As section 1 requires.\n", "#sec_1"),
    ],
    ids=["its own section", "the document's, when it has none"],
)
async def test_a_reference_inside_a_schedule_names_its_nearest_section(
    schedule: str, target: str
) -> None:
    akn = parse_to_akn(
        f"BODY\n\nSECTION 1\n\n  Outer.\n\nSCHEDULE - Forms\n\n{schedule}",
        "xa",
        doctype="guidance",
        date="2024-06-01",
        number="2",
    )
    marked = await emit_inline_markup(akn, "xa", "guidance")
    assert _hrefs(_main(marked)) == [target]
