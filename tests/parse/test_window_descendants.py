"""Body-fill windows expose the leaves that own their source text."""

from __future__ import annotations

import re
from typing import Any, cast

import pytest
from lxml import etree

from codify.core.llm import LLMClient
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors, windows_from_anchors
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.scaffold import BodyBlock, BodyFillResponse
from codify.pipeline.enrich.structure import _scaffold_for_window, text_to_bluebell_scaffolded

SOURCE = """Article 1
Paragraph 1
Point a
First explanation.
Point b
Second explanation.
Article 2
Other text.
"""


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    from codify.jurisdictions import JurisdictionConfig
    from codify.pipeline.enrich import structure

    config = JurisdictionConfig.model_validate(
        {
            "code": "xa",
            "name": "Synthetic",
            "tradition": ["civil_law"],
            "languages": ["eng"],
            "document_classes": {
                "act": {
                    "label": "Act",
                    "hierarchy": [
                        {
                            "local_term": "Article",
                            "akn_element": "article",
                            "level": "basic",
                            "bluebell_keyword": "ARTICLE",
                            "numbering": "arabic_continuous",
                        },
                        {
                            "local_term": "Paragraph",
                            "akn_element": "paragraph",
                            "level": "subdivision",
                            "bluebell_keyword": "PARAGRAPH",
                            "numbering": "arabic_continuous",
                        },
                        {
                            "local_term": "Point",
                            "akn_element": "point",
                            "level": "subdivision",
                            "bluebell_keyword": "POINT",
                            "numbering": "alpha_lower_period",
                        },
                    ],
                }
            },
        }
    )
    monkeypatch.setattr(structure, "load_config", lambda _: config)


def test_window_prompt_includes_descendant_targets() -> None:
    anchors = scan_anchors(SOURCE, build_anchor_regex(None, ""))
    windows = windows_from_anchors(SOURCE, anchors)
    prompt = _scaffold_for_window(windows[0].anchors, {a.akn_eid: a for a in anchors})
    assert "eid=art_1__para_1__point_a\n" in prompt
    assert "eid=art_1__para_1__point_b\n" in prompt


@pytest.mark.asyncio
async def test_body_fill_attaches_explanations_to_exact_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    class Client:
        async def chat_schema(self, prompt: str, schema: Any, **kwargs: Any) -> BodyFillResponse:
            targets = set(re.findall(r"(?m)^\s*eid=(\S+)$", prompt))
            lines = {
                "art_1__para_1__point_a": ["First explanation."],
                "art_1__para_1__point_b": ["Second explanation."],
                "art_2": ["Other text."],
            }
            if "art_1__para_1__point_a" not in targets:
                lines["art_1__para_1"] = [
                    "Point a",
                    "First explanation.",
                    "Point b",
                    "Second explanation.",
                ]
            return BodyFillResponse(
                bodies=[
                    BodyBlock(eid=eid, lines=body) for eid, body in lines.items() if eid in targets
                ]
            )

    bluebell = await text_to_bluebell_scaffolded(
        SOURCE, client=cast(LLMClient, Client()), country=""
    )
    xml = parse_to_akn(bluebell, country="xa", doctype="act", date="2026-01-01", number="1")
    root = etree.fromstring(xml.encode())
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    for letter, expected in [("a", "First explanation."), ("b", "Second explanation.")]:
        points = root.xpath(f'//a:point[@eId="art_1__para_1__point_{letter}"]', namespaces=ns)
        assert len(points) == 1
        assert " ".join(points[0].xpath(".//a:p/text()", namespaces=ns)) == expected
        assert " ".join(root.itertext()).count(expected) == 1
    intro = " ".join(root.xpath("//a:paragraph/a:intro//text()", namespaces=ns))
    assert "Point a" not in intro and "Point b" not in intro


