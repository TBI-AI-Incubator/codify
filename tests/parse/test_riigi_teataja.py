"""Test Estonian Riigi Teataja XML transformation and pipeline ingestion."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import structlog
from lxml import etree

from codify.akn._schema import AKN_NS, validate_akn
from codify.akn.bluebell import akn_xml_to_bluebell, bluebell_to_akn
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
		<globaalID>105052022001</globaalID>
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
    # Keyed by the full title, not the source's abbreviation ÄRS.
    assert doc.frbr_work_uri == "/akn/ee/act/2022/ariregistri-seadus"
    assert doc.frbr_expression_uri == "/akn/ee/act/2022/ariregistri-seadus/est@2023-02-01"
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
    assert complete_ev.document.frbr_work_uri == "/akn/ee/act/2022/ariregistri-seadus"
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


def test_detector_bounds_an_oversized_xml_string_before_encoding() -> None:
    source = "  <oigusakt xmlns='Juurakt'>" + "x" * 10_000_005
    assert is_riigi_teataja(source)


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
		<HTMLKonteiner>&lt;p&gt;Punkt 1 muudetakse.
		§ 43&lt;sup&gt;8&lt;/sup&gt; jäetakse välja.&lt;/p&gt;</HTMLKonteiner>
	</sisu>
</oigusakt>"""
    akn_xml, meta = riigi_teataja_to_akn(amend_xml)
    validate_akn(akn_xml)
    doc = parse_akn(akn_xml)
    assert meta["doctype"] == "maarrus"
    assert len(doc.body) >= 2
    assert "Punkt 1 muudetakse. § 43⁸ jäetakse välja." in akn_xml


def test_display_numbers_keep_their_superscripts() -> None:
    # The source wraps a superscripted kuvatavNr in CDATA, so its <sup> arrives as text.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="sup-1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Riigikogu</valjaandja>
		<dokumentLiik>seadus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2000-11-15</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Näidisseadus</pealkiri></nimi></aktinimi>
	<sisu>
	<osa id="o1b1">
		<osaNr ylaIndeks="1">1</osaNr>
		<kuvatavNr><![CDATA[1<sup>1</sup>. osa]]></kuvatavNr>
		<peatykk id="ch5b1">
			<peatykkNr ylaIndeks="1">5</peatykkNr>
			<kuvatavNr><![CDATA[5<sup>1</sup>. peatükk]]></kuvatavNr>
			<peatykkPealkiri>Andmekogud</peatykkPealkiri>
			<jagu id="j2b1">
			<jaguNr ylaIndeks="1">2</jaguNr>
			<kuvatavNr><![CDATA[2<sup>1</sup>. jagu]]></kuvatavNr>
			<jaotis id="jt1b1">
			<jaotisNr ylaIndeks="1">1</jaotisNr>
			<kuvatavNr><![CDATA[1<sup class="x">1</sup >. jaotis]]></kuvatavNr>
			<paragrahv id="para43b9">
				<paragrahvNr ylaIndeks="9">43</paragrahvNr>
				<kuvatavNr><![CDATA[§ 43<sup>9</sup>. ]]></kuvatavNr>
				<paragrahvPealkiri>Riigi infosüsteemi kindlustavad süsteemid</paragrahvPealkiri>
				<loige id="lg1b1">
					<loigeNr ylaIndeks="1">1</loigeNr>
					<kuvatavNr><![CDATA[(1<sup>1</sup>)]]></kuvatavNr>
					<sisuTekst><tavatekst>Kindlustavad süsteemid on:</tavatekst></sisuTekst>
					<alampunkt id="p14b1">
						<alampunktNr ylaIndeks="1">14</alampunktNr>
						<kuvatavNr><![CDATA[14<sup>1</sup>) ]]></kuvatavNr>
						<sisuTekst>
							<HTMLKonteiner>§ 43&lt;sup&gt;8&lt;/sup&gt;</HTMLKonteiner>
						</sisuTekst>
					</alampunkt>
				</loige>
			</paragrahv>
			<paragrahv id="para43b10">
				<paragrahvNr ylaIndeks="10">43</paragrahvNr>
				<kuvatavNr><![CDATA[§ 43<sup>10</sup>.]]></kuvatavNr>
				<HTMLKonteiner>§ 43&lt;sup&gt;7&lt;/sup&gt;</HTMLKonteiner>
			</paragrahv>
			</jaotis>
			</jagu>
		</peatykk>
	</osa>
	</sisu>
