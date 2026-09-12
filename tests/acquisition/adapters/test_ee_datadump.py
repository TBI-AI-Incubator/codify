"""Estonian bulk XML archive adapter tests over a synthetic zip and index."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

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
async def test_ee_datadump_fetch_derives_slug_from_index(tmp_path: Path) -> None:
    # When ref has no extra.slug, fetch must derive from index title, not use raw ID
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
    assert doc.frbr_work_uri == "/akn/ee/act/2022/ars"


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
