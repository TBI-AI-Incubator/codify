"""Riigi Teataja amendment notes, structured references and source anchors."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.akn._schema import AKN_NS, validate_akn
from codify.akn.io import parse_akn
from codify.pipeline.formats.riigi_teataja import _viide_targets, riigi_teataja_to_akn

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
    assert meta["amendment_notes"]["kept"] == 1
    assert meta["source_anchors"]["art_1__para_1"] == "para1lg1"
    assert meta["source_anchors"]["art_1"] == "para1"


def test_parsed_document_carries_effect_and_date() -> None:
    akn, _ = riigi_teataja_to_akn(_act(_loige(1, "A.", _note("750865")) + _loige(2, "B.")))
    doc = parse_akn(akn)
    (effect,) = doc.textual_mods
    assert effect.authority_uri == f"{_URL}750865"
    assert effect.source_akn_wid == "art_1__para_1"
    assert [e.event_date for e in doc.lifecycle_events] == ["2004-06-01"]
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
    assert meta["amendment_notes"]["repeal"] == 1


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
    assert meta["amendment_notes"]["undated"] == 1


def test_unusable_notes_are_skipped_and_counted() -> None:
    body = _loige(1, "A.", _note(None)) + _loige(2, "B.", _note("750865", joustumine=None))
    xml = _act(body).replace("<sisu>", f"<sisu>{_note('750865')}", 1)
    akn, meta = riigi_teataja_to_akn(xml)
    assert meta["amendment_notes"] == {
        "skipped_no_amending_act": 1,
        "skipped_no_unit": 1,
        "kept": 1,
        "amendment": 1,
        "undated": 1,
    }
    assert len(_mods(akn)) == 1


def test_act_level_note_is_counted_not_emitted() -> None:
    xml = _act(_loige(1, "A.") + _loige(2, "B.")).replace(
        "</oigusakt>", _note("750865") + "</oigusakt>"
    )
    akn, meta = riigi_teataja_to_akn(xml)
    assert meta["amendment_notes"] == {"skipped_act_level": 1}
    assert _mods(akn) == []
    assert etree.fromstring(akn.encode()).find(f".//{N}analysis") is None


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
        ("./915681", ["./915681"]),
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
