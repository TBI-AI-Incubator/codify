"""Riigi Teataja amendment notes, structured references and source anchors."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import structlog
from lxml import etree

from codify.akn._schema import AKN_NS, validate_akn
from codify.akn.io import parse_akn
from codify.pipeline.formats.riigi_teataja import _viide_targets, riigi_teataja_to_akn
from codify.storage.mappers import document_to_rows

N = f"{{{AKN_NS}}}"
_URL = "https://www.riigiteataja.ee/akt/"


def _note(
    akt: str | None,
    *,
    joustumine: str | None = "2004-06-01",
    lead: str = "",
    tail: str = "",
) -> str:
    ref = f"<aktViide>{akt}</aktViide>" if akt else ""
    force = f"<joustumine>{joustumine}</joustumine>" if joustumine else ""
    return (
        "<muutmismarge>"
        + (f"<tavatekst>{lead}</tavatekst>" if lead else "")
        + f"<avaldamismarge><RTosa>RT I</RTosa><RTaasta>2004</RTaasta><RTnr>41</RTnr>"
        f"<RTartikkel>278</RTartikkel>{ref}</avaldamismarge>{force}"
        + (f"<tavatekst>{tail}</tavatekst>" if tail else "")
        + "</muutmismarge>"
    )


def _act(body: str, preamble: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="x" xmlns="Juurakt">
<metaandmed><valjaandja>Riigikogu</valjaandja><dokumentLiik>seadus</dokumentLiik>
<vastuvoetud><aktikuupaev>2004-01-01</aktikuupaev><aktiNr>1</aktiNr></vastuvoetud>
<avaldamismarge><RTosa>RT I</RTosa><avaldamineKuupaev>2004-02-01</avaldamineKuupaev>
<RTartikkel>1</RTartikkel><aktViide>101022004001</aktViide></avaldamismarge>
<kehtivus><kehtivuseAlgus>2004-03-01</kehtivuseAlgus></kehtivus>
<globaalID>101022004001</globaalID></metaandmed>
<aktinimi><nimi><pealkiri>Test seadus</pealkiri></nimi></aktinimi>
<sisu>{preamble}<paragrahv id="para1"><paragrahvNr>1</paragrahvNr>
<paragrahvPealkiri>Üldsätted</paragrahvPealkiri>{body}</paragrahv></sisu></oigusakt>"""


def _loige(nr: int, text: str, notes: str = "") -> str:
    return (
        f'<loige id="para1lg{nr}"><loigeNr>{nr}</loigeNr>'
        f"<sisuTekst><tavatekst>{text}</tavatekst></sisuTekst>{notes}</loige>"
    )


def _counts(xml: str) -> dict[str, int]:
    with structlog.testing.capture_logs() as logs:
        riigi_teataja_to_akn(xml)
    events = [e for e in logs if e["event"] == "riigi_teataja_notes"]
    return {k: v for e in events for k, v in e.items() if k not in ("event", "log_level", "work")}


def _mods(akn: str) -> list[etree._Element]:
    return list(etree.fromstring(akn.encode()).iter(f"{N}textualMod"))


def test_note_becomes_textual_mod_on_its_unit() -> None:
    xml = _act(_loige(1, "Tekst.", _note("750865")) + _loige(2, "Teine."))
    akn, meta = riigi_teataja_to_akn(xml)
    validate_akn(akn)
    (mod,) = _mods(akn)
    assert mod.get("type") == "substitution"
    assert mod.find(f"{N}source").get("href") == f"{_URL}750865"
    assert mod.find(f"{N}destination").get("href").endswith("/main#art_1__para_1")
    assert meta["source_anchors"]["art_1__para_1"] == "para1lg1"
    assert meta["source_anchors"]["art_1"] == "para1"


