"""Synthetic regressions for Estonia acquisition and formatting."""

import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest
from lxml import etree

import codify.acquisition.adapters  # noqa: F401
from codify.acquisition import get_acquirer
from codify.acquisition.adapters.ee.datadump import EeDatadumpAcquirer, build_ee_index
from codify.pipeline.formats.riigi_teataja import riigi_teataja_to_akn


def source(end: str = "", title: str = "Synthetic Act", body: str = "") -> str:
    return f"""<oigusakt><metaandmed><dokumentLiik>seadus</dokumentLiik>
    <valjaandja>Riigikogu</valjaandja><globaalID>123</globaalID>
    <vastuvoetud><aktikuupaev>2022-04-13</aktikuupaev></vastuvoetud>
    <kehtivus>{end}</kehtivus></metaandmed>
    <aktinimi><nimi><pealkiri>{title}</pealkiri></nimi></aktinimi>
    <sisu>{body}</sisu></oigusakt>"""


def archive(tmp_path: Path, xml: str) -> Path:
    path = tmp_path / "laws.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("123.xml", xml)
    return path


@pytest.mark.parametrize("offset,expected", [(-1, 0), (0, 1), (1, 1)])
def test_end_date_inclusive(tmp_path: Path, offset: int, expected: int) -> None:
    end = (date.today() + timedelta(days=offset)).isoformat()
    result = build_ee_index(archive(tmp_path, source(f"<kehtivuseLopp>{end}</kehtivuseLopp>")))
    assert len(result["entries"]) == expected


def test_entities_agree_with_formatter(tmp_path: Path) -> None:
    xml = source(title="Rights &amp; Duties &#245;")
    entry = build_ee_index(archive(tmp_path, xml))["entries"]["123"]
    assert entry["title"] == "Rights & Duties õ"
    akn, _ = riigi_teataja_to_akn(xml)
    assert f"/2022/{entry['slug']}" in akn


def test_archive_portability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = archive(tmp_path, source())
    monkeypatch.chdir(tmp_path)
    assert build_ee_index(path)["archive"] == "laws.zip"
    monkeypatch.chdir(tmp_path.parent)
    assert build_ee_index(path)["archive"] == f"{tmp_path.name}/laws.zip"


def test_normal_acquisition_loads_bundled_config() -> None:
    assert isinstance(get_acquirer("ee"), EeDatadumpAcquirer)


def test_nested_inline_text_and_tails() -> None:
    xml = source(
        body=(
            "<paragrahv><paragrahvNr>1</paragrahvNr><sisuTekst><tavatekst>"
            "Start <b>bold <i>nested</i> tail</b> end</tavatekst></sisuTekst></paragrahv>"
        )
    )
    akn, _ = riigi_teataja_to_akn(xml)
    root = etree.fromstring(akn.encode())
    bold = root.find(".//{*}b")
    assert bold is not None
    assert "".join(bold.itertext()) == "bold nested tail"
    assert bold.find("{*}i").text == "nested"
    assert bold.tail == " end"


def test_generation_date_comes_from_source() -> None:
    akn, _ = riigi_teataja_to_akn(source())
    root = etree.fromstring(akn.encode())
    assert root.find(".//{*}FRBRManifestation/{*}FRBRdate").get("date") == "2022-04-13"


def test_constitution_chapter_identity() -> None:
    body = "".join(
        f"<jagu><jaguNr>{n}</jaguNr><jaguPealkiri>Chapter {n}</jaguPealkiri>"
        f"<paragrahv><paragrahvNr>{n}</paragrahvNr></paragrahv></jagu>"
        for n in (1, 2)
    )
    akn, _ = riigi_teataja_to_akn(source(title="Synthetic põhiseadus", body=body))
    root = etree.fromstring(akn.encode())
    chapters = root.findall(".//{*}chapter")
    assert [c.get("eId") for c in chapters] == ["chp_1", "chp_2"]
    assert [c.findtext("{*}heading") for c in chapters] == ["Chapter 1", "Chapter 2"]
