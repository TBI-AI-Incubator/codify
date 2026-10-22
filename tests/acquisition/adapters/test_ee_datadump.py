"""Estonian bulk XML archive adapter tests over a synthetic zip and index."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import codify.acquisition.adapters  # noqa: F401
from codify.acquisition import DocumentRef
from codify.acquisition.adapters.ee.datadump import (
    EeDatadumpAcquirer,
    EeDatadumpIndexMissing,
    EeDocNotInDump,
    build_ee_index,
)
from codify.jurisdictions import SourceAdapter
from codify.pipeline.formats.riigi_teataja import riigi_teataja_to_akn

_MEMBER = "123122022025.xml"
_SYNTHETIC_EE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-1" xmlns="Juurakt">
    <metaandmed>
        <valjaandja>Riigikogu</valjaandja>
        <dokumentLiik>seadus</dokumentLiik>
        <tekstiliik>terviktekst</tekstiliik>
        <vastuvoetud><aktikuupaev>2022-04-13</aktikuupaev></vastuvoetud>
        <globaalID>123122022025</globaalID>
    </metaandmed>
    <aktinimi><nimi><pealkiri>Synthetic Act</pealkiri></nimi></aktinimi>
    <sisu><paragrahv id="p1"><paragrahvNr>1</paragrahvNr></paragrahv></sisu>
</oigusakt>"""


def _adapter() -> SourceAdapter:
    return SourceAdapter(kind="bulk_xml_archive", name="riigi_teataja_archive")


def _dump(tmp_path: Path) -> Path:
    archive = tmp_path / "xml.2026.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(_MEMBER, _SYNTHETIC_EE_XML)
    payload = {
        "archive": str(archive),
        "entries": {
            "123122022025": {
                "member": _MEMBER,
                "title": "Synthetic Act",
                "doctype": "act",
                "year": 2022,
            }
        },
    }
    index = tmp_path / "index.json"
    index.write_text(json.dumps(payload))
    return index


def _ref() -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="ee",
        doctype="act",
        year=2022,
        number="123122022025",
        extra={"member": _MEMBER, "title": "Synthetic Act", "slug": "synthetic-act"},
    )


@pytest.mark.asyncio
async def test_ee_datadump_fetch_succeeds(tmp_path: Path) -> None:
    idx_path = _dump(tmp_path)
    acquirer = EeDatadumpAcquirer(_adapter(), index_path=idx_path)
    doc = await acquirer.fetch(_ref())

    assert doc.bodies[0].content == _SYNTHETIC_EE_XML
    assert doc.source_url == "https://www.riigiteataja.ee/akt/123122022025.xml"
    assert doc.frbr_work_uri == "/akn/ee/act/2022/synthetic-act"


@pytest.mark.asyncio
async def test_ee_datadump_fetch_takes_the_uri_from_the_member_not_a_stale_index(
    tmp_path: Path,
) -> None:
    # An index built before the full-title rule names an abbreviation slug and another title.
    archive = tmp_path / "xml.2026.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(_MEMBER, _SYNTHETIC_EE_XML)
    payload = {
        "archive": str(archive),
        "entries": {
            "123122022025": {
                "member": _MEMBER,
                "title": "Äriregistri seadus",
                "slug": "ars",
                "doctype": "act",
                "year": 2022,
            }
        },
    }
    idx_path = tmp_path / "index.json"
    idx_path.write_text(json.dumps(payload))

    acquirer = EeDatadumpAcquirer(_adapter(), index_path=idx_path)
    id_only_ref = DocumentRef(
        jurisdiction_code="ee",
        doctype="act",
        year=2022,
        number="123122022025",
    )
    doc = await acquirer.fetch(id_only_ref)
    assert doc.frbr_work_uri == "/akn/ee/act/2022/synthetic-act"


def test_build_ee_index(tmp_path: Path) -> None:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("test1.xml", _SYNTHETIC_EE_XML)

    payload = build_ee_index(archive, principal_only=False)
    assert payload["stats"]["files_scanned"] == 1
    assert "123122022025" in payload["entries"]
    assert payload["entries"]["123122022025"]["title"] == "Synthetic Act"