</oigusakt>"""
    akn_xml, _ = riigi_teataja_to_akn(xml)
    validate_akn(akn_xml)
    nums = [el.text for el in etree.fromstring(akn_xml.encode()).iter(f"{{{AKN_NS}}}num")]
    assert nums == [
        "1¹. osa",
        "5¹. peatükk",
        "2¹. jagu",
        "1¹. jaotis",
        "§ 43⁹.",
        "(1¹)",
        "14¹)",
        "§ 43¹⁰.",
    ]
    assert "§ 43⁸</p>" in akn_xml
    assert "§ 43⁷</p>" in akn_xml


@pytest.mark.asyncio
async def test_superscripted_points_pass_the_validator(tmp_path: Path) -> None:
    # "2¹" passes str.isdigit() but not int(); the validator must skip it, not crash.
    src = tmp_path / "punktid.xml"
    src.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<oigusakt id="m1" xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Vabariigi Valitsus</valjaandja>
		<dokumentLiik>määrus</dokumentLiik>
		<vastuvoetud><aktikuupaev>2010-01-01</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Näidismäärus</pealkiri></nimi></aktinimi>
	<sisu>
		<punkt id="p1"><punktNr>1</punktNr><kuvatavNr>1.</kuvatavNr>
			<sisuTekst><tavatekst>Üks.</tavatekst></sisuTekst></punkt>
		<punkt id="p2"><punktNr>2</punktNr><kuvatavNr>2.</kuvatavNr>
			<sisuTekst><tavatekst>Kaks.</tavatekst></sisuTekst></punkt>
		<punkt id="p2b1"><punktNr ylaIndeks="1">2</punktNr>
			<kuvatavNr><![CDATA[2<sup>1</sup>.]]></kuvatavNr>
			<sisuTekst><tavatekst>Kaks-üks.</tavatekst></sisuTekst></punkt>
	</sisu>
</oigusakt>""",
        encoding="utf-8",
    )
    events = [ev async for ev in ingest_document(src, "ee")]
    assert [ev.kind for ev in events if ev.kind in {"failed", "complete"}] == ["complete"]


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


# --- line breaks, every text element, tables ----------------------------------

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "riigi_teataja"
_A = f"{{{AKN_NS}}}"


def _fixture(name: str) -> str:
    akn_xml, _ = riigi_teataja_to_akn((_FIXTURES / name).read_bytes())
    validate_akn(akn_xml)
    return akn_xml


def _by_eid(akn_xml: str, eid: str) -> etree._Element:
    root = etree.fromstring(akn_xml.encode())
    found = [el for el in root.iter() if el.get("eId") == eid]
    assert len(found) == 1, (eid, len(found))
    return found[0]


def _texts(el: etree._Element, tag: str = "p") -> list[str]:
    return [" ".join("".join(p.itertext()).split()) for p in el.iter(_A + tag)]


def _cells(table: etree._Element) -> list[list[str]]:
    return [
        [" | ".join(_texts(cell)) for cell in tr if etree.QName(cell).localname in {"td", "th"}]
        for tr in table.iter(_A + "tr")
    ]


