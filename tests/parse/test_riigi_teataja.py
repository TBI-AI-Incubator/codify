"""Test Estonian Riigi Teataja XML transformation and pipeline ingestion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codify.akn._schema import validate_akn
from codify.akn.io import parse_akn
from codify.pipeline import ingest_document
from codify.pipeline.events import Complete, MetadataExtracted
from codify.pipeline.formats.riigi_teataja import is_riigi_teataja, riigi_teataja_to_akn

_SAMPLE_EST_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-act-1" xmlns="Juurakt" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
	<metaandmed>
		<valjaandja>Riigikogu</valjaandja>
		<dokumentLiik>seadus</dokumentLiik>
		<tekstiliik>terviktekst</tekstiliik>
		<lyhend>ÄRS</lyhend>
		<vastuvoetud>
			<aktikuupaev>2022-04-13</aktikuupaev>
			<aktiNr>25</aktiNr>
		</vastuvoetud>
		<avaldamismarge>
			<RTosa>RT I</RTosa>
			<avaldamineKuupaev>2022-05-05</avaldamineKuupaev>
			<RTaasta>2022</RTaasta>
			<RTartikkel>1</RTartikkel>
			<aktViide>105052022001</aktViide>
		</avaldamismarge>
		<kehtivus>
			<kehtivuseAlgus>2023-02-01</kehtivuseAlgus>
			<kehtivuseLopp>2025-12-31</kehtivuseLopp>
		</kehtivus>
	</metaandmed>
	<aktinimi>
		<nimi>
			<pealkiri>Äriregistri seadus</pealkiri>
		</nimi>
	</aktinimi>
	<sisu>
		<preambul>
			<tavatekst>Käesolev seadus võetakse vastu ettevõtluse toetamiseks.</tavatekst>
		</preambul>
		<peatykk id="chp1">
			<peatykkNr>1</peatykkNr>
			<kuvatavNr>1. peatükk</kuvatavNr>
			<peatykkPealkiri>Üldsätted</peatykkPealkiri>
			<paragrahv id="para1">
				<paragrahvNr>1</paragrahvNr>
				<kuvatavNr>§ 1.</kuvatavNr>
				<paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
				<loige id="para1lg1">
					<loigeNr>1</loigeNr>
					<kuvatavNr>(1)</kuvatavNr>
					<sisuTekst>
						<tavatekst>Seadus sätestab äriregistri korra vastavalt </tavatekst>
						<viide>
							<kuvatavTekst>Euroopa Liidu õigusele</kuvatavTekst>
							<viideURID>
								<viideURI>./dyn=105052022001&amp;id=130062023055!pr10lg2</viideURI>
							</viideURID>
						</viide>
						<tavatekst>.</tavatekst>
					</sisuTekst>
				</loige>
				<loige id="para1lg2">
					<loigeNr>2</loigeNr>
					<kuvatavNr>(2)</kuvatavNr>
					<sisuTekst>
						<tavatekst>Valdkonna minister kehtestab:</tavatekst>
					</sisuTekst>
					<alampunkt id="para1lg2p1">
						<alampunktNr>1</alampunktNr>
						<kuvatavNr>1)</kuvatavNr>
						<sisuTekst>
							<tavatekst>registripidamise nõuded;</tavatekst>
						</sisuTekst>
					</alampunkt>
					<alampunkt id="para1lg2p2">
						<alampunktNr>2</alampunktNr>
						<kuvatavNr>2)</kuvatavNr>
						<sisuTekst>
							<tavatekst>tasumäärad.</tavatekst>
						</sisuTekst>
					</alampunkt>
				</loige>
			</paragrahv>
			<paragrahv id="para2">
				<paragrahvNr ylaIndeks="1">1</paragrahvNr>
				<kuvatavNr>§ 1¹.</kuvatavNr>
				<paragrahvPealkiri>Täiendav säte</paragrahvPealkiri>
				<loige id="para2lg1">
					<sisuTekst>
						<tavatekst>Üksik lõige ilma numbrita, millel on punktid:</tavatekst>
					</sisuTekst>
					<alampunkt id="para2lg1p1">
						<alampunktNr>1</alampunktNr>
						<kuvatavNr>1)</kuvatavNr>
						<sisuTekst><tavatekst>esimene määratlus;</tavatekst></sisuTekst>
					</alampunkt>
					<alampunkt id="para2lg1p2">
						<alampunktNr>2</alampunktNr>
						<kuvatavNr>2)</kuvatavNr>
						<sisuTekst><tavatekst>teine määratlus.</tavatekst></sisuTekst>
					</alampunkt>
				</loige>
			</paragrahv>
		</peatykk>
	</sisu>
	<lisaViide>
		<lisaViit>
			<sisuTekst>
				<fail failKuvamine="viitena" failNimi="SOM_lisa1.pdf" failVorming="pdf" id="f123"/>
			</sisuTekst>
		</lisaViit>
		<lisaPealkiri>
			<kavand>Lisa 1</kavand>
		</lisaPealkiri>
	</lisaViide>
</oigusakt>"""

