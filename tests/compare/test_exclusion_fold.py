"""The comparator judges each unit on its own text and folds only the descendants
the row rule keeps, in the vectors and in the prompt alike."""

from __future__ import annotations

from codify.akn import Article, Paragraph, Point
from codify.compare.aligner import Candidate
from codify.compare.prompts import build_user_prompt
from codify.compare.scaffold import is_excluded, provision_text, unit_excluded
from codify.jurisdictions import placeholder_markers_for

MARKER_TEXT = "[TIFF not transcribed: table-1.tif]"


def _paragraph() -> Paragraph:
    return Paragraph(
        akn_eid="art_1__p1",
        akn_type="paragraph",
        position=1,
        text="Copper wire shall bear the duty set out below:",
        children=[
            Point(akn_eid="art_1__p1__a", akn_type="point", position=1, text="3 % ad valorem."),
            Point(akn_eid="art_1__p1__b", akn_type="point", position=2, text=MARKER_TEXT),
            Point(
                akn_eid="art_1__p1__c", akn_type="point", position=3, text="| 7408 | wire | 3 % |"
            ),
        ],
    )


def test_fold_drops_excluded_descendants_and_keeps_the_parent() -> None:
    para = _paragraph()
    markers = placeholder_markers_for("/akn/xz/act/2024/1")
    assert not unit_excluded(para, markers)
    assert unit_excluded(para.children[1], markers)
    assert unit_excluded(para.children[2], markers)
    folded = provision_text(para, markers)
    assert "3 % ad valorem" in folded
    assert MARKER_TEXT not in folded
    assert "| 7408 |" not in folded
    # Without markers the fold is the historical whole, so callers must pass them.
    assert MARKER_TEXT in provision_text(para)


def test_prompt_shows_the_model_the_folded_text() -> None:
    para = _paragraph()
    markers = placeholder_markers_for("/akn/xz/act/2024/1")
    directive = Article(akn_eid="art_9", akn_type="article", position=1, text="Duties apply.")
    prompt = build_user_prompt(
        directive,
        [Candidate(provision=para, score=0.9, frbr="/akn/xz/act/2024/1/eng@2024")],
        domestic_frbr_uri="/akn/xz/act/2024/1/eng@2024",
        directive_markers=markers,
        domestic_markers=markers,
    )
    assert "3 % ad valorem" in prompt
    assert MARKER_TEXT not in prompt


def test_is_excluded_judges_a_unit_by_its_own_text() -> None:
    import uuid

    from codify.akn.document import Document

    para = _paragraph()
    doc = Document(
        id=uuid.uuid4(),
        frbr_work_uri="/akn/xz/act/2024/1",
        frbr_expression_uri="/akn/xz/act/2024/1/eng@2024",
        language="eng",
        expression_date="2024-01-01",
        body=[Article(akn_eid="art_1", akn_type="article", position=1, children=[para])],
    )
    assert not is_excluded(para, doc)
    assert is_excluded(para.children[1], doc)
