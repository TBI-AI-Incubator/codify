"""Both checks `docs/jurisdictions/adding-a-jurisdiction.md` asks a reader to run.
`parse_to_akn` never reads `document_classes`, so the round-trip is a syntax and
schema check and the anchor scan is the one that exercises the config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

from codify.akn.bluebell import parse_to_akn
from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors
from codify.pipeline.enrich.validator import validate_akn

_NS = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}

# Relative to this file, so the fixture travels with the package that reads it.
_CORPUS = Path(__file__).parent / "fixtures" / "synthetic" / "xq"

# Read at import: `parametrize` needs the cases at collection time.
_MANIFEST = json.loads((_CORPUS / "manifest.json").read_text(encoding="utf-8"))

# Pinned, not derived: deriving them from the parsed file proves nothing.
_EXPECTED = {
    "publicacion-2004.bluebell": {"part": 2, "article": 5, "paragraph": 2},
    "archivos-2011.bluebell": {"part": 2, "article": 5, "paragraph": 2},
    "archivos-reforma-2018.bluebell": {"part": 2, "article": 5, "paragraph": 0},
}


@pytest.mark.parametrize("law", _MANIFEST["laws"], ids=lambda law: law["file"])
def test_each_guide_law_round_trips_to_valid_akn(law: dict) -> None:
    """The guide's step 3. A year, not a full date: an ISO date mints a work URI
    the validator reports as uncitable, which the guide documents as a trap."""
    text = (_CORPUS / law["file"]).read_text(encoding="utf-8")
    akn = parse_to_akn(
        text,
        "xq",
        doctype=law["doctype"],
        date=law["date"][:4],
        number=law["number"],
        language=law["language"],
    )
    root = etree.fromstring(akn.encode())
    counts = {k: len(root.findall(f".//a:{k}", _NS)) for k in ("part", "article", "paragraph")}
    assert counts == _EXPECTED[law["file"]]
    assert validate_akn(akn) == []
    uri = root.find(".//a:FRBRWork/a:FRBRthis", _NS)
    assert uri is not None
    assert uri.get("value") == f"/akn/xq/act/{law['date'][:4]}/{law['number']}"


def test_the_manifest_cites_the_uris_the_parser_mints() -> None:
    """A relation keyed on the local instrument name dangles: the doctype
    normalises to its AKN element before the URI is minted, so `/akn/xq/ley/...`
    names nothing. This is the guide's own worked trap, and the manifest fell
    into it before review caught it."""
    minted = {f"/akn/xq/act/{law['date'][:4]}/{law['number']}" for law in _MANIFEST["laws"]}
    for law in _MANIFEST["laws"]:
        for uri in law.get("cites", []):
            assert uri in minted, f"{law['file']} cites {uri}"
        amends = law.get("amends")
        if amends is not None:
            assert isinstance(amends, str), "amends is scalar in every corpus manifest"
            assert amends in minted


def test_the_anchor_scan_reads_the_local_terms_the_config_declares() -> None:
    """The guide's step 4, and the only one that exercises the config.

    Source-form text, not Bluebell: `TÍTULO`/`Artículo` rather than
    `PART`/`ARTICLE`. The scanner builds its alternation from `local_term`, so
    this is where a wrong hierarchy shows up.
    """
    config = load_config("xq")
    assert config is not None
    source = (
        "TÍTULO I - Disposiciones generales\n\n"
        "Artículo 1 - Objeto\n\nLa presente ley regula la publicación.\n\n"
        "Artículo 2 - Ámbito\n\nEsta ley se aplica a toda ley.\n\n"
        "TÍTULO II - De la publicación\n\n"
        "Artículo 3 - Publicación\n\nToda ley se publica en la Gaceta.\n"
    )
    anchors = scan_anchors(source, build_anchor_regex(config, "ley"), country="xq", doctype="ley")
    assert [a.kind for a in anchors].count("article") == 3
    assert [a.kind for a in anchors].count("part") == 2


def test_a_wrong_local_term_loses_the_provisions() -> None:
    """Why the previous test is the config check and the round-trip is not.

    Renaming the basic unit's `local_term` to something the source does not use
    drops every article, while the same source still round-trips through Bluebell
    unchanged, because `parse_to_akn` never consults the config.
    """
    config = load_config("xq")
    assert config is not None
    broken = config.model_copy(deep=True)
    broken.document_classes["ley"].hierarchy[1].local_term = "Section"
    source = (
        "TÍTULO I - Generales\n\nArtículo 1 - Objeto\n\nTexto.\n\nArtículo 2 - Ámbito\n\nTexto.\n"
    )
    anchors = scan_anchors(source, build_anchor_regex(broken, "ley"), country="xq", doctype="ley")
    assert [a.kind for a in anchors].count("article") == 0