_SAMPLE_MAARRUS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-maarrus-1" xmlns="Juurakt" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
	<metaandmed>
		<valjaandja>Sotsiaalkaitseminister</valjaandja>
		<dokumentLiik>määrus</dokumentLiik>
		<tekstiliik>algtekst-terviktekst</tekstiliik>
		<vastuvoetud>
			<aktikuupaev>2023-08-29</aktikuupaev>
			<aktiNr>48</aktiNr>
		</vastuvoetud>
		<avaldamismarge>
			<RTosa>RT I</RTosa>
			<avaldamineKuupaev>2023-09-01</avaldamineKuupaev>
			<RTaasta>2023</RTaasta>
			<RTartikkel>8</RTartikkel>
			<aktViide>101092023008</aktViide>
		</avaldamismarge>
		<kehtivus>
			<kehtivuseAlgus>2023-09-04</kehtivuseAlgus>
		</kehtivus>
	</metaandmed>
	<aktinimi>
		<nimi>
			<pealkiri>Toetuse maksmise tingimused</pealkiri>
		</nimi>
	</aktinimi>
	<sisu>
		<paragrahv id="para1">
			<paragrahvNr>1</paragrahvNr>
			<kuvatavNr>§ 1.</kuvatavNr>
			<paragrahvPealkiri>Reguleerimisala</paragrahvPealkiri>
			<loige id="para1lg1">
				<sisuTekst>
					<tavatekst>Määrusega sätestatakse toetuse maksmine.</tavatekst>
				</sisuTekst>
			</loige>
		</paragrahv>
	</sisu>
