"""Which grouping a duplicate was decided in.

`_drop_toc_duplicates` keys on the ancestor chain, so it already knows. Without that
on the span a reader sees only a number, and one number in two containers reads as one
unit seen twice: a complete chapter then accounts for the units a cut chapter lacks.

Nested two levels, because the pass compares the whole chain and a single level would
pass against a rule that only looked at the nearest parent.
"""

from __future__ import annotations

from codify.pipeline.enrich.anchors import cached_regex, scan_anchors_with_ambiguity

COUNTRY, DOCTYPE = "xz", "act"


def _twins(text: str) -> list[dict]:
    scan = scan_anchors_with_ambiguity(
        text, cached_regex(COUNTRY, DOCTYPE), country=COUNTRY, doctype=DOCTYPE
    )
    return [sp.detail for sp in scan.ambiguity if sp.kind == "duplicate_number"]


def _document(parts: int, chapters: int, articles: int) -> str:
    """A contents listing then a body, both carrying the same nested numbering, so
    every unit twins and the only thing telling two twins apart is where they sit."""

    def units() -> list[str]:
        out: list[str] = []
        for p in range(1, parts + 1):
            out.append(f"Part {p}. Part heading {p}")
            for c in range(1, chapters + 1):
                out.append(f"Chapter {c}. Chapter heading {p}.{c}")
                out += [f"Article {a}. Heading {p}.{c}.{a}" for a in range(1, articles + 1)]
        return out

    lines = ["AN ACT OF THE ASSEMBLY", "", "TABLE OF CONTENTS", "", *units(), "", "BODY", ""]
    for line in units():
        lines += [line, "  Body text.", ""]
    return "\n".join(lines) + "\n"


def _articles(details: list[dict]) -> list[tuple[str, tuple]]:
    return [
        (d["number"], tuple(tuple(step) for step in d["container"]))
        for d in details
        if d["kind"] == "article"
    ]


def test_one_number_in_two_containers_is_two_records() -> None:
    """The case a number alone cannot express."""
    articles = _articles(_twins(_document(parts=1, chapters=2, articles=1)))
    assert len(articles) == 2
    assert articles[0][0] == articles[1][0], "the fixture no longer repeats a number"
    assert articles[0][1] != articles[1][1], "two containers recorded the same way"


def test_the_container_carries_every_level_it_sits_under() -> None:
    """Two levels, because the pass keys on the whole chain: a rule reading only the
    nearest parent would call these two the same grouping."""
    articles = _articles(_twins(_document(parts=2, chapters=1, articles=1)))
    assert len(articles) == 2
    first, second = articles[0][1], articles[1][1]
    assert first != second
    assert [step[0] for step in first] == ["part", "chapter"]
    assert first[0][1] != second[0][1], "the chains differ only at the part, and must"


def test_a_top_level_unit_records_an_empty_container() -> None:
    """An empty chain is the document root, not an absent answer."""
    text = "AN ACT\n\nTABLE OF CONTENTS\n\nArticle 1. A\nArticle 2. B\n\nBODY\n\n"
    text += "Article 1. A\n  Body.\n\nArticle 2. B\n  Body.\n"
    assert all(d["container"] == [] for d in _twins(text))


def test_the_container_is_json_shaped() -> None:
    """The span is stored and read back, so the value has to survive a round trip."""
    import json

    details = _twins(_document(parts=1, chapters=2, articles=1))
    assert details
    assert json.loads(json.dumps([d["container"] for d in details]))
