"""One Riigi Teataja source file gets one work URI on every path that reads it."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from codify.acquisition import DocumentRef
from codify.acquisition.adapters.ee.datadump import EeDatadumpAcquirer, build_ee_index
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter
from codify.pipeline.formats.riigi_teataja import riigi_teataja_to_akn

# A title that names the constitution in passing; the act is a government regulation.
_NAMES_THE_CONSTITUTION = (
    "Riigikaitseliste ülesannete, sisejulgeoleku ja põhiseadusliku korra või elutähtsa "
    "teenuse toimepidevuse tagamisega vahetult seotud ameti- ja töökohad riigi "
    "ametiasutuse, valitsusasutuse hallatava riigiasutuse ja riigikaitseliste ülesannetega "
    "juriidilise isiku koosseisus"
)


def _source(
    *,
    title: str,
    kind: str = "seadus",
    abbreviation: str = "",
    issuer: str = "Riigikogu",
    adopted: str = "2005-04-20",
    global_id: str = "100",
) -> bytes:
    lyhend = f"<lyhend>{abbreviation}</lyhend>" if abbreviation else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt xmlns="Juurakt">
  <metaandmed>
    <valjaandja>{issuer}</valjaandja>
    <dokumentLiik>{kind}</dokumentLiik>
    <tekstiliik>terviktekst</tekstiliik>
    {lyhend}
    <vastuvoetud><aktikuupaev>{adopted}</aktikuupaev></vastuvoetud>
    <avaldamismarge><RTosa>RT I</RTosa><avaldamineKuupaev>2024-01-05+02:00</avaldamineKuupaev>
      <RTartikkel>3</RTartikkel></avaldamismarge>
    <kehtivus><kehtivuseAlgus>2024-02-01</kehtivuseAlgus></kehtivus>
    <globaalID>{global_id}</globaalID>
  </metaandmed>
  <aktinimi><nimi><pealkiri>{title}</pealkiri></nimi></aktinimi>
  <sisu><paragrahv id="p1"><paragrahvNr>1</paragrahvNr>
    <sisuTekst><tavatekst>Tekst.</tavatekst></sisuTekst></paragrahv></sisu>
</oigusakt>""".encode()


def _acquirer(tmp_path: Path, raw: bytes) -> tuple[EeDatadumpAcquirer, DocumentRef, dict]:
    """The archive adapter over an index of one member, and the ref a caller builds."""
    archive = tmp_path / "laws.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("member.xml", raw)
    payload = build_ee_index(archive)
    payload["archive"] = str(archive)
    index = tmp_path / "index.json"
    index.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    [(doc_id, entry)] = payload["entries"].items()
    acquirer = EeDatadumpAcquirer(
        SourceAdapter(kind="bulk_xml_archive", name="riigi_teataja_archive"), index_path=index
    )
    ref = DocumentRef(
        jurisdiction_code="ee",
        doctype=entry["doctype"],
        year=entry["year"],
        number=doc_id,
        extra={"member": entry["member"], "title": entry["title"]},
    )
    return acquirer, ref, entry


async def _paths(tmp_path: Path, raw: bytes) -> tuple[str, str, dict]:
    """The transformer's work URI and the archive adapter's, for one member."""
    acquirer, ref, entry = _acquirer(tmp_path, raw)
    fetched = (await acquirer.fetch(ref)).frbr_work_uri
    transformed = riigi_teataja_to_akn(raw)[1]["frbr_work_uri"]
    return transformed, fetched, entry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("abbreviation", "kind", "expected"),
    [
        ("TsMS", "seadus", "/akn/ee/act/2005/tsiviilkohtumenetluse-seadustik"),
        ("", "seadus", "/akn/ee/act/2005/tsiviilkohtumenetluse-seadustik"),
        ("TsMS", "määrus", "/akn/ee/act/maarrus/2005/tsiviilkohtumenetluse-seadustik"),
    ],
)
async def test_transformer_and_adapter_give_one_work_uri_for_one_source_file(
    tmp_path: Path, abbreviation: str, kind: str, expected: str
) -> None:
    # A redaction with an abbreviation and one without land on the same work.
    raw = _source(title="Tsiviilkohtumenetluse seadustik", kind=kind, abbreviation=abbreviation)
    transformed, fetched, entry = await _paths(tmp_path, raw)
    assert (transformed, fetched) == (expected, expected)
    # What a consumer keying on the index entry builds: its class, year and slug.
    indexed = build_frbr_work_uri("ee", entry["doctype"], entry["year"], entry["slug"])
    assert (indexed, entry["year"]) == (expected, 2005)