@pytest.mark.asyncio
async def test_missing_index_raises(tmp_path: Path) -> None:
    acquirer = EeDatadumpAcquirer(_adapter(), index_path=tmp_path / "missing.json")
    with pytest.raises(EeDatadumpIndexMissing):
        await acquirer.fetch(_ref())


@pytest.mark.asyncio
async def test_unknown_doc_raises(tmp_path: Path) -> None:
    idx_path = _dump(tmp_path)
    acquirer = EeDatadumpAcquirer(_adapter(), index_path=idx_path)
    bad_ref = DocumentRef(
        jurisdiction_code="ee",
        doctype="act",
        year=2022,
        number="999999",
    )
    with pytest.raises(EeDocNotInDump):
        await acquirer.fetch(bad_ref)


# --- which members the index keeps --------------------------------------------------
# Members in the publisher's shape with invented values; newer files add id attributes.

_VERSION_CHANGE = """
\t\t<versiooniMuutus>
\t\t\t<dokumentEtapp>avaldamine</dokumentEtapp>
\t\t\t<dokumentStaatus>esitatud avaldamiseks</dokumentStaatus>
\t\t\t<metaandmedVersioon>{n}</metaandmedVersioon>
\t\t\t<metaandmedVersioonKuupaev>2015-04-13</metaandmedVersioonKuupaev>
\t\t\t<metaandmedVersioonPohjustaja>Näidis Toimetaja</metaandmedVersioonPohjustaja>
\t\t</versiooniMuutus>"""


def _member(
    gid: str,
    title: str = "Näidistoetuse andmise tingimused ja kord",
    *,
    issuer: str = "Haridus- ja teadusminister",
    kind: str = "määrus",
    text: str = "algtekst-terviktekst",
    series: str = "RT I",
    ids: bool = True,
    changes: int = 0,
    end: str = "",
) -> bytes:
    """One archive member. `ids` writes the newer layout, `changes` lengthens the
    metadata ahead of the title, `end` closes the redaction's validity."""
    attr = ' id="0c1e5b9a-8d1f-4a52-9c3e-2f6b7d8e9a01"' if ids else ""
    lopp = f"<kehtivuseLopp>{end}</kehtivuseLopp>" if end else ""
    history = "".join(_VERSION_CHANGE.format(n=n) for n in range(changes))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt{attr} xmlns="Juurakt" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