def test_descendants_stay_with_owned_span_and_preserve_group_bounds() -> None:
    from dataclasses import replace

    from codify.pipeline.enrich.anchors import StructuralAnchor

    def anchor(kind: str, eid: str, offset: int, parent: str | None = None) -> StructuralAnchor:
        return StructuralAnchor(
            kind=kind,
            keyword=kind.upper(),
            number="1",
            char_offset=offset,
            line=1,
            matched_text=kind,
            akn_eid=eid,
            parent_eid=parent,
        )

    roots = [anchor("article", f"art_{i}", i * 100) for i in range(6)]
    before = anchor("point", "before_boundary", 299, "art_2")
    boundary = anchor("point", "at_boundary", 300, "art_3")
    nested = anchor("indent", "nested", 301, "at_boundary")
    quoted = replace(anchor("point", "quoted", 302, "art_3"), quoted_amendment=True)
    unrelated = anchor("point", "unrelated", 303, "missing")
    carried = anchor("point", "earlier_ancestor", 304, "art_2")
    anchors = sorted(
        [*roots, before, boundary, nested, quoted, unrelated, carried], key=lambda a: a.char_offset
    )
    windows = windows_from_anchors("X" * 650, anchors, max_per_window=3, overlap=100)
    baseline = windows_from_anchors("X" * 650, roots, max_per_window=3, overlap=100)
    assert [(w.text, w.body_start, w.body_end) for w in windows] == [
        (w.text, w.body_start, w.body_end) for w in baseline
    ]
    assert [[a.akn_eid for a in w.anchors] for w in windows] == [
        ["art_0", "art_1", "art_2", "before_boundary"],
        ["art_3", "at_boundary", "nested", "earlier_ancestor", "art_4", "art_5"],
    ]
    assert len(windows[0].text) > windows[0].body_end
    assert windows[1].body_start > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("recover_child", [False, True])
async def test_context_ancestor_cannot_overwrite_owner_body(
    monkeypatch: pytest.MonkeyPatch, recover_child: bool
) -> None:
    from codify.pipeline.enrich import structure

    _configure(monkeypatch)
    original_windows = windows_from_anchors

    def small_windows(text: str, anchors: Any, **kwargs: Any) -> Any:
        return original_windows(
            text, anchors, **({"max_per_window": 3, "min_per_window": 1} | kwargs)
        )

    monkeypatch.setattr(structure, "windows_from_anchors", small_windows)
    source = """Article 1
Correct parent text.
Paragraph 1
First paragraph.
Paragraph 2
Second paragraph.
Paragraph 3
Third paragraph.
Point a
Child explanation.
"""

    class Client:
        child_calls = 0

        async def chat_schema(self, prompt: str, schema: Any, **kwargs: Any) -> BodyFillResponse:
            source_part = prompt.split("</scaffold>", 1)[1]
            if "Article 1" in source_part:
                return BodyFillResponse(
                    bodies=[
                        BodyBlock(eid="art_1", lines=["Correct parent text."]),
                        BodyBlock(eid="art_1__para_1", lines=["First paragraph."]),
                        BodyBlock(eid="art_1__para_2", lines=["Second paragraph."]),
                    ]
                )
            self.child_calls += 1
            bodies = [
                BodyBlock(eid="art_1", lines=["CORRUPTED CONTEXT ANCESTOR"]),
            ]
            if not recover_child or self.child_calls > 1:
                bodies.append(BodyBlock(eid="art_1__para_3", lines=["Third paragraph."]))
                bodies.append(BodyBlock(eid="art_1__para_3__point_a", lines=["Child explanation."]))
            return BodyFillResponse(bodies=bodies)

    client = Client()
    bluebell = await text_to_bluebell_scaffolded(source, client=cast(LLMClient, client), country="")
    xml = parse_to_akn(bluebell, country="xa", doctype="act", date="2026-01-01", number="1")
    root = etree.fromstring(xml.encode())
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    assert (
        root.xpath('string(//a:article[@eId="art_1"]/a:intro/a:p)', namespaces=ns)
        == "Correct parent text."
    )
    assert (
        root.xpath('string(//a:point[@eId="art_1__para_3__point_a"]/a:content/a:p)', namespaces=ns)
        == "Child explanation."
    )
    assert "CORRUPTED" not in xml
    assert client.child_calls == (2 if recover_child else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True])
