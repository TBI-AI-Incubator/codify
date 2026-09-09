# ruff: noqa: E501  # AKN XML fixtures: line wraps would change tested whitespace
"""A placeholder unit carries the status its marker names, in a document the
strict schema still accepts; law is never touched and an editor's status wins."""

from __future__ import annotations

from lxml import etree

from codify.akn import AKN_NS
from codify.akn._schema import validate_akn
from codify.pipeline.enrich.placeholder_status import (
    mark_placeholder_status,
    mark_placeholder_status_if_changed,
)
from codify.quality.sentinels import PLACEHOLDER_PATTERN

_META = """<meta><identification source="#codify">
 <FRBRWork><FRBRthis value="/akn/xz/act/2007/1/!main"/><FRBRuri value="/akn/xz/act/2007/1"/>
  <FRBRdate date="2007-01-01" name="Generation"/><FRBRauthor href="#codify"/><FRBRcountry value="xz"/></FRBRWork>
 <FRBRExpression><FRBRthis value="/akn/xz/act/2007/1/eng@2007-01-01"/><FRBRuri value="/akn/xz/act/2007/1/eng@2007-01-01"/>
  <FRBRdate date="2007-01-01" name="Generation"/><FRBRauthor href="#codify"/><FRBRlanguage language="eng"/></FRBRExpression>
 <FRBRManifestation><FRBRthis value="/akn/xz/act/2007/1/eng@2007-01-01.xml"/><FRBRuri value="/akn/xz/act/2007/1/eng@2007-01-01.xml"/>
  <FRBRdate date="2007-01-01" name="Generation"/><FRBRauthor href="#codify"/></FRBRManifestation>
 </identification></meta>"""
MARKERS = (
    (PLACEHOLDER_PATTERN, "incomplete"),
    (r"^\s*Elucidation omitted\.?\s*$", "editorial"),
    (r"^\s*Repealed\.?\s*$", "removed"),
)


def _doc(body: str) -> str:
    return f'<akomaNtoso xmlns="{AKN_NS}"><act name="act">{_META}<body>{body}</body></act></akomaNtoso>'


def _status(xml: str, eid: str) -> str | None:
    root = etree.fromstring(xml.encode())
    el = root.xpath(f'//*[@eId="{eid}"]')[0]
    return el.get("status")


_BODY = (
    '<article eId="art_1"><num>1</num>'
    '<paragraph eId="art_1__p1"><content><p>Copper wire shall bear the duty set out below:</p></content></paragraph>'
    '<paragraph eId="art_1__p2"><content><p>Elucidation omitted.</p></content></paragraph>'
    '<paragraph eId="art_1__p3" status="removed"><content><p>Elucidation omitted.</p></content></paragraph>'
    "</article>"
    '<hcontainer name="includedSource" eId="body__includedsource_1"><content><p>[TIFF not transcribed: a.tif]</p></content></hcontainer>'
    '<article eId="art_2"><num>2</num><paragraph eId="art_2__p1"><content><blockList eId="art_2__p1__list_1">'
    '<item eId="art_2__p1__list_1__item_1"><num>(a)</num><p>A rule in a list.</p></item>'
    '<item eId="art_2__p1__list_1__item_2"><num>(b)</num><p>Elucidation omitted</p></item>'
    "</blockList></content></paragraph></article>"
    '<article eId="art_3"><num>3</num><indent eId="art_3__indent_1"><content><p>Elucidation omitted</p></content></indent></article>'
    '<section eId="sec_9"><num>9</num><content><p><b>[TIFF not transcribed: b.tif]</b></p></content></section>'
    '<article eId="art_4"><num>4</num><paragraph eId="art_4__p1"><intro><p>Elucidation omitted</p></intro>'
    '<point eId="art_4__p1__a"><content><p>A point of law.</p></content></point></paragraph></article>'
    '<article eId="art_5"><num>5</num><content><p>Repealed.</p></content></article>'
)


def test_each_marker_sets_its_status_and_law_is_untouched() -> None:
    out = mark_placeholder_status(_doc(_BODY), MARKERS)
    assert _status(out, "art_1__p1") is None
    assert _status(out, "art_1__p2") == "editorial"
    assert _status(out, "body__includedsource_1") == "incomplete"
    # A list item is not a row: its text belongs to the paragraph holding the list,
    # which is judged whole and stays law, exactly as the row flag would judge it.
    assert _status(out, "art_2__p1__list_1__item_2") is None
    assert _status(out, "art_2__p1") is None
    # A unit tag beyond the common three, with its own content, is a row and is marked;
    # so is a container with direct content, and an inline wrapper does not hide a marker.
    assert _status(out, "art_3__indent_1") == "editorial"
    assert _status(out, "sec_9") == "incomplete"
    # A paragraph whose own text is only its intro is judged on that intro, as the
    # mapper stores it; its point is a separate row and stays law.
    assert _status(out, "art_4__p1") == "editorial"
    assert _status(out, "art_4__p1__a") is None
    # An editor's status outranks the marker.
    assert _status(out, "art_1__p3") == "removed"
    # Every configured status word is emitted, not only the two above.
    assert _status(out, "art_5") == "removed"
    assert validate_akn(out) is None


def test_second_pass_changes_nothing() -> None:
    once = mark_placeholder_status(_doc(_BODY), MARKERS)
    assert mark_placeholder_status_if_changed(once, MARKERS) is None
    # Law-only documents are not rewritten either.
    law_only = _doc('<article eId="art_1"><content><p>A rule.</p></content></article>')
    assert mark_placeholder_status_if_changed(law_only, MARKERS) is None


async def test_ingest_runs_the_pass_after_the_metadata_pass() -> None:
    """The wiring, end to end through the enrich sequence: the model-bound tail is
    caught and skipped, so the sync passes alone must have set the status."""
    import pytest

    from codify.pipeline import stages

    class _NoModel:
        def __getattr__(self, name: str) -> object:
            raise RuntimeError("no model in this test")

    desc = stages.Descriptors(
        title="Test act",
        raw_date="2007-01-01",
        number="1",
        year="2007",
        language="eng",
        doctype="act",
    )
    out = await stages.run_enrich_passes(
        _doc(_BODY),
        llm=_NoModel(),
        jurisdiction_code="xz",
        desc=desc,
        skip_external_refs=True,  # type: ignore[arg-type]
    )
    assert _status(out, "body__includedsource_1") == "incomplete"
    assert _status(out, "art_1__p1") is None
    pytest.importorskip("cobalt")