\t<metaandmed>
\t\t<valjaandja>{issuer}</valjaandja>
\t\t<dokumentLiik>{kind}</dokumentLiik>
\t\t<tekstiliik>{text}</tekstiliik>
\t\t<dokumentEtapp>avaldamine</dokumentEtapp>
\t\t<dokumentStaatus>avaldatud</dokumentStaatus>
\t\t<vastuvoetud>
\t\t\t<aktikuupaev>2026-09-28</aktikuupaev>
\t\t\t<aktiNr>42</aktiNr>
\t\t</vastuvoetud>
\t\t<avaldamismarge><RTosa{attr}>{series}</RTosa>
\t\t\t<avaldamineKuupaev{attr}>2026-10-01</avaldamineKuupaev><RTaasta>2026</RTaasta>
\t\t\t<RTartikkel>1</RTartikkel><aktViide>{gid}</aktViide></avaldamismarge>
\t\t<kehtivus><kehtivuseAlgus>2026-10-04+03:00</kehtivuseAlgus>{lopp}</kehtivus>
\t\t<skeemiNimi>maarus_1_10.02.2010.xsd</skeemiNimi>
\t\t<globaalID>{gid}</globaalID>
\t\t<metaandmedVersioon>6</metaandmedVersioon>
\t\t<metaandmedVersioonKuupaev>2026-09-30</metaandmedVersioonKuupaev>
\t\t<metaandmedVersioonPohjustaja>Näidis Toimetaja</metaandmedVersioonPohjustaja>{history}
\t</metaandmed>
\t<aktinimi{attr}>
\t\t<nimi{attr}>
\t\t\t<pealkiri{attr}>{title}</pealkiri>
\t\t</nimi>
\t</aktinimi>
\t<sisu{attr}><paragrahv id="para1"><paragrahvNr{attr}>1</paragrahvNr>
\t\t<sisuTekst><tavatekst>Sätte tekst.</tavatekst></sisuTekst></paragrahv></sisu>
</oigusakt>""".encode()


def _indexed(tmp_path: Path, *members: bytes) -> dict[str, Any]:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, data in enumerate(members):
            zf.writestr(f"{i:012d}.xml", data)
    entries: dict[str, Any] = build_ee_index(archive)["entries"]
    return entries


@pytest.mark.parametrize("ids", [True, False], ids=["newer-layout", "older-layout"])
@pytest.mark.parametrize("text", ["algtekst", "terviktekst", "algtekst-terviktekst"])
@pytest.mark.parametrize(("kind", "issuer"), [("seadus", "Riigikogu"), ("määrus", "Siseminister")])
def test_index_keeps_a_state_act_whatever_its_text_type(
    tmp_path: Path, kind: str, issuer: str, text: str, ids: bool
) -> None:
    entries = _indexed(tmp_path, _member("101", kind=kind, issuer=issuer, text=text, ids=ids))
    assert {k: (v["title"], v["issuer"]) for k, v in entries.items()} == {
        "101": ("Näidistoetuse andmise tingimused ja kord", issuer)
    }


def test_index_files_an_older_layout_member_as_before(tmp_path: Path) -> None:
    entry = _indexed(tmp_path, _member("103", ids=False))["103"]
    assert {k: entry[k] for k in ("slug", "year", "date", "doctype", "expression_date")} == {
        "slug": "naidistoetuse-andmise-tingimused-ja-kord",
        "year": 2026,
        "date": "2026-09-28",
        "doctype": "maarrus",
        "expression_date": "2026-10-04",
    }


def test_index_reads_a_title_that_long_metadata_pushes_past_3000_bytes(tmp_path: Path) -> None:
    member = _member("102", ids=False, changes=12)
    assert member.index(b"<pealkiri>") > 3000
    assert list(_indexed(tmp_path, member)) == ["102"]


@pytest.mark.parametrize(
    ("issuer", "series"),
    [
        ("Näidise Linnavalitsus", "RT IV"),
        ("Näidise Vallavolikogu", "RT IV"),
        ("Näidise Linnavolikogu", "KO"),
        ("Näidise Vallavalitsus", "RTL"),
        ("Näidise Alevivolikogu", "KO"),
        ("Näidise Linnavalitsuse Ehitusamet", "RT IV"),
    ],
)
def test_index_leaves_out_every_act_a_municipal_body_issued(
    tmp_path: Path, issuer: str, series: str
) -> None:
    municipal = _member("201", issuer=issuer, series=series)
    state = _member("202", issuer="Vabariigi Valitsus")
    assert list(_indexed(tmp_path, municipal, state)) == ["202"]


def test_index_leaves_out_other_kinds_of_document(tmp_path: Path) -> None:
    decision = _member("301", kind="otsus", issuer="Vabariigi Valitsus", series="RT III")
    assert list(_indexed(tmp_path, decision, _member("302"))) == ["302"]


def test_index_leaves_out_a_member_with_no_title(tmp_path: Path) -> None:
    untitled = _member("401", title="")
    assert list(_indexed(tmp_path, untitled, _member("402"))) == ["402"]


def test_index_dates_an_entry_as_the_parser_dates_its_expression(tmp_path: Path) -> None:
    member = _member("501")
    entry = _indexed(tmp_path, member)["501"]
    _, meta = riigi_teataja_to_akn(member)
    assert meta["frbr_expression_uri"].endswith(f"@{entry['expression_date']}")
    assert (entry["expression_date"], entry["year"]) == ("2026-10-04", 2026)


# --- redactions kept only to mark a repeal ------------------------------------------

_BODY = re.compile(rb"\t<sisu\b.*?</sisu>", re.S)


def _repeal_stub(
    gid: str, *, repealer: str | None = None, note: str | None = None, original: str = ""
) -> bytes:
    """A redaction whose text is gone: no body, or only a "Kehtetu" note naming the act
    that repealed it. A repeal mark citing `repealer` follows the title."""
    body = (
        b"\t<sisu/>"
        if note is None
        else f"\t<sisu><sisuTekst><tavatekst>{note}</tavatekst></sisuTekst></sisu>".encode()
    )
    member = _BODY.sub(body, _member(gid, ids=False))
    if original:
        member = member.replace(
            b"\t\t\t<aktiNr>42</aktiNr>",
            f"\t\t\t<avaldamismarge><aktViide>{original}</aktViide></avaldamismarge>\n"
            "\t\t\t<aktiNr>42</aktiNr>".encode(),
        )
    if repealer:
        mark = (
            "\t<muutmismarge><tavatekst>Kehtetu</tavatekst><avaldamismarge>"
            f"<aktViide>{repealer}</aktViide></avaldamismarge></muutmismarge>\n"
        )
        member = member.replace(b"\t<sisu", mark.encode() + b"\t<sisu", 1)
    return member


def test_a_redaction_left_only_to_mark_a_repeal_is_kept_aside_with_its_repealer(
    tmp_path: Path,
) -> None:
    payload = _indexed_payload(
        tmp_path,
        _repeal_stub("601", repealer="900001"),
        _repeal_stub("602", note='Kehtetu - <a href="./900002">RTL 2004, 7, 94</a>'),
        _repeal_stub("603", repealer="900003", original="900003"),
        _member("604"),
    )
    assert list(payload["entries"]) == ["604"]
    assert {k: v["repealed_by"] for k, v in payload["repealed"].items()} == {
        "601": "900001",
        "602": "900002",
        # A temporary act's lapse note cites its own publication, so it names no repealer.
        "603": None,
    }
    assert payload["repealed"]["601"]["expression_date"] == "2026-10-04"
    assert payload["stats"]["repealed"] == 3


def test_a_short_act_that_repeals_another_stays_an_entry(tmp_path: Path) -> None:
    repealing = _BODY.sub(
        "\t<sisu><sisuTekst><tavatekst>Kehtetuks tunnistada määrus nr 80.</tavatekst>"
        "</sisuTekst></sisu>".encode(),
        _member("605"),
    )
    assert list(_indexed(tmp_path, repealing)) == ["605"]


@pytest.mark.parametrize(("day", "kept"), [((2026, 10, 20), True), ((2026, 10, 21), False)])
def test_as_of_keeps_a_redaction_not_ended_that_day(
    tmp_path: Path, day: tuple[int, int, int], kept: bool
) -> None:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("000000000606.xml", _member("606", end="2026-10-20"))
    assert ("606" in build_ee_index(archive, as_of=date(*day))["entries"]) is kept


def test_a_redaction_not_yet_in_force_is_kept(tmp_path: Path) -> None:
    # Published before it starts: a weekly refresh takes it then or never, and an index
    # for a past day holds it as an index built that day did.
    archive = tmp_path / "archive.zip"
    future = _member("608").replace(b"2026-10-04+03:00", b"2099-01-01")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("000000000608.xml", future)
    assert list(build_ee_index(archive)["entries"]) == ["608"]
    assert list(build_ee_index(archive, as_of=date(2026, 9, 11))["entries"]) == ["608"]


def test_a_repeal_mark_with_no_body_at_all_is_a_stub_but_a_cut_one_is_not(
    tmp_path: Path,
) -> None:
    whole = _repeal_stub("609", repealer="900009").replace(b"\t<sisu/>", b"")
    assert b"<sisu" not in whole
    long_body = _member("610").replace(b"</sisu>", b"<!--" + b"x" * 70_000 + b"--></sisu>")
    payload = _indexed_payload(tmp_path, whole, long_body)
    assert list(payload["repealed"]) == ["609"] and list(payload["entries"]) == ["610"]


def test_the_original_publication_is_read_from_the_adoption_block_alone(
    tmp_path: Path,
) -> None:
    # No reference in the adoption block; the act's own and the repealer's come later.
    stub = _repeal_stub("611", repealer="900011")
    entry = _indexed_payload(tmp_path, stub)["repealed"]["611"]
    assert (entry["original"], entry["repealed_by"]) == (None, "900011")


def _indexed_payload(tmp_path: Path, *members: bytes) -> dict[str, Any]:
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, data in enumerate(members):
            zf.writestr(f"{i:012d}.xml", data)
    return build_ee_index(archive)


def test_text_after_the_title_is_not_read_as_metadata(tmp_path: Path) -> None:
    # An abbreviation in the body is not the act's own, so it cannot make it the constitution.
    member = _member("607").replace(b"</sisu>", b"<lyhend>PS</lyhend></sisu>")
    assert _indexed(tmp_path, member)["607"]["doctype"] == "maarrus"