</oigusakt>"""


def test_is_riigi_teataja_detection() -> None:
    assert is_riigi_teataja(_SAMPLE_EST_XML)
    assert is_riigi_teataja(_SAMPLE_EST_XML.encode())
    assert not is_riigi_teataja("<akomaNtoso><act/></akomaNtoso>")
    assert not is_riigi_teataja("not xml")


def test_riigi_teataja_to_akn_validates_and_parses() -> None:
    akn_xml, meta = riigi_teataja_to_akn(_SAMPLE_EST_XML)

    validate_akn(akn_xml)

    doc = parse_akn(akn_xml)
    assert doc.frbr_work_uri == "/akn/ee/act/2022/ars"
    assert doc.frbr_expression_uri == "/akn/ee/act/2022/ars/est@2023-02-01"
    assert doc.language == "est"

    assert meta["title"] == "Äriregistri seadus"
    assert meta["doctype"] == "act"
    assert meta["year"] == 2022
    assert meta["number"] == "25"
    assert meta["date"] == "2022-04-13"
    assert meta["gazette"]["name"] == "RT I"

    # Chapter structure
    assert 'eId="chp_1"' in akn_xml
    assert "Üldsätted" in akn_xml

    # Article structure & ylaIndeks attribute normalisation
    assert 'eId="chp_1__art_1"' in akn_xml
    assert 'eId="chp_1__art_11"' in akn_xml
    assert "Reguleerimisala" in akn_xml

    # Paragraphs & points
    assert 'eId="chp_1__art_1__para_1"' in akn_xml
    assert 'eId="chp_1__art_1__para_2"' in akn_xml
    assert 'eId="chp_1__art_1__para_2__point_1"' in akn_xml
    assert 'eId="chp_1__art_1__para_2__point_2"' in akn_xml

    # Single unnumbered loige with alampunkt retains all points
    assert 'eId="chp_1__art_11__para_1__point_1"' in akn_xml
    assert 'eId="chp_1__art_11__para_1__point_2"' in akn_xml
    assert "esimene määratlus;" in akn_xml
    assert "teine määratlus." in akn_xml

    # References rewritten to public URLs & punctuation tail has no space
    assert 'href="https://www.riigiteataja.ee/akt/130062023055#pr10lg2"' in akn_xml
    assert "Euroopa Liidu õigusele</ref>." in akn_xml

    # Attachments
    assert '<attachment eId="att_1">' in akn_xml
    assert "<heading>Lisa 1</heading>" in akn_xml
    assert "/!att_1" in akn_xml
    assert 'href="https://www.riigiteataja.ee/aktilisa/105052022001/SOM_lisa1.pdf"' in akn_xml


def test_riigi_teataja_maarrus_mapping() -> None:
    akn_xml, meta = riigi_teataja_to_akn(_SAMPLE_MAARRUS_XML)

    validate_akn(akn_xml)
    doc = parse_akn(akn_xml)

    # Regulation URI pattern & metadata per ee/config.json
    assert doc.frbr_work_uri.startswith("/akn/ee/act/maarrus/2023/")
    assert meta["doctype"] == "maarrus"
    assert 'href="#minister"' in akn_xml
    assert 'showAs="Sotsiaalkaitseminister"' in akn_xml


@pytest.mark.asyncio
async def test_pipeline_ingest_riigi_teataja(tmp_path: Path) -> None:
    src = tmp_path / "test-akt.xml"
    src.write_text(_SAMPLE_EST_XML, encoding="utf-8")

    events = [ev async for ev in ingest_document(src, "ee")]
    kinds = [ev.kind for ev in events]

    assert "metadata_extracted" in kinds
    assert "parsed" in kinds
    assert "complete" in kinds

    complete_ev = next(ev for ev in events if isinstance(ev, Complete))
    assert complete_ev.document.frbr_work_uri == "/akn/ee/act/2022/ars"
    assert len(complete_ev.document.body) > 0


@pytest.mark.asyncio
async def test_dispatch_routes_by_oigusakt_root(tmp_path: Path) -> None:
    src = tmp_path / "custom.xml"
    src.write_text(_SAMPLE_EST_XML, encoding="utf-8")

    events = [ev async for ev in ingest_document(src, "xx")]
    meta_ev = next(ev for ev in events if isinstance(ev, MetadataExtracted))
    assert meta_ev.metadata["title"] == "Äriregistri seadus"


async def test_oversized_riigi_teataja_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A large text node > 10MB exceeds standard libxml2 limit without huge_tree
    big_text = "A" * 10000005
    big_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-big-1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Riigikogu</valjaandja>
		<dokumentLiik>seadus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2024-01-01</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Suur Seadus</pealkiri></nimi></aktinimi>
	<sisu>
		<paragrahv id="para1">
			<paragrahvNr>1</paragrahvNr>
			<kuvatavNr>§ 1.</kuvatavNr>
			<loige id="p1l1">
				<sisuTekst><tavatekst>{big_text}</tavatekst></sisuTekst>
			</loige>
		</paragrahv>
	</sisu>
</oigusakt>"""
    src = tmp_path / "big-akt.xml"
    src.write_text(big_xml, encoding="utf-8")

    assert is_riigi_teataja(src)
    akn_xml, meta = riigi_teataja_to_akn(src.read_bytes())
    assert meta["title"] == "Suur Seadus"
    assert "<article" in akn_xml

    from codify.akn._schema import parse_xml

    validator_flags: list[bool] = []

    def parse_for_validator(
        xml: str, *, huge_tree: bool = False, **_kwargs: object
    ) -> list[dict[str, object]]:
        validator_flags.append(huge_tree)
        parse_xml(xml, huge_tree=huge_tree)
        return []

    monkeypatch.setattr("codify.pipeline.enrich.validator.validate_akn", parse_for_validator)
    events = [event async for event in ingest_document(src, "ee")]
    assert any(isinstance(event, Complete) for event in events)
    assert validator_flags == [True]


