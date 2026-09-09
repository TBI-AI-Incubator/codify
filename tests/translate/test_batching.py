"""Fixture-driven tests for chapter-locality batching."""

from __future__ import annotations

from codify.translate.anchors import SourceUnit
from codify.translate.batching import group_by_chapter


def _u(eid: str, kind: str, depth: int = 1, heading: str | None = None) -> SourceUnit:
    return SourceUnit(
        akn_eid=eid,
        kind=kind,
        akn_type=kind,
        number=None,
        depth=depth,
        heading=heading,
        body_text="body of " + eid,
    )


class TestGroupByChapter:
    def test_single_chapter_small_act_yields_one_group_one_batch(self) -> None:
        units = [
            _u("chp_1", "chapter", heading="General"),
            _u("art_1", "article"),
            _u("art_2", "article"),
        ]
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 1
        assert len(groups[0].batches) == 1
        assert groups[0].label.startswith("Chapter")
        assert "General" in groups[0].label

    def test_single_chapter_oversized_splits_within_chapter(self) -> None:
        units = [_u("chp_1", "chapter", heading="Big")] + [
            _u(f"art_{i}", "article") for i in range(1, 11)
        ]
        groups = group_by_chapter(units, batch_size=4)
        assert len(groups) == 1
        # 11 total (chapter + 10 articles) split by 4 → batches of 4, 4, 3.
        assert [len(b) for b in groups[0].batches] == [4, 4, 3]

    def test_multi_chapter_mix_yields_one_group_per_chapter(self) -> None:
        units = [
            _u("chp_1", "chapter", heading="One"),
            _u("art_1", "article"),
            _u("art_2", "article"),
            _u("chp_2", "chapter", heading="Two"),
            _u("art_3", "article"),
        ]
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 2
        assert "One" in groups[0].label
        assert "Two" in groups[1].label

    def test_flat_act_no_chapters_yields_single_empty_labelled_group(self) -> None:
        units = [
            _u("art_1", "article"),
            _u("art_2", "article"),
            _u("art_3", "article"),
        ]
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 1
        assert groups[0].label == ""
        assert len(groups[0].batches[0]) == 3

    def test_preamble_units_before_first_chapter_land_in_synthetic_group(self) -> None:
        units = [
            _u("preface_art_1", "article"),
            _u("chp_1", "chapter", heading="Actual chapter"),
            _u("art_1", "article"),
        ]
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 2
        # Preamble group has empty label.
        assert groups[0].label == ""
        assert groups[0].units[0].akn_eid == "preface_art_1"
        assert "Actual chapter" in groups[1].label

    def test_unbalanced_giant_chapter_plus_tiny_ones(self) -> None:
        units = (
            [_u("chp_1", "chapter", heading="Giant")]
            + [_u(f"art_{i}", "article") for i in range(1, 41)]
            + [
                _u("chp_2", "chapter", heading="Small"),
                _u("art_41", "article"),
            ]
        )
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 2
        # Giant chapter: chapter + 40 articles = 41 units, batched by 8 = 6 batches.
        assert len(groups[0].batches) == 6
        assert sum(len(b) for b in groups[0].batches) == 41
        # Small chapter: chapter + 1 article = 2 units, 1 batch.
        assert len(groups[1].batches) == 1

    def test_every_unit_lands_in_exactly_one_batch(self) -> None:
        units = [
            _u("chp_1", "chapter", heading="X"),
            _u("art_1", "article"),
            _u("part_1", "part", heading="Y"),
            _u("art_2", "article"),
            _u("art_3", "article"),
        ]
        groups = group_by_chapter(units, batch_size=3)
        seen: list[str] = []
        for g in groups:
            for b in g.batches:
                seen.extend(u.akn_eid for u in b)
        assert sorted(seen) == sorted(u.akn_eid for u in units)
        assert len(seen) == len(units)

    def test_civil_code_hierarchy_book_title_part(self) -> None:
        # Books count as chapter-tier containers.
        units = [
            _u("bk_1", "book", heading="Property"),
            _u("art_1", "article"),
            _u("tit_1", "title", heading="Possession"),
            _u("art_2", "article"),
        ]
        groups = group_by_chapter(units, batch_size=8)
        assert len(groups) == 2
        assert "Property" in groups[0].label
        assert "Possession" in groups[1].label

    def test_zero_batch_size_raises(self) -> None:
        try:
            group_by_chapter([], batch_size=0)
        except ValueError:
            return
        raise AssertionError("expected ValueError")
