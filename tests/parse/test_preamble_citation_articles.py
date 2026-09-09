"""The constitutional citation in an Indonesian enacting formula is not a provision.

Every blocking document in the Indonesian corpus was blocked by one phantom
article built from its own `Mengingat` recital. The scanner anchored on
`Pasal 5`, which landed an article at <body> root before BAB I and swallowed
`MEMUTUSKAN` into it.
"""

from __future__ import annotations

from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity

# UU 26/2007's opening, close to the stored page text: the citation opens its
# own line, which is why `sameline_precursors` cannot catch it.
PREAMBLE = (
    "Menimbang: bahwa ruang wilayah Negara Kesatuan Republik Indonesia;\n"
    "Mengingat:\n"
    "Pasal 5 ayat (1), Pasal 20, Pasal 25A, dan Pasal 33 ayat (3)\n"
    "Undang-Undang Dasar Negara Republik Indonesia Tahun 1945;\n"
    "MEMUTUSKAN:\n"
    "Menetapkan: UNDANG-UNDANG TENTANG PENATAAN RUANG.\n"
    "BAB I\n"
    "KETENTUAN UMUM\n"
    "Pasal 1\n"
    "Dalam Undang-Undang ini yang dimaksud dengan ruang adalah wadah.\n"
    "BAB II\n"
    "ASAS DAN TUJUAN\n"
    "Pasal 2\n"
    "Penataan ruang diselenggarakan berdasarkan asas keterpaduan.\n"
)


def _scan(text: str, country: str):
    return scan_anchors_with_ambiguity(
        text, cached_regex(country, "act"), country=country, doctype="act"
    )


def test_the_citation_before_the_first_chapter_is_not_anchored() -> None:
    scan = _scan(PREAMBLE, "id")
    articles = [a for a in scan.anchors if a.kind == "article"]
    assert [a.number for a in articles] == ["1", "2"]
    # The enacting formula stays ahead of every anchor, so the preamble split
    # that follows can see it.
    assert min(a.char_offset for a in scan.anchors) > PREAMBLE.index("MEMUTUSKAN")


def test_the_drop_is_recorded_as_resolved() -> None:
    scan = _scan(PREAMBLE, "id")
    dropped = [s for s in scan.ambiguity if s.emitted_by == "drop_preamble_citation_articles"]
    assert len(dropped) == 1
    assert dropped[0].resolved is True
    assert dropped[0].detail["reads_as"] == "preamble_citation"
    assert dropped[0].blocking is False


def test_an_amending_act_keeps_its_roman_pasal() -> None:
    """UU 32/2024's `Pasal I` carries the amendments and legitimately precedes a
    quoted container, so the pass declines for the whole document."""
    amending = (
        "Mengingat:\n"
        "Pasal 20, Pasal 21 Undang-Undang Dasar 1945;\n"
        "MEMUTUSKAN:\n"
        "Pasal I\n"
        "Beberapa ketentuan dalam Undang-Undang Nomor 5 Tahun 1990 diubah:\n"
        "BAB IX\n"
        "KETENTUAN PIDANA\n"
        "Pasal 40\n"
        "Setiap orang dilarang melakukan kegiatan yang mengakibatkan kerusakan.\n"
    )
    numbers = [a.number for a in _scan(amending, "id").anchors if a.kind == "article"]
    assert "I" in numbers
    assert "20" in numbers  # untouched: the pass declines document-wide


def test_a_document_with_no_container_keeps_its_root_articles() -> None:
    flat = (
        "MEMUTUSKAN:\n"
        "Pasal 1\n"
        "Dalam Peraturan ini yang dimaksud dengan Menteri adalah menteri.\n"
        "Pasal 2\n"
        "Peraturan ini mulai berlaku pada tanggal diundangkan.\n"
    )
    numbers = [a.number for a in _scan(flat, "id").anchors if a.kind == "article"]
    assert numbers == ["1", "2"]


def test_a_flat_body_survives_a_structured_annex() -> None:
    """A LAMPIRAN carries its own hierarchy. If the annex opens with BAB I and
    the body is flat, the annex's chapter must not be read as the body's first
    container, which would drop every real article ahead of it."""
    flat_body_structured_annex = (
        "Mengingat:\n"
        "Pasal 5 ayat (1) Undang-Undang Dasar 1945;\n"
        "MEMUTUSKAN:\n"
        "Pasal 1\n"
        "Dalam Peraturan Pemerintah ini yang dimaksud dengan Menteri adalah menteri.\n"
        "Pasal 2\n"
        "Peraturan Pemerintah ini mulai berlaku pada tanggal diundangkan.\n"
        "LAMPIRAN\n"
        "PEDOMAN TEKNIS\n"
        "BAB I\n"
        "UMUM\n"
        "Pasal 1\n"
        "Pedoman teknis ini menjadi acuan pelaksanaan.\n"
    )
    numbers = [
        a.number for a in _scan(flat_body_structured_annex, "id").anchors if a.kind == "article"
    ]
    assert "2" in numbers  # the flat body's own articles are untouched
    assert numbers.count("1") >= 1


def test_another_jurisdiction_is_unaffected() -> None:
    """The pass is config-gated, so a jurisdiction that has not declared the
    convention keeps whatever it anchored before."""
    scan = _scan(PREAMBLE, "al")
    assert not [s for s in scan.ambiguity if s.emitted_by == "drop_preamble_citation_articles"]