@pytest.mark.parametrize(
    "source",
    [
        b"<!DOCTYPE oigusakt><oigusakt xmlns='Juurakt'/>",
        b"<oigusakt xmlns='Elsewhere'/>",
        b"<!--" + b"x" * 70_000 + b"--><oigusakt xmlns='Juurakt'/>",
        b"<akomaNtoso xmlns='http://docs.oasis-open.org/legaldocml/ns/akn/3.0'>"
        + b"x" * 10_000_005
        + b"</akomaNtoso>",
    ],
)
def test_detector_does_not_relax_limits_for_unconfirmed_xml(source: bytes) -> None:
    assert not is_riigi_teataja(source)


def test_riigi_teataja_constitution_mapping() -> None:
    ps_xml = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-ps-1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Rahvas</valjaandja>
		<dokumentLiik>seadus</dokumentLiik>
		<tekstiliik>terviktekst</tekstiliik>
		<lyhend>PS</lyhend>
		<vastuvoetud><aktikuupaev>1992-06-28</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Eesti Vabariigi põhiseadus</pealkiri></nimi></aktinimi>
	<sisu>
		<jagu id="jg1">
			<jaguNr>1</jaguNr>
			<kuvatavNr>I peatükk</kuvatavNr>
			<jaguPealkiri>Üldsätted</jaguPealkiri>
			<paragrahv id="para1">
				<paragrahvNr>1</paragrahvNr>
				<kuvatavNr>§ 1.</kuvatavNr>
				<loige id="para1lg1">
					<sisuTekst>
						<tavatekst>Eesti on iseseisev riik.</tavatekst>
					</sisuTekst>
				</loige>
			</paragrahv>
		</jagu>
	</sisu>
</oigusakt>"""
    akn_xml, meta = riigi_teataja_to_akn(ps_xml)
    validate_akn(akn_xml)
    doc = parse_akn(akn_xml)

    assert meta["doctype"] == "constitution"
    assert doc.frbr_work_uri == "/akn/ee/act/1992/pohiseadus"
    assert '<chapter eId="chp_1"' in akn_xml


def test_missing_dates_raises_value_error() -> None:
    bad_xml = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="bad" xmlns="Juurakt">
	<metaandmed><dokumentLiik>seadus</dokumentLiik></metaandmed>
	<aktinimi><nimi><pealkiri>Tundmatu akt</pealkiri></nimi></aktinimi>
	<sisu><paragrahv id="p1"><paragrahvNr>1</paragrahvNr></paragrahv></sisu>
</oigusakt>"""
    with pytest.raises(ValueError, match="lacks both enactment and publication dates"):
        riigi_teataja_to_akn(bad_xml)


def test_direct_sisu_tekst_and_html_konteiner() -> None:
    amend_xml = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="amend-1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Vabariigi Valitsus</valjaandja>
		<dokumentLiik>määrus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2024-01-15</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Määruse muutmine</pealkiri></nimi></aktinimi>
	<sisu>
		<sisuTekst>
			<tavatekst>Määruses tehakse järgmised muudatused:</tavatekst>
		</sisuTekst>
		<HTMLKonteiner>&lt;p&gt;Punkt 1 muudetakse.&lt;/p&gt;</HTMLKonteiner>
	</sisu>