def _act(sisu: str, *, doctype: str = "määrus") -> str:
    akn_xml, _ = riigi_teataja_to_akn(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<oigusakt xmlns="Juurakt">
	<metaandmed>
		<valjaandja>Vabariigi Valitsus</valjaandja>
		<dokumentLiik>{doctype}</dokumentLiik>
		<vastuvoetud><aktikuupaev>2020-01-01</aktikuupaev></vastuvoetud>
	</metaandmed>
	<aktinimi><nimi><pealkiri>Näidismäärus</pealkiri></nimi></aktinimi>
	<sisu>{sisu}</sisu>
</oigusakt>"""
    )
    validate_akn(akn_xml)
    return akn_xml


def _html_act(markup: str) -> str:
    return _act(f"<sisuTekst><HTMLKonteiner><![CDATA[{markup}]]></HTMLKonteiner></sisuTekst>")


def test_a_line_break_keeps_the_text_after_it() -> None:
    akn_xml = _fixture("line-break.xml")
    para = _by_eid(akn_xml, "art_227__para_1")
    assert _texts(para) == [
        "Näidiskaubaga kauplemise eest –",
        "karistatakse rahatrahviga kuni 100 ühikut.",
    ]
    assert [p.get("eId") for p in para.iter(_A + "p")] == [
        "art_227__para_1__p_1",
        "art_227__para_1__p_2",
    ]
    provision = next(p for p in parse_akn(akn_xml).body[0].children if p.akn_eid.endswith("_1"))
    assert provision.text == (
        "Näidiskaubaga kauplemise eest –\nkaristatakse rahatrahviga kuni 100 ühikut."
    )
    # The break is a line in Bluebell too, so the source pane round-trips it.
    bluebell = akn_xml_to_bluebell(akn_xml)
    assert "kauplemise eest –\n" in bluebell
    reparsed, _, errors = bluebell_to_akn(bluebell, country="ee", language="est")
    assert errors == []
    assert "karistatakse rahatrahviga" in reparsed


def test_a_content_table_becomes_an_akn_table() -> None:
    akn_xml = _fixture("fee-table.xml")
    para = _by_eid(akn_xml, "art_2__para_1")
    content = para.find(_A + "content")
    assert content is not None
    assert [etree.QName(c).localname for c in content] == ["p", "table", "p"]
    table = _by_eid(akn_xml, "art_2__para_1__table_1")
    assert [tr.get("eId") for tr in table] == [f"art_2__para_1__table_1__tr_{i}" for i in (1, 2, 3)]
    assert _cells(table) == [
        ["1) põhitasu kuus", "1.00 eurot;"],
        ["2) tasu vee eest (1 m³ kohta)", "0.10 eurot;"],
        ["3) tasu heitvee eest (1 m³ kohta ilma maksuta)", "0.20 eurot."],
    ]
    # The note after the table is its own block, italic only, with no indent text.
    note = _by_eid(akn_xml, "art_2__para_1__p_2")
    assert note.text is None
    assert [etree.QName(c).localname for c in note] == ["i"]
    # A subscript inside a cell stays inline.
    grouped = _cells(_by_eid(akn_xml, "art_2__para_3__table_1"))
    assert grouped[0][1] == "Aine X7 sisaldus (mg/l)"
    assert _by_eid(akn_xml, "art_2__para_3__table_1").find(f".//{_A}sub") is not None
    assert len(grouped) == 9
    reparsed, _, errors = bluebell_to_akn(akn_xml_to_bluebell(akn_xml), country="ee")
    assert errors == []
    assert reparsed.count("<table") == 3


def test_a_native_table_keeps_its_rows_and_cell_breaks() -> None:
    akn_xml = _fixture("native-table.xml")
    para = _by_eid(akn_xml, "art_3__para_2")
    assert _texts(para.find(_A + "content"))[0] == ("Palgagruppide vahemikud on järgmised:")
    rows = _cells(_by_eid(akn_xml, "art_3__para_2__table_1"))
    assert rows[0] == [
        "Grupp",
        "Vahemik eurodes, | alammäär",
        "Vahemik eurodes, ülemmäär",
        "Ametikohad",
    ]
    assert rows[1][0] == "I"
    assert len(rows) == 3


@pytest.mark.parametrize(
    ("name", "left_out", "dropped", "kept"),
    [
        # Signature lines placed beside a blank column.
        (
            "signature-layout.xml",
            {"placed": 1},
            ["Peaminister", "Riigisekretär"],
            ["kiita heaks näidisriikide vaheline kokkulepe"],
        ),
        # One row of blocks, each signed.
        (
            "signature-row.xml",
            {"signed": 1},
            ["allkirjastatud", "juhatuse esimees"],
            ["2.2. täitja teeb ettevalmistusi asendusreisiks."],
        ),
        # An annex's approval stamp, before the annex's own table.
        (
            "approval-stamp.xml",
            {"placed": 1},
            ["KINNITATUD", "määrusega nr 9"],
            ["VASTAVUSTABEL", "Määrus jõustub 2010. aasta 1. augustil."],
        ),
    ],
)
def test_layout_and_signature_tables_stay_out(
    name: str, left_out: dict[str, int], dropped: list[str], kept: list[str]
) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as captured:
        akn_xml = _fixture(name)
    structlog.reset_defaults()
    body = etree.fromstring(akn_xml.encode()).find(f".//{_A}body")
    assert body is not None
    text = " ".join(_texts(body))
    assert [w for w in dropped if w in text] == []
    assert [w for w in kept if w not in text] == []
    logged = [r for r in captured if r.get("event") == "riigi_teataja_tables_left_out"]
    assert [{k: r[k] for k in ("placed", "signed") if k in r} for r in logged] == [left_out]


def test_a_spanned_table_beside_a_stamp_is_kept() -> None:
    akn_xml = _fixture("approval-stamp.xml")
    table = _by_eid(akn_xml, "art_8__table_1")
    head = table[0]
    assert [(c.get("colspan"), " | ".join(_texts(c))) for c in head] == [
        (None, "Aastad"),
        ("2", "Klassid | näidiskoolis"),
        ("3", "Klassid | tavakoolis"),
    ]
    assert _cells(table)[1][:4] == ["1", "1st", "Algkool", "1. klass"]
    assert table[1][2].get("rowspan") == "5"


@pytest.mark.parametrize(
    ("rows", "cells"),
    [
        # One row without a signature is data.
        ("<tr><td>Riigilõiv</td><td>10 eurot</td></tr>", [["Riigilõiv", "10 eurot"]]),
        # Signing named in a fee is not a signature.
        (
            "<tr><td>Hind</td><td>Allkirjastamise tasu 5 eurot</td></tr>",
            [["Hind", "Allkirjastamise tasu 5 eurot"]],
        ),
        ("<tr><td>Allkirjastamise tasu 5 eurot</td></tr>", [["Allkirjastamise tasu 5 eurot"]]),
        # One signed cell beside data is not a signature row.
        (
            "<tr><td>Leping (allkirjastatud digitaalselt)</td><td>10 eurot</td></tr>",
            [["Leping (allkirjastatud digitaalselt)", "10 eurot"]],
        ),
        # A spanned label beside its value is data, though a row looks short.
        (
            "<tr><td rowspan='2'>Määr</td><td></td></tr><tr><td>10</td></tr>",
            [["Määr", ""], ["10"]],
        ),
        (
            "<tr><td colspan='2'>Tasu</td><td></td></tr>"
            "<tr><td>Riigilõiv</td><td></td><td></td></tr>",
            [["Tasu", ""], ["Riigilõiv", "", ""]],
        ),
        # A long line beside a blank cell is body text, not a stamp.
        (
            f"<tr><td></td><td>{'Seaduse tekst. ' * 8}</td></tr>",
            [["", ("Seaduse tekst. " * 8).strip()]],
        ),
        # Signing named in a data table.
        (
            "<tr><td>Dokument</td><td>Allkirjastamise kuupäev</td></tr>"
            "<tr><td>Leping</td><td>01.01.2020</td></tr>",
            [["Dokument", "Allkirjastamise kuupäev"], ["Leping", "01.01.2020"]],
        ),
        # One column, so no blank column beside it.
        ("<tr><td>Esimees</td></tr><tr><td>Liige</td></tr>", [["Esimees"], ["Liige"]]),
        # A blank column beside rows that each fill two cells.
        (
            "<tr><td>E</td><td></td><td>10:00</td></tr><tr><td>T</td><td></td><td>11:00</td></tr>",
            [["E", "", "10:00"], ["T", "", "11:00"]],
        ),
    ],
)
def test_data_tables_near_the_layout_rules_are_kept(rows: str, cells: list[list[str]]) -> None:
    akn_xml = _html_act(f"<p>Sissejuhatus.</p><table>{rows}</table>")
    assert _cells(_by_eid(akn_xml, "para_1__table_1")) == cells
    assert "Sissejuhatus." in akn_xml


def test_headings_keep_inline_text() -> None:
    akn_xml = _act(
        """<peatykk><peatykkNr>1</peatykkNr><kuvatavNr>1. peatükk</kuvatavNr>
		<peatykkPealkiri><b>ÜLDSÄTTED</b></peatykkPealkiri>
		<paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
			<paragrahvPealkiri>Osa 5<sup>1</sup>. ptk<reavahetus/>rakendamine</paragrahvPealkiri>
			<sisuTekst><tavatekst>Tekst.</tavatekst></sisuTekst>
		</paragrahv></peatykk>"""
    )
    assert _texts(etree.fromstring(akn_xml.encode()), "heading") == [
        "ÜLDSÄTTED",
        "Osa 5¹. ptk rakendamine",
    ]


def test_every_text_element_of_a_unit_is_written() -> None:
    akn_xml = _act(
        """<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
			<sisuTekst><tavatekst>Määrusega muudetakse:</tavatekst></sisuTekst>
			<loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
				<sisuTekst><tavatekst>Esimene osa.</tavatekst></sisuTekst>
				<sisuTekst><tavatekst>Teine osa.</tavatekst></sisuTekst>
			</loige>
			<loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
				<sisuTekst><tavatekst>Loetelu:</tavatekst></sisuTekst>
				<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
					<sisuTekst><tavatekst>punkt;</tavatekst></sisuTekst></alampunkt>
				<sisuTekst><tavatekst>Lõpetus.</tavatekst></sisuTekst>
			</loige>
			<sisuTekst><tavatekst>Paragrahvi lõpp.</tavatekst></sisuTekst>
		</paragrahv>
		<paragrahv><paragrahvNr>3</paragrahvNr><kuvatavNr>§ 3.</kuvatavNr>
			<muutmismarge><tavatekst>Kehtetu -</tavatekst></muutmismarge></paragrahv>
		<paragrahv><paragrahvNr>4</paragrahvNr><kuvatavNr>§ 4.</kuvatavNr>
			<sisuTekst><tavatekst>Ees.</tavatekst></sisuTekst>
			<loige><sisuTekst><tavatekst>Lõige.</tavatekst></sisuTekst></loige></paragrahv>
		<punkt><punktNr>2</punktNr><kuvatavNr>2.</kuvatavNr>
			<sisuTekst><tavatekst>Punkti sissejuhatus:</tavatekst></sisuTekst>
			<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
				<sisuTekst><tavatekst>alampunkt.</tavatekst></sisuTekst></alampunkt>
		</punkt>"""
    )
    assert _texts(_by_eid(akn_xml, "art_1").find(_A + "intro")) == ["Määrusega muudetakse:"]
    assert _texts(_by_eid(akn_xml, "art_1__para_1")) == ["Esimene osa.", "Teine osa."]
    assert _texts(_by_eid(akn_xml, "art_1__para_2").find(_A + "wrapUp")) == ["Lõpetus."]
    assert _texts(_by_eid(akn_xml, "art_1").find(_A + "wrapUp")) == ["Paragrahvi lõpp."]
    assert _texts(_by_eid(akn_xml, "point_2__point_1")) == ["alampunkt."]
    assert _texts(_by_eid(akn_xml, "art_3")) == ["Kehtetu -"]
    assert _texts(_by_eid(akn_xml, "art_4")) == ["Ees.", "Lõige."]


def test_text_between_units_keeps_its_place() -> None:
    akn_xml = _act(
        """<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
			<loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
				<sisuTekst><tavatekst>Esimene.</tavatekst></sisuTekst></loige>
			<sisuTekst><tavatekst>Lõigete vahel.</tavatekst></sisuTekst>
			<loige><loigeNr>2</loigeNr><kuvatavNr>(2)</kuvatavNr>
				<sisuTekst><tavatekst>Loetelu:</tavatekst></sisuTekst>
				<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
					<sisuTekst><tavatekst>üks;</tavatekst></sisuTekst></alampunkt>
				<sisuTekst><tavatekst>Punktide vahel.</tavatekst></sisuTekst>
				<alampunkt><alampunktNr>2</alampunktNr><kuvatavNr>2)</kuvatavNr>
					<sisuTekst><tavatekst>kaks.</tavatekst></sisuTekst></alampunkt>
			</loige>
			<sisuTekst><tavatekst>Pärast punkte.</tavatekst></sisuTekst>
			<loige><loigeNr>3</loigeNr><kuvatavNr>(3)</kuvatavNr>
				<sisuTekst><tavatekst>Kolmas.</tavatekst></sisuTekst></loige>
		</paragrahv>"""
    )
    assert _texts(_by_eid(akn_xml, "art_1")) == [
        "Esimene.",
        "Lõigete vahel.",
        "Loetelu:",
        "üks;",
        "Punktide vahel.",
        "kaks.",
        "Pärast punkte.",
        "Kolmas.",
    ]
    assert _texts(_by_eid(akn_xml, "art_1__para_2").find(_A + "wrapUp")) == ["Pärast punkte."]
    assert _texts(_by_eid(akn_xml, "art_1__para_1")) == ["Esimene.", "Lõigete vahel."]
    assert _texts(_by_eid(akn_xml, "art_1__para_2__point_1")) == ["üks;", "Punktide vahel."]


def test_inline_text_keeps_every_tail() -> None:
    akn_xml = _act(
        """<sisuTekst><tavatekst><b>Paks<reavahetus/>jätk</b> ja
			<i>kald</i> <u>joon</u>.<tundmatu>Peidus</tundmatu> lõpp<i><reavahetus/> </i>
			</tavatekst>
			<muutmismarge><tavatekst>[RT I, 01.01.2020, 1]</tavatekst></muutmismarge>
			<veaparandus><veaparandusTekst>Parandatud trükiviga</veaparandusTekst></veaparandus>
			<fail failNimi="lisa.pdf">QUJDRA==</fail>
		</sisuTekst>"""
    )
    para = _by_eid(akn_xml, "para_1")
    assert _texts(para) == ["Paks", "jätk ja kald joon.Peidus lõpp"]
    # A break inside bold reopens the bold in the next block.
    second = _by_eid(akn_xml, "para_1__p_2")
    assert [etree.QName(c).localname for c in second] == ["b", "i", "u"]
    assert "RT I, 01.01.2020" not in akn_xml
    assert "trükiviga" not in akn_xml
    assert "QUJDRA" not in akn_xml


def test_html_markup_maps_to_blocks_and_cells() -> None:
    akn_xml = _html_act(
        "<p>Esimene<br/>teine</p><!-- märkus --><div>kolmas</div>"
        "<table><tr><th colspan='2'>Pealkiri</th></tr>"
        "<tr><td colspan='1'>a<table><tr><td>sisemine</td></tr></table></td>"
        "<td rowspan='x' colspan='²'>b</td>"
        "</tr><tr></tr>"
        "<caption>Tabeli pealdis</caption></table>"
        "<style>p {{ color: red }}</style><script>alert(1)</script>"
    )
    assert _texts(_by_eid(akn_xml, "para_1").find(_A + "content"))[:3] == [
        "Esimene",
        "teine",
        "kolmas",
    ]
    table = _by_eid(akn_xml, "para_1__table_1")
    head = table[0][0]
    assert etree.QName(head).localname == "th"
    assert head.get("colspan") == "2"
    assert table[1][1].get("rowspan") is None
    assert table[1][1].get("colspan") is None
    assert table[1][0].get("colspan") is None
    # The inner table is the cell's text, not rows of the outer one or a table of its own.
    assert _cells(table) == [["Pealkiri"], ["a | sisemine", "b"]]
    assert akn_xml.count("<table") == 1
    assert "märkus" not in akn_xml
    assert "Tabeli pealdis" in _texts(_by_eid(akn_xml, "para_1").find(_A + "content"))
    assert "color" not in akn_xml
    assert "alert" not in akn_xml


def test_table_edges_keep_text_and_stay_valid() -> None:
    akn_xml = _act(
        """<sisuTekst><tabel><tr/><tr>
			<tc><sisuTekst><tavatekst>Välimine</tavatekst></sisuTekst></tc>
			<tc><sisuTekst><tavatekst>Lahter</tavatekst>
				<tabel><tr><tc><sisuTekst><tavatekst>Sisemine</tavatekst></sisuTekst></tc>
				<tc><sisuTekst><tavatekst>Teine</tavatekst></sisuTekst></tc></tr></tabel>
			</sisuTekst></tc></tr></tabel></sisuTekst>"""
    )
    table = _by_eid(akn_xml, "para_1__table_1")
    assert _cells(table) == [["Välimine", "Lahter | Sisemine | Teine"]]
    assert akn_xml.count("<table") == 1
    rowless = _html_act("<table><caption>Pealdis</caption></table>")
    assert "<table" not in rowless
    assert "Pealdis" in rowless


@pytest.mark.parametrize(
    "sisu",
    [
        "<sisuTekst><HTMLKonteiner><![CDATA[<table><tr><td></td><td>Minister Mari Mets</td></tr>"
        "</table>]]></HTMLKonteiner></sisuTekst>",
        "<HTMLKonteiner><![CDATA[<table><tr><td></td><td>Minister Mari Mets</td></tr>"
        "</table>]]></HTMLKonteiner>",
        "<sisuTekst><tabel><tr><tc><sisuTekst/></tc><tc><sisuTekst><tavatekst>Minister Mari Mets"
        "</tavatekst></sisuTekst></tc></tr></tabel></sisuTekst>",
    ],
)
def test_a_dropped_table_leaves_no_text(sisu: str) -> None:
    akn_xml = _act(sisu)
    assert "Mari Mets" not in akn_xml
    assert "&lt;" not in akn_xml


def test_a_short_native_row_is_not_a_blank_column() -> None:
    akn_xml = _act(
        """<sisuTekst><tabel>
			<tr><tc><sisuTekst><tavatekst>Määr</tavatekst></sisuTekst></tc><tc><sisuTekst/></tc></tr>
			<tr><tc><sisuTekst><tavatekst>10</tavatekst></sisuTekst></tc></tr>
		</tabel></sisuTekst>"""
    )
    assert _cells(_by_eid(akn_xml, "para_1__table_1")) == [["Määr", ""], ["10"]]


def test_a_native_cell_reads_its_html() -> None:
    akn_xml = _act(
        """<sisuTekst><tabel><tr>
			<tc><sisuTekst><tavatekst>Nimetus</tavatekst></sisuTekst></tc>
			<tc><HTMLKonteiner><![CDATA[<p>10 <b>eurot</b></p>]]></HTMLKonteiner></tc>
		</tr><tr>
			<tc><sisuTekst><tavatekst>Teine</tavatekst></sisuTekst></tc>
			<tc><sisuTekst><tavatekst>20 eurot</tavatekst></sisuTekst></tc>
		</tr></tabel></sisuTekst>"""
    )
    assert _cells(_by_eid(akn_xml, "para_1__table_1"))[0] == ["Nimetus", "10 eurot"]
    assert "&lt;" not in akn_xml


def test_an_emptied_block_takes_no_editorial_note() -> None:
    akn_xml = _act(
        """<sisuTekst><HTMLKonteiner><![CDATA[<table><tr><td></td><td>Minister</td></tr>
		</table>]]></HTMLKonteiner><muutmismarge><tavatekst>[RT I, 1]</tavatekst></muutmismarge>
		</sisuTekst>"""
    )
    assert _texts(_by_eid(akn_xml, "para_1")) == [""]


def test_stored_rows_and_reader_keep_unit_text_in_reading_order() -> None:
    import uuid

    from codify.storage.documents import _eid_index, _to_provision
    from codify.storage.mappers import document_to_rows

    table = (
        "<![CDATA[<table><tr><td>{0}</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>]]>"
    )
    akn_xml = _act(
        f"""<paragrahv><paragrahvNr>1</paragrahvNr><kuvatavNr>§ 1.</kuvatavNr>
			<sisuTekst><tavatekst>Ees.</tavatekst></sisuTekst>
			<loige><loigeNr>1</loigeNr><kuvatavNr>(1)</kuvatavNr>
				<sisuTekst><tavatekst>Loetelu:</tavatekst></sisuTekst>
				<alampunkt><alampunktNr>1</alampunktNr><kuvatavNr>1)</kuvatavNr>
					<sisuTekst><tavatekst>punkt;</tavatekst></sisuTekst></alampunkt>
				<sisuTekst><tavatekst>Lõpetus.</tavatekst>
					<HTMLKonteiner>{table.format("L")}</HTMLKonteiner></sisuTekst></loige>
			<sisuTekst><tavatekst>Lõpp.</tavatekst>
				<HTMLKonteiner>{table.format("P")}</HTMLKonteiner></sisuTekst>
		</paragrahv>
		<paragrahv><paragrahvNr>2</paragrahvNr><kuvatavNr>§ 2.</kuvatavNr>
			<sisuTekst><HTMLKonteiner><![CDATA[<table><tr><td>A</td><td>10</td></tr>
			<tr><td>B</td><td><p>20</p><p>eurot</p></td></tr></table>]]></HTMLKonteiner></sisuTekst>
		</paragrahv>"""
    )
    _, _, rows, _, _, _ = document_to_rows(parse_akn(akn_xml), law_id=uuid.uuid4())
    by_section: dict[object, list[tuple[int, str, str]]] = {}
    for r in rows:
        by_section.setdefault(r.section_id, []).append((r.position, r.akn_eid, r.text))
    reading = [(eid, text) for sec in by_section.values() for _, eid, text in sorted(sec)]
    assert reading == [
        ("art_1__intro", "Ees."),
        ("art_1__para_1", "Loetelu:"),
        ("art_1__para_1__point_1", "punkt;"),
        ("art_1__para_1__wrapup", "Lõpetus. L 1 B 2"),
        ("art_1__wrapup", "Lõpp. P 1 B 2"),
        ("art_2__content", "A 10 B 20 eurot"),
    ]
    # The reader renders each split-off run in its own row, tables included,
    # and the lõige's own block leaves its wrap-up to that row.
    persisted = frozenset(r.akn_eid for r in rows)
    index = _eid_index(etree.fromstring(akn_xml.encode()))
    rendered = {r.akn_eid: _to_provision(r, index, persisted).blocks for r in rows}
    para = rendered["art_1__para_1"][0]
    assert para.wrap_up == []
    assert [b.akn_type for b in para.blocks] == []
    for eid, table_eid in (
        ("art_1__para_1__wrapup", "art_1__para_1__table_1"),
        ("art_1__wrapup", "art_1__table_1"),
    ):
        (closing,) = rendered[eid]
        assert closing.intro and closing.intro[0].text.startswith("Lõp")
        assert [b.akn_eid for b in closing.blocks] == [table_eid]


def test_a_wrap_up_closing_subsections_is_not_rowed_before_them() -> None:
    import uuid

    from codify.akn import Article, Chapter
    from codify.akn.document import Document
    from codify.storage.mappers import document_to_rows

    chapter = Chapter(
        akn_eid="chp_1",
        akn_type="chapter",
        position=0,
        wrap_up="Closing words.",
        children=[Article(akn_eid="chp_1__art_1", akn_type="article", position=0, text="Body.")],
    )
    doc = Document(
        id=uuid.uuid4(),
        frbr_work_uri="/akn/xz/act/2012/7",
        frbr_expression_uri="/akn/xz/act/2012/7/est@2012",
        language="est",
        expression_date="2012-01-01",
        body=[chapter],
    )
    _, _, rows, _, _, _ = document_to_rows(doc, law_id=uuid.uuid4())
    assert [r.akn_eid for r in rows] == ["chp_1__art_1__content"]


def test_a_native_cell_is_classified_on_its_visible_text() -> None:
    akn_xml = _act(
        """<sisuTekst><tabel><tr><tc><HTMLKonteiner><![CDATA[<a href="allkirjastatud-leping.pdf">
		Allkirjastamise tasu 5 eurot</a>]]></HTMLKonteiner></tc></tr></tabel></sisuTekst>"""
    )
    assert _cells(_by_eid(akn_xml, "para_1__table_1")) == [["Allkirjastamise tasu 5 eurot"]]