def test_parsed_document_carries_effect_and_date() -> None:
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.", _note("750865")) + _loige(2, "B.")))
    doc = parse_akn(akn)
    (effect,) = doc.textual_mods
    assert effect.authority_uri == f"{_URL}750865"
    assert effect.source_akn_wid == "art_1__para_1"
    assert [e.event_date for e in doc.lifecycle_events] == ["2004-06-01"]
    assert effect.in_force_date == date(2004, 6, 1)
    force = etree.fromstring(akn.encode()).find(f".//{N}force")
    assert force is not None
    group = etree.fromstring(akn.encode()).find(
        f".//{N}temporalGroup[@eId='{force.get('period')[1:]}']"
    )
    assert group is not None


def test_repeal_note_is_a_repeal() -> None:
    notes = _note("118022014002", lead="Kehtetu - ")
    akn, meta = riigi_teataja_to_akn(_act(_loige(1, "Kehtetu.", notes) + _loige(2, "B.")))
    assert _mods(akn)[0].get("type") == "repeal"


@pytest.mark.parametrize(
    "tail",
    [
        ", osaliselt 01.01.2007",
        "(jõustumine muudetud - RT I, 01.01.2006, 3)",
        "jõustub päeval, mis on kindlaks määratud",
    ],
)
def test_qualified_entry_into_force_is_left_undated(tail: str) -> None:
    akn, meta = riigi_teataja_to_akn(
        _act(_loige(1, "A.", _note("750865", tail=tail)) + _loige(2, "B."))
    )
    (mod,) = _mods(akn)
    assert mod.find(f"{N}force") is None


def test_unusable_notes_are_skipped_and_counted() -> None:
    body = _loige(1, "A.", _note(None)) + _loige(2, "B.", _note("750865", joustumine=None))
    xml = _act(body).replace("<sisu>", f"<sisu>{_note('750865')}", 1)
    akn, meta = riigi_teataja_to_akn(xml)
    assert _counts(xml) == {"skipped_no_amending_act": 1, "skipped_no_unit": 1, "kept": 1}
    assert len(_mods(akn)) == 1


def test_act_level_note_is_a_lifecycle_event_not_an_effect() -> None:
    xml = _act(_loige(1, "A.") + _loige(2, "B.")).replace(
        "</oigusakt>", _note("750865") + "</oigusakt>"
    )
    akn, meta = riigi_teataja_to_akn(xml)
    validate_akn(akn)
    assert _counts(xml) == {"whole_act_event": 1}
    doc = parse_akn(akn)
    assert doc.textual_mods == []
    (event,) = doc.lifecycle_events
    assert (event.event_type, event.event_date) == ("amendment", "2004-06-01")
    assert event.source_uri == "#rt_750865"


def test_undated_act_level_note_is_skipped() -> None:
    xml = _act(_loige(1, "A.") + _loige(2, "B.")).replace(
        "</oigusakt>", _note("750865", tail=", osaliselt 01.01.2007") + "</oigusakt>"
    )
    akn, meta = riigi_teataja_to_akn(xml)
    assert _counts(xml) == {"skipped_whole_act_undated": 1}
    assert parse_akn(akn).lifecycle_events == []


def test_repeated_source_id_is_not_a_unit() -> None:
    twin = _loige(1, "A.", _note("750865")).replace("para1lg1", "dup")
    other = _loige(2, "B.", _note("1")).replace("para1lg2", "dup")
    xml = _act(twin + other)
    akn, _ = riigi_teataja_to_akn(xml)
    assert _counts(xml) == {"skipped_repeated_unit_id": 2}
    assert _mods(akn) == []


@pytest.mark.parametrize(
    "lead",
    [
        "Välja jäetud - ",
        "– Riigikohtu üldkogu otsus tunnistab seaduse § 9 põhiseaduse vastaseks ja kehtetuks.",
    ],
)
def test_omissions_and_court_annulments_are_repeals(lead: str) -> None:
    akn, _ = riigi_teataja_to_akn(
        _act(_loige(1, "A.", _note("750865", lead=lead)) + _loige(2, "B."))
    )
    assert _mods(akn)[0].get("type") == "repeal"


def _body(xml: str) -> tuple[list[str], str]:
    body = etree.fromstring(riigi_teataja_to_akn(xml)[0].encode())[0].find(f"{N}body")
    return [e.get("eId") for e in body.iter() if e.get("eId")], "".join(body.itertext())