</oigusakt>"""
    akn_xml, meta = riigi_teataja_to_akn(amend_xml)
    validate_akn(akn_xml)
    doc = parse_akn(akn_xml)
    assert meta["doctype"] == "maarrus"
    assert len(doc.body) >= 2


def test_mixed_osa_and_flat_paragrahvs_nesting() -> None:
    mixed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="test-mixed-1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Riigikogu</valjaandja>
		<dokumentLiik>seadus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2002-06-05</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Näidisseadus</pealkiri></nimi></aktinimi>
	<sisu>
		<osa id="osa1">
			<osaNr>1</osaNr>
			<kuvatavNr>1. osa</kuvatavNr>
			<osaPealkiri>Üldsätted</osaPealkiri>
		</osa>
		<sisuTekst><tavatekst>Osa 1 sissejuhatus.</tavatekst></sisuTekst>
		<paragrahv id="para1">
			<paragrahvNr>1</paragrahvNr>
			<kuvatavNr>§ 1.</kuvatavNr>
			<paragrahvPealkiri>Seaduse ülesanne</paragrahvPealkiri>
			<loige id="p1l1">
				<sisuTekst><tavatekst>Seadus reguleerib suhteid.</tavatekst></sisuTekst>
			</loige>
		</paragrahv>
		<osa id="osa2">
			<osaNr>2</osaNr>
			<kuvatavNr>2. osa</kuvatavNr>
			<osaPealkiri>Isikud</osaPealkiri>
			<peatykk id="ch1">
				<peatykkNr>1</peatykkNr>
				<kuvatavNr>1. peatükk</kuvatavNr>
				<peatykkPealkiri>Füüsilised isikud</peatykkPealkiri>
				<paragrahv id="para2">
					<paragrahvNr>2</paragrahvNr>
					<kuvatavNr>§ 2.</kuvatavNr>
					<paragrahvPealkiri>Füüsiline isik</paragrahvPealkiri>
					<loige id="p2l1">
						<sisuTekst><tavatekst>Füüsiline isik on inimene.</tavatekst></sisuTekst>
					</loige>
				</paragrahv>
			</peatykk>
		</osa>
		<osa id="osa3">
			<osaNr>3</osaNr>
			<kuvatavNr>3. osa</kuvatavNr>
			<osaPealkiri>Rakendamine</osaPealkiri>
		</osa>
		<sisuTekst><tavatekst>Osa 3 sissejuhatus.</tavatekst></sisuTekst>
		<paragrahv id="para3">
			<paragrahvNr>3</paragrahvNr>
			<kuvatavNr>§ 3.</kuvatavNr>
			<paragrahvPealkiri>Jõustumine</paragrahvPealkiri>
			<loige id="p3l1">
				<sisuTekst><tavatekst>Seadus jõustub 2026.</tavatekst></sisuTekst>
			</loige>
		</paragrahv>
	</sisu>
</oigusakt>"""
    akn_xml, _ = riigi_teataja_to_akn(mixed_xml)
    validate_akn(akn_xml)
    doc = parse_akn(akn_xml)

    # All 3 parts must be top-level body items, with no unnested root articles
    assert len(doc.body) == 3
    assert all(item.akn_type == "part" for item in doc.body)

    # Part 1 contains paragraph 1 and article 1
    part1_paras = [c for c in doc.body[0].children if c.akn_type == "paragraph"]
    assert len(part1_paras) == 1
    assert part1_paras[0].akn_eid == "part_1__para_1"
    part1_arts = [c for c in doc.body[0].children if c.akn_type == "article"]
    assert len(part1_arts) == 1
    assert part1_arts[0].akn_eid == "part_1__art_1"

    # Part 2 contains chapter 1 which contains article 2
    part2_chps = [c for c in doc.body[1].children if c.akn_type == "chapter"]
    assert len(part2_chps) == 1
    assert part2_chps[0].akn_eid == "part_2__chp_1"

    # Part 3 contains paragraph 1 (scoped per-parent) and article 3
    part3_paras = [c for c in doc.body[2].children if c.akn_type == "paragraph"]
    assert len(part3_paras) == 1
    assert part3_paras[0].akn_eid == "part_3__para_1"
    part3_arts = [c for c in doc.body[2].children if c.akn_type == "article"]
    assert len(part3_arts) == 1
    assert part3_arts[0].akn_eid == "part_3__art_3"


def test_real_estonian_acts_from_zip() -> None:
    archive = os.environ.get("CODIFY_TEST_EE_ARCHIVE")
    if not archive:
        pytest.skip("set CODIFY_TEST_EE_ARCHIVE to run the optional corpus check")
    zip_path = Path(archive)
    assert zip_path.is_file(), zip_path

    import zipfile

    targets = ["123122022025.xml", "101092023008.xml", "101032023009.xml", "131122024048.xml"]
    with zipfile.ZipFile(zip_path) as z:
        for name in targets:
            raw = z.read(name)
            assert is_riigi_teataja(raw)
            akn_xml, meta = riigi_teataja_to_akn(raw)
            validate_akn(akn_xml)
            doc = parse_akn(akn_xml)
            assert doc.frbr_work_uri.startswith("/akn/ee/act/")
            assert len(doc.body) > 0