@pytest.mark.asyncio
async def test_a_title_naming_the_constitution_is_another_act(tmp_path: Path) -> None:
    raw = _source(
        title=_NAMES_THE_CONSTITUTION,
        kind="määrus",
        issuer="Vabariigi Valitsus",
        adopted="2015-03-27",
    )
    transformed, fetched, entry = await _paths(tmp_path, raw)
    expected = (
        "/akn/ee/act/maarrus/2015/riigikaitseliste-ulesannete-sisejulgeoleku-ja-pohiseadusliku"
        "-korra-voi-elutahtsa-teenuse-toimepidevuse-tagamisega-vahetult-seotud-ameti-ja-tookohad"
        "-riigi-ametiasutuse-valitsusasutuse-hallatava-riigiasutuse-ja-riigikaitseliste"
        "-ulesannetega-juriidilise-isiku-koosseisus"
    )
    assert (transformed, fetched, entry["doctype"]) == (expected, expected, "maarrus")
    akn = riigi_teataja_to_akn(raw)[0]
    assert etree.fromstring(akn.encode()).find("{*}act").get("name") == "maarrus"


@pytest.mark.asyncio
async def test_an_act_amending_the_constitution_is_another_act(tmp_path: Path) -> None:
    raw = _source(title="Eesti Vabariigi põhiseaduse muutmise seadus", adopted="2025-03-26")
    transformed, fetched, entry = await _paths(tmp_path, raw)
    expected = "/akn/ee/act/2025/eesti-vabariigi-pohiseaduse-muutmise-seadus"
    assert (transformed, fetched, entry["doctype"]) == (expected, expected, "act")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "kind", "abbreviation"),
    [
        ("Eesti Vabariigi põhiseadus", "seadus", ""),  # the exact title
        ("Eesti  Vabariigi Põhiseadus", "seadus", ""),  # its spacing and case vary
        ("Põhiseadus", "seadus", "PS"),  # the abbreviation
        ("Põhiseadus", "põhiseadus", ""),  # the document type
    ],
)
async def test_the_constitution_is_known_by_its_identity(
    tmp_path: Path, title: str, kind: str, abbreviation: str
) -> None:
    # Its URI is fixed: a redaction adopted in another year still lands on 1992.
    raw = _source(title=title, kind=kind, abbreviation=abbreviation, adopted="2015-03-27")
    transformed, fetched, entry = await _paths(tmp_path, raw)
    expected = "/akn/ee/act/1992/pohiseadus"
    assert (transformed, fetched, entry["doctype"]) == (expected, expected, "constitution")


@pytest.mark.asyncio
async def test_a_member_stating_no_date_fails_at_the_fetch(tmp_path: Path) -> None:
    raw = (
        _source(title="Näidisseadus")
        .replace(b"<vastuvoetud><aktikuupaev>2005-04-20</aktikuupaev></vastuvoetud>", b"")
        .replace(b"<avaldamineKuupaev>2024-01-05+02:00</avaldamineKuupaev>", b"")
    )
    acquirer, ref, _ = _acquirer(tmp_path, raw)
    with pytest.raises(ValueError, match="lacks both enactment and publication dates"):
        await acquirer.fetch(ref)
    with pytest.raises(ValueError, match="lacks both enactment and publication dates"):
        riigi_teataja_to_akn(raw)
