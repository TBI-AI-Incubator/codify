"""A DOCTYPE is refused whatever format path the document arrives by.

The protection used to be two accidents stacked. `akn_native` routed through
`ensure_unique_eids`, which rejects a DOCTYPE, so that path was covered; the EU
directive path did not, and reached bare `etree.fromstring` calls in the enrich
passes. Nothing bad followed today only because the pinned libxml2 refuses a
declared external entity on its own, which is a dependency's behaviour rather
than this project's, and the version that changes it will not announce itself.

So the rule lives in `parse_xml` now, which is the shared hardened entry point,
and the format paths inherit it instead of each remembering.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from lxml import etree

from codify.akn._schema import parse_xml
from codify.pipeline import ingest_document
from codify.pipeline.enrich.references import emit_references
from codify.pipeline.enrich.validator import validate_akn
from codify.pipeline.events import Complete, Failed
from codify.pipeline.formats.eu_directive import parse_akn4eu

FIXTURES_DIR = Path(__file__).resolve().parents[4] / "data" / "fixtures"

PLAIN = (
    '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
    "<act><meta/><body/></act></akomaNtoso>"
)


def _with_doctype(doctype: str) -> str:
    return f'<?xml version="1.0"?>\n{doctype}\n{PLAIN}'


DOCTYPES = [
    pytest.param('<!DOCTYPE akomaNtoso [<!ENTITY x "harmless">]>', id="internal-entity"),
    pytest.param('<!DOCTYPE akomaNtoso SYSTEM "https://example.test/akn.dtd">', id="external-dtd"),
    pytest.param(
        '<!DOCTYPE akomaNtoso [<!ENTITY x SYSTEM "file:///etc/passwd">]>', id="external-entity"
    ),
    pytest.param("<!DOCTYPE akomaNtoso>", id="bare-doctype"),
]


@pytest.mark.parametrize("doctype", DOCTYPES)
def test_the_shared_parser_refuses_a_doctype(doctype: str) -> None:
    with pytest.raises(etree.XMLSyntaxError, match="DOCTYPE"):
        parse_xml(_with_doctype(doctype))


def test_a_document_without_one_still_parses() -> None:
    """The cost of the rule has to be nothing, or it will be reverted."""
    assert parse_xml(PLAIN).tag.endswith("akomaNtoso")


@pytest.mark.parametrize("doctype", DOCTYPES)
def test_the_eu_directive_parser_refuses_it_too(doctype: str) -> None:
    """The asymmetry this closes: the native path rejected a DOCTYPE and this
    one did not, so the guarantee was a property of one format."""
    with pytest.raises(etree.XMLSyntaxError, match="DOCTYPE"):
        parse_akn4eu(_with_doctype(doctype))


def test_a_real_directive_with_a_doctype_fails_the_whole_ingest(tmp_path: Path) -> None:
    """Everything above calls a parser directly. This drives the actual upload
    path, so a regression in dispatch or `_detect_format` cannot slip through
    while the parser tests stay green.

    A real fixture with a DOCTYPE spliced in, so the only difference from a
    document that ingests cleanly is the declaration itself."""
    source = FIXTURES_DIR / "eu" / "2016-943" / "eng.xml"
    if not source.is_file():
        pytest.skip(f"fixture absent: {source}")

    raw = source.read_text(encoding="utf-8")
    assert "<!DOCTYPE" not in raw[:2000], "premise: the clean fixture has none"
    head, sep, tail = raw.partition("?>")  # a DOCTYPE is legal only after the declaration
    assert sep, "expected an XML declaration"
    poisoned = tmp_path / "eng.xml"
    poisoned.write_text(
        f'{head}{sep}\n<!DOCTYPE ACT [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>{tail}',
        encoding="utf-8",
    )

    async def go() -> list:
        return [e async for e in ingest_document(poisoned, "eu")]

    events = asyncio.run(go())
    assert events, "no events yielded"
    assert not any(isinstance(e, Complete) for e in events), "a poisoned document completed"
    assert isinstance(events[-1], Failed), f"expected Failed, got {events[-1]!r}"
    assert "root:" not in repr(events), "file contents leaked into the event stream"


def test_the_same_fixture_without_a_doctype_still_completes() -> None:
    """The negative control: the guard rejects the declaration, not the file."""
    source = FIXTURES_DIR / "eu" / "2016-943" / "eng.xml"
    if not source.is_file():
        pytest.skip(f"fixture absent: {source}")

    async def go() -> list:
        return [e async for e in ingest_document(source, "eu")]

    events = asyncio.run(go())
    assert isinstance(events[-1], Complete), f"expected Complete, got {events[-1]!r}"


@pytest.mark.parametrize("doctype", DOCTYPES)
def test_the_enrich_passes_refuse_it(doctype: str) -> None:
    """These ran on document-derived XML with a bare parser, which is where a
    libxml2 that resolved entities would have been reachable.

    `postprocess_hcontainers` is deliberately not here: it returns before
    parsing when the jurisdiction declares no post-processed hcontainers, so
    there is no parse to harden on that path."""
    xml = _with_doctype(doctype)
    with pytest.raises(etree.XMLSyntaxError, match="DOCTYPE"):
        emit_references(xml, "eu")
    with pytest.raises(etree.XMLSyntaxError, match="DOCTYPE"):
        validate_akn(xml)


def test_a_real_directive_still_ingests() -> None:
    """The guard must not tax the corpus. This is the fixture the EU path's own
    integration test uses, parsed through the same hardened entry."""
    source = FIXTURES_DIR / "eu" / "2016-943" / "eng.xml"
    if not source.is_file():
        pytest.skip(f"fixture absent: {source}")
    raw = source.read_text(encoding="utf-8")
    assert "<!DOCTYPE" not in raw[:2000], "premise: real EU sources carry no DOCTYPE"
    assert parse_xml(raw) is not None