async def test_short_missing_leaf_is_recovered_or_filled_from_its_source(
    monkeypatch: pytest.MonkeyPatch, recover: bool
) -> None:
    _configure(monkeypatch)
    source = """Article 1
Parent text.
Paragraph 1
Paragraph text.
Point a
Clear.
Point b
"""

    class Client:
        calls = 0

        async def chat_schema(self, prompt: str, schema: Any, **kwargs: Any) -> BodyFillResponse:
            self.calls += 1
            targets = set(re.findall(r"(?m)^\s*eid=(\S+)$", prompt))
            bodies = [
                BodyBlock(eid="art_1", lines=["Parent text."]),
                BodyBlock(eid="art_1__para_1", lines=["Paragraph text."]),
            ]
            if recover and self.calls > 1:
                bodies.append(BodyBlock(eid="art_1__para_1__point_a", lines=["Clear."]))
            return BodyFillResponse(bodies=[b for b in bodies if b.eid in targets])

    client = Client()
    bluebell = await text_to_bluebell_scaffolded(source, client=cast(LLMClient, client), country="")
    xml = parse_to_akn(bluebell, country="xa", doctype="act", date="2026-01-01", number="1")
    root = etree.fromstring(xml.encode())
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    assert (
        root.xpath('string(//a:point[@eId="art_1__para_1__point_a"]/a:content/a:p)', namespaces=ns)
        == "Clear."
    )
    assert root.xpath("string(//a:paragraph/a:intro/a:p)", namespaces=ns) == "Paragraph text."
    assert not root.xpath('//a:point[@eId="art_1__para_1__point_b"]//a:p', namespaces=ns)
    assert " ".join(root.itertext()).count("Clear.") == 1
    assert client.calls == (2 if recover else 3)


@pytest.mark.parametrize("own_body", ["", "Own.\n"])
def test_body_source_excludes_children_and_captured_heading(own_body: str) -> None:
    from codify.pipeline.enrich.anchors import StructuralAnchor
    from codify.pipeline.enrich.structure import _anchors_with_body_source

    heading = "A heading long enough to exceed the old threshold"
    text = (
        f"Section 1\n{heading}\n{own_body}Point a\n"
        "A child's explanation long enough to exceed the old threshold.\nPoint b\n"
    )
    anchors = [
        StructuralAnchor(
            kind="section",
            keyword="SECTION",
            number="1",
            char_offset=0,
            line=1,
            matched_text="Section 1",
            akn_eid="sec_1",
            heading=heading,
        ),
        StructuralAnchor(
            kind="point",
            keyword="POINT",
            number="a",
            char_offset=text.index("Point a"),
            line=3,
            matched_text="Point a",
            akn_eid="sec_1__point_a",
            parent_eid="sec_1",
        ),
        StructuralAnchor(
            kind="point",
            keyword="POINT",
            number="b",
            char_offset=text.index("Point b"),
            line=5,
            matched_text="Point b",
            akn_eid="sec_1__point_b",
            parent_eid="sec_1",
        ),
    ]
    expected = {"sec_1__point_a"} | ({"sec_1"} if own_body else set())
    assert _anchors_with_body_source(text, anchors) == expected


@pytest.mark.parametrize("prefix", ["", "\n", "\n  "])
def test_verbatim_keeps_inline_heading_after_marker_whitespace(prefix: str) -> None:
    from codify.pipeline.enrich.anchors import StructuralAnchor
    from codify.pipeline.enrich.verbatim import fill_bodies_verbatim

    marker = prefix + "Section 1"
    source = marker + " - Scope\nClear.\n"
    anchor = StructuralAnchor(
        kind="section",
        keyword="SECTION",
        number="1",
        char_offset=0,
        line=1,
        matched_text=marker,
        akn_eid="sec_1",
    )
    block = fill_bodies_verbatim(source, [anchor]).bodies[0]
    assert block.heading == "Scope"
    assert block.lines == ["Clear."]