def test_notes_leave_body_text_and_eids_unchanged() -> None:
    plain = _act(_loige(1, "A.") + _loige(2, "B."))
    noted = _act(_loige(1, "A.", _note("750865")) + _loige(2, "B.", _note("1", lead="Kehtetu - ")))
    assert _body(plain) == _body(noted)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("./dyn=1&id=13198077", [f"{_URL}13198077"]),
        ("./dyn=1&id=105;106;107", [f"{_URL}105", f"{_URL}106", f"{_URL}107"]),
        ("./dyn=1&id=105!pr10lg2", [f"{_URL}105#pr10lg2"]),
        (
            "./dyn=1&id=105!pr10lg2p3;106!pr5b1lg1",
            [f"{_URL}105#pr10lg2p3", f"{_URL}106#pr5b1lg1"],
        ),
        ("./../vaheleht.html", []),
        ("./915681", [f"{_URL}915681"]),
    ],
)
def test_viide_targets(raw: str, expected: list[str]) -> None:
    assert _viide_targets(raw) == expected


def _viide(uri: str, text: str = "seaduse") -> str:
    return (
        f"<viide><kuvatavTekst>{text}</kuvatavTekst>"
        f"<viideURID><viideURI>{uri}</viideURI></viideURID></viide>"
    )


def test_id_list_emits_each_target_and_placeholder_keeps_its_text() -> None:
    body = (
        '<loige id="para1lg1"><loigeNr>1</loigeNr><sisuTekst><tavatekst>Vt </tavatekst>'
        + _viide("./dyn=1&amp;id=105;106")
        + "<tavatekst> ja </tavatekst>"
        + _viide("./../vaheleht.html", "määrus")
        + '</sisuTekst></loige><loige id="para1lg2"><loigeNr>2</loigeNr>'
        "<sisuTekst><tavatekst>B.</tavatekst></sisuTekst></loige>"
    )
    akn, _ = riigi_teataja_to_akn(_act(body))
    validate_akn(akn)
    root = etree.fromstring(akn.encode())
    assert [r.get("href") for r in root.iter(f"{N}ref")] == [f"{_URL}105", f"{_URL}106"]
    assert "Vt seaduse ja määrus" in "".join(root.itertext()).replace("\n", " ")


def test_preamble_references_are_emitted() -> None:
    link = _viide("./dyn=1&amp;id=105!pr16")
    preamble = f"<preambul><tavatekst>Alusel </tavatekst>{link}</preambul>"
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.") + _loige(2, "B."), preamble))
    preamble_el = etree.fromstring(akn.encode())[0].find(f"{N}preamble")
    assert [r.get("href") for r in preamble_el.iter(f"{N}ref")] == [f"{_URL}105#pr16"]


def test_preamble_reference_becomes_a_row() -> None:
    link = _viide("./dyn=1&amp;id=105!pr16")
    preamble = f"<preambul><tavatekst>Alusel </tavatekst>{link}</preambul>"
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.") + _loige(2, "B."), preamble))
    *_, refs, _effects, _events = document_to_rows(parse_akn(akn), uuid.uuid4())
    assert [r.target_uri for r in refs] == [f"{_URL}105#pr16"]


def test_formula_line_with_two_references_keeps_text_and_both() -> None:
    first = _viide("./dyn=1&amp;id=105!pr16", "esimese seaduse")
    second = _viide("./dyn=1&amp;id=106", "teise seaduse")
    preamble = (
        f"<preambul><tavatekst>Alusel </tavatekst>{first}"
        f"<tavatekst> ja </tavatekst>{second}<tavatekst> määrus.</tavatekst></preambul>"
    )
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.") + _loige(2, "B."), preamble))
    prov = document_to_rows(parse_akn(akn), uuid.uuid4())[2]
    (row,) = [p for p in prov if p.akn_eid.startswith("preamble")]
    assert row.text == "Alusel esimese seaduse ja teise seaduse määrus."
    refs = document_to_rows(parse_akn(akn), uuid.uuid4())[3]
    assert sorted(r.target_uri for r in refs) == [f"{_URL}105#pr16", f"{_URL}106"]


