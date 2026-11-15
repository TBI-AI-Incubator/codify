"""Opening material and the enacting formula a source carries."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.akn import AKN_NS, NS
from codify.jurisdictions import JurisdictionConfigError
from codify.pipeline.enrich.bluebell import parse_to_akn


def _minimal_act() -> str:
    return f'''<?xml version="1.0"?>
<akomaNtoso xmlns="{AKN_NS}">
  <act contains="originalVersion">
    <meta><identification source="#codify"/></meta>
    <body><section eId="sec_1"><num>1.</num><content><p>Short title.</p></content></section></body>
  </act>
</akomaNtoso>
'''


class _NoModel:
    def __getattr__(self, name: str) -> object:
        raise RuntimeError("no model in this test")


async def _enrich(akn: str, country: str, doctype: str, raw_date: str) -> etree._Element:
    from codify.pipeline import stages

    desc = stages.Descriptors(
        title="Test",
        raw_date=raw_date,
        number="1",
        year=raw_date[:4],
        language="eng",
        doctype=doctype,
    )
    out = await stages.run_enrich_passes(
        akn,
        llm=_NoModel(),  # type: ignore[arg-type]
        jurisdiction_code=country,
        desc=desc,
        skip_external_refs=True,
    )
    return etree.fromstring(out.encode("utf-8"))


async def test_a_source_without_a_formula_gets_none() -> None:
    root = await _enrich(_minimal_act(), "xa", "act", "2024-06-01")
    assert root.find(".//akn:formula", NS) is None
    assert root.find(".//akn:preamble", NS) is None


async def test_a_formula_the_source_prints_is_still_tagged() -> None:
    akn = parse_to_akn(
        "PREAMBLE\n\n  Having regard to the Act,\n\n  HEREBY RESOLVES:\n\n"
        "BODY\n\nSECTION 1\n\n  A.\n",
        "xa",
        doctype="resolution",
        date="2024-03-05",
        number="4",
    )
    root = await _enrich(akn, "xa", "resolution", "2024-03-05")
    formula = root.find(".//akn:preamble/akn:formula[@name='enactingFormula']", NS)
    assert formula is not None
    assert "HEREBY RESOLVES" in "".join(formula.itertext())
    assert formula.get("refersTo") is None


def test_a_config_still_carrying_formulae_loads() -> None:
    """Deployed configs may keep the retired key until their data is cleaned."""
    from codify.jurisdictions import JurisdictionConfig, load_config

    raw = load_config("xa").model_dump(by_alias=True, exclude_unset=True)
    raw["enacting_formulae"] = [{"text": "Be it enacted."}]
    assert JurisdictionConfig.model_validate(raw).code == "xa"


class TestSplitOpeningMaterial:
    def test_recital_chain_splits_to_preamble(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        text = (
            "قانون رقم (7) لسنة 1999م بشأن المراعي\n"
            "رئيس جمهورية زرزورة\n"
            "بعد الاطلاع على القانون الأساسي،\n"
            "وبناءً على تنسيب مجلس الوزراء،\n"
            "أصدرنا القانون الآتي:\n"
        )
        preface, preamble = split_opening_material(text, "xz")
        assert preface is not None and "بشأن المراعي" in preface
        assert "رئيس جمهورية" in preface
        assert preamble is not None and preamble.startswith("بعد الاطلاع")
        assert "أصدرنا القانون الآتي" in preamble

    def test_no_opener_stays_preface(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        preface, preamble = split_opening_material("قانون رقم 5 لسنة 2000\n", "xz")
        assert preface is not None and preamble is None

    def test_empty(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        assert split_opening_material("  \n ", "xz") == (None, None)


class TestOpeningMaterialTerminator:
    """The truncation cut is `opening_material_terminators`, not
    `enacting_formula_markers`. An instrument that opens on an invocation lists
    it as formula evidence, and cutting there deletes the recitals."""

    PERBUP = (
        "BUPATI TANJUNG SERI\n"
        "PERATURAN BUPATI TANJUNG SERI NOMOR 7 TAHUN 2031\n"
        "DENGAN RAHMAT TUHAN YANG MAHA KUASA\n"
        "BUPATI TANJUNG SERI,\n"
        "Menimbang : a. bahwa berdasarkan ketentuan Pasal 34 Peraturan Menteri;\n"
        "Mengingat : 1. Undang-Undang Nomor 9 Tahun 2011;\n"
        "MEMUTUSKAN:\n"
        "Menetapkan : PERATURAN BUPATI TENTANG PERUBAHAN ATAS PERATURAN BUPATI.\n"
    )

    def test_invocation_does_not_truncate_the_recitals(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        preface, preamble = split_opening_material(self.PERBUP, "xl")
        both = f"{preface or ''}\n{preamble or ''}"
        assert "Menimbang" in both
        assert "Mengingat" in both
        assert "Menetapkan" in both

    def test_invocation_is_still_formula_evidence(self):
        """It stays in `enacting_formula_markers`, which marks where the preamble
        opens; the two fields answer different questions."""
        from codify.jurisdictions import load_config

        cfg = load_config("xl")
        assert "DENGAN RAHMAT TUHAN YANG MAHA KUASA" in cfg.enacting_formula_markers
        assert "DENGAN RAHMAT TUHAN YANG MAHA KUASA" not in cfg.opening_material_terminators

    def test_no_terminator_declared_means_no_cut(self):
        from codify.pipeline.enrich.enacting import _truncate_after_enacting_formula

        lines = ["TITLE", "recital", "tail"]
        assert _truncate_after_enacting_formula(lines, [], "xl") == lines


class TestFurnitureNeverBecomesOpeningMaterial:
    """Gazette furniture is dropped before the preface/preamble split, so it
    never reaches a paragraph slot."""

    def test_masthead_and_page_numbers_dropped_recitals_kept(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        text = (
            "الوقائع الزرزورية\n"
            "قانون رقم (3) لسنة 2000م بشأن الآبار\n"
            "صفحة 12\n"
            "بعد الاطلاع على القانون الأساسي،\n"
            "صفحة 13\n"
            "وبناءً على تنسيب مجلس الوزراء،\n"
            "أصدرنا القانون الآتي:\n"
        )
        preface, preamble = split_opening_material(text, "xz")
        assert preface is not None and preamble is not None
        both = f"{preface}\n{preamble}"
        assert "الوقائع الزرزورية" not in both
        assert "صفحة 12" not in both and "صفحة 13" not in both
        # The recitals interleaved with the footers survive, in order.
        assert preamble.startswith("بعد الاطلاع")
        assert "وبناءً على تنسيب" in preamble
        assert "أصدرنا القانون الآتي" in preamble

    def test_law_title_is_not_treated_as_furniture(self):
        # The running-title pattern in `ocr_header_patterns` matches the law's
        # own title, which is why whole-line disposal reads the narrower
        # `furniture_line_patterns` list instead. Dropping this line would
        # delete the document's title from its preface.
        from codify.pipeline.enrich.enacting import split_opening_material

        text = "قانون رقم (7) لسنة 1999 بشأن المراعي\nبعد الاطلاع على القانون الأساسي،\n"
        preface, _preamble = split_opening_material(text, "xz")
        assert preface is not None and "بشأن المراعي" in preface

    def test_partial_match_keeps_the_rest_of_the_line(self):
        # Provenance sharing a line with the masthead is retained: only the
        # matched phrase goes, not the issue number and year.
        from codify.pipeline.enrich.enacting import split_opening_material

        text = "الوقائع الزرزورية - العدد 45 - 2016\nبعد الاطلاع على القانون الأساسي،\n"
        preface, _preamble = split_opening_material(text, "xz")
        assert preface is not None
        assert "45" in preface and "2016" in preface
        assert "الوقائع الزرزورية" not in preface

    def test_all_furniture_opening_material_yields_nothing(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        assert split_opening_material("الوقائع الزرزورية\nصفحة 3\n", "xz") == (
            None,
            None,
        )

    def test_a_jurisdiction_declaring_no_patterns_is_untouched(self):
        """Declaring no furniture patterns is a real configuration, and the text
        passes through. Distinct from having no config at all, which raises."""
        from codify.pipeline.enrich.enacting import split_opening_material

        text = "Some Act 2020\nHaving regard to nothing,\n"
        preface, _preamble = split_opening_material(text, "xa")
        assert preface is not None and "Some Act 2020" in preface

    def test_an_absent_config_raises_rather_than_passing_text_through(self):
        from codify.pipeline.enrich.enacting import split_opening_material

        with pytest.raises(JurisdictionConfigError, match="zz"):
            split_opening_material("Some Act 2020\n", "zz")

    def test_the_bidi_mangled_form_of_the_title_is_not_furniture(self):
        # Arabic OCR can emit the law's own title with its tokens in visual order.
        # That form matches a pattern kept for mid-paragraph scrubbing, and
        # promoting it to whole-line disposal deleted the statute's title.
        from codify.pipeline.enrich.enacting import split_opening_material

        mangled = "7 بشأن المراعي 1999 ) لسنة 7 ) قانون رقم"
        preface, _preamble = split_opening_material(
            f"{mangled}\nبعد الاطلاع على القانون الأساسي،\n", "xz"
        )
        assert preface is not None and mangled in preface


def test_opening_material_stops_at_the_enacting_formula() -> None:
    """Synthetic page furniture after a formula must not enter the preamble."""
    from codify.pipeline.enrich.enacting import split_opening_material

    text = "\n".join(
        [
            "قانون المرصد رقم ٤١ لسنة ٢٠٤٢",
            "بعد الإطلاع على نظام الأدوات التجريبية لسنة ٢٠٤٠،",
            "وبعد إقرار مجلس الأمة،",
            "أصدرنا القانون التالي:",
            "-٥-",
            "12062042",
            "00417/000",
            "يونيه ٢٠٤٢",
        ]
    )
    preface, preamble = split_opening_material(text, "xz")
    assert preamble is not None
    assert preamble.strip().endswith("أصدرنا القانون التالي:")
    assert "بعد الإطلاع" in preamble
    for furniture in ("-٥-", "12062042", "00417/000", "يونيه"):
        assert furniture not in preamble
        assert furniture not in (preface or "")


def test_opening_material_without_a_formula_is_not_truncated() -> None:
    """Without a formula marker, preserve the supplied opening text."""
    from codify.pipeline.enrich.enacting import split_opening_material

    text = "نظام\nبعد الإطلاع على شيء،\nسطر أخير"
    _, preamble = split_opening_material(text, "xz")
    assert preamble is not None
    assert preamble.strip().endswith("سطر أخير")


def test_truncation_declines_when_the_tail_carries_provisions() -> None:
    """If the first article anchor was missed, real articles sit in the tail.
    The guard reads source OCR, so it must know `مادة (١)`, not `ARTICLE 1`."""
    from codify.pipeline.enrich.enacting import split_opening_material

    text = "\n".join(
        [
            "قانون رقم ٣ لسنة ٢٠٠٠",
            "بعد الإطلاع على القانون الأساسي،",
            "أصدرنا القانون التالي:",
            "مادة (١)",
            "يعمل بأحكام هذا القانون اعتباراً من تاريخ نشره.",
        ]
    )
    _, preamble = split_opening_material(text, "xz")
    assert preamble is not None
    assert "يعمل بأحكام هذا القانون" in preamble


def test_truncation_cuts_at_the_first_formula_not_a_repeated_one() -> None:
    """A bled-in page repeats the formula; cutting at the later one keeps the
    furniture between the two."""
    from codify.pipeline.enrich.enacting import split_opening_material

    text = "\n".join(
        [
            "قانون المرصد رقم ٤١ لسنة ٢٠٤٢",
            "بعد الإطلاع على القانون الأساسي،",
            "أصدرنا القانون التالي:",
            "-٥-",
            "00417/000",
            "قانون المرصد رقم ٤١ لسنة ٢٠٤٢",
            "أصدرنا القانون التالي:",
        ]
    )
    _, preamble = split_opening_material(text, "xz")
    assert preamble is not None
    assert preamble.strip().endswith("أصدرنا القانون التالي:")
    assert "-٥-" not in preamble
    assert "00417/000" not in preamble


def test_truncation_declines_when_recitals_follow_the_formula() -> None:
    """A preamble quoting a formula early must keep the recitals that follow."""
    from codify.pipeline.enrich.enacting import split_opening_material

    text = "\n".join(
        [
            "قانون رقم ٣ لسنة ٢٠٠٠",
            "أصدرنا القانون التالي:",
            "وبناء على ما تقدم من مشروع القانون،",
        ]
    )
    _, preamble = split_opening_material(text, "xz")
    assert preamble is not None
    assert "وبناء على ما تقدم" in preamble


def test_document_with_no_preamble_marker_is_all_preface() -> None:
    """No opener and no formula: nothing cut, nothing becomes a preamble."""
    from codify.pipeline.enrich.enacting import split_opening_material

    preface, preamble = split_opening_material("ARBITRATION REGULATION\nSome title line", "xz")
    assert preamble is None
    assert preface == "ARBITRATION REGULATION\nSome title line"
