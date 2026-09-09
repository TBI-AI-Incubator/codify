"""A work URI splits into its parts whether or not it carries a subtype."""

from __future__ import annotations

from codify.akn.frbr import work_parts


def test_four_and_five_segment_shapes() -> None:
    assert work_parts("/akn/xq/act/2018/12") == ("xq", "act", None, "2018", "12")
    assert work_parts("/akn/xq/act/xpa/2018/12") == ("xq", "act", "xpa", "2018", "12")
    assert work_parts("https://host/akn/xq/act/2018/12/") == ("xq", "act", None, "2018", "12")


def test_other_shapes_are_not_guessed() -> None:
    assert work_parts("/akn/xq/act/2018") is None
    assert work_parts("/akn/xq/act/xpa/2018/12/main") is None
    assert work_parts("urn:x") is None
    assert work_parts("/akn/xq/act//2018/12") is None  # a malformed identity is not a subtype