def test_note_on_a_unit_without_an_id_is_not_the_enclosing_unit() -> None:
    bare = _loige(1, "A.", _note("750865")).replace(' id="para1lg1"', "")
    xml = _act(bare + _loige(2, "B."))
    akn, _ = riigi_teataja_to_akn(xml)
    assert _mods(akn) == []
    assert _counts(xml) == {"skipped_no_unit": 1}


def test_note_on_a_collapsed_single_paragraph_belongs_to_its_article() -> None:
    only = (
        '<loige id="para1lg1"><sisuTekst><tavatekst>A.</tavatekst></sisuTekst>'
        f"{_note('750865')}</loige>"
    )
    akn, _ = riigi_teataja_to_akn(_act(only))
    assert _mods(akn)[0].find(f"{N}destination").get("href").endswith("#art_1")


def test_external_link_with_an_id_parameter_keeps_its_uri() -> None:
    assert _viide_targets("https://example.test/doc?id=law-7") == [
        "https://example.test/doc?id=law-7"
    ]


def test_every_uri_of_a_link_is_a_target() -> None:
    link = (
        "<viide><kuvatavTekst>seadus</kuvatavTekst><viideURID>"
        "<viideURI>./dyn=1&amp;id=105</viideURI><viideURI>./dyn=1&amp;id=106</viideURI>"
        "</viideURID></viide>"
    )
    body = f'<loige id="para1lg1"><loigeNr>1</loigeNr><sisuTekst>{link}</sisuTekst></loige>'
    akn, _ = riigi_teataja_to_akn(_act(body + _loige(2, "B.")))
    hrefs = [r.get("href") for r in etree.fromstring(akn.encode()).iter(f"{N}ref")]
    assert hrefs == [f"{_URL}105", f"{_URL}106"]


def test_plain_entry_into_force_label_keeps_the_date() -> None:
    akn, meta = riigi_teataja_to_akn(
        _act(_loige(1, "A.", _note("750865", tail="- jõust.")) + _loige(2, "B."))
    )
    assert parse_akn(akn).textual_mods[0].in_force_date == date(2004, 6, 1)


_X = "<sisuTekst><tavatekst>x</tavatekst></sisuTekst>"


def _point(note: str = "") -> str:
    return f'<alampunkt id="para1lg1p1"><alampunktNr>1</alampunktNr>{_X}{note}</alampunkt>'


def test_unnumbered_paragraph_with_points_keeps_its_own_note() -> None:
    only = f'<loige id="para1lg1">{_X}{_point()}{_note("750865")}</loige>'
    akn, _ = riigi_teataja_to_akn(_act(only))
    assert _mods(akn)[0].find(f"{N}destination").get("href").endswith("#art_1__para_1")


def test_note_on_a_point_targets_the_point() -> None:
    body = f'<loige id="para1lg1"><loigeNr>1</loigeNr>{_X}{_point(_note("750865"))}</loige>'
    akn, _ = riigi_teataja_to_akn(_act(body + _loige(2, "B.")))
    assert _mods(akn)[0].find(f"{N}destination").get("href").endswith("#art_1__para_1__point_1")


def test_lifted_footnote_inside_a_formula_line_is_not_a_row() -> None:
    link = _viide("./dyn=1&amp;id=105!pr16")
    preamble = f"<preambul><tavatekst>Alusel </tavatekst>{link}</preambul>"
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.") + _loige(2, "B."), preamble))
    note = (
        '<authorialNote placement="bottom" eId="fn_1"><p eId="fn_1__p_1">'
        '<ref href="https://example.test/x">x</ref></p></authorialNote>'
    )
    noted = akn.replace("</p>\n      </formula>", f"{note}</p>\n      </formula>", 1)
    assert noted != akn
    rows = document_to_rows(parse_akn(noted), uuid.uuid4())
    assert [p.akn_eid for p in rows[2] if p.akn_type == "recital"] == ["preamble__formula__p_1"]
    assert [r.target_uri for r in rows[3]] == [f"{_URL}105#pr16"]
