from __future__ import annotations

import json
from pathlib import Path

import pytest
from lxml import etree

from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors_with_ambiguity
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.scaffold import scaffold_from_anchors
from codify.pipeline.stages import resolve_doctype

_EXAMPLES = sorted(
    (Path(__file__).parents[1] / "fixtures" / "regulatory").glob("*/joint-example.json")
)
_COUNTS = [4, 2, 7, 5, 3, 1, 9, 4, 20, 4, 8, 4, 7, 6, 4, 4]
_RULES = "I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI".split()


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_joint_issuer_identity_is_narrow(path: Path) -> None:
    example = json.loads(path.read_text())
    cfg = load_config(example["code"])
    for case in example["classification"]:
        assert (
            resolve_doctype(
                cfg, title=case["title"], raw_date="", year=example["year"], source=case["source"]
            )
            == case["expected"]
        )
    uri = build_frbr_work_uri(
        cfg.code, example["instrument_class"], example["year"], example["number"]
    )
    assert uri == example["expected_uri"]
    assert uri != build_frbr_work_uri(cfg.code, "act", example["year"], example["number"])


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_rule_scoped_sections_survive_anchor_scaffold_and_akn(path: Path) -> None:
    example = json.loads(path.read_text())
    cfg = load_config(example["code"])
    kind = example["instrument_class"]
    chunks = ["Synthetic outline. This is not legislation."]
    for rule, count in zip(_RULES, _COUNTS, strict=True):
        chunks.append(f"RULE {rule}\nEXAMPLE DIVISION\n")
        for number in range(1, count + 1):
            chunks.append(f"SECTION {number}. Example heading. Example text.\n")
    source = "\n".join(chunks)
    scan = scan_anchors_with_ambiguity(
        source, build_anchor_regex(cfg, kind), country=cfg.code, doctype=kind
    )
    sections = [a for a in scan.anchors if a.kind == "section"]
    rules = [a for a in scan.anchors if a.kind == "chapter"]
    assert len(rules) == 16
    assert len(sections) == 92
    assert len({a.akn_eid for a in sections}) == 92
    for rule, count in zip(rules, _COUNTS, strict=True):
        children = [a for a in sections if a.parent_eid == rule.akn_eid]
        assert [a.number for a in children] == [str(n) for n in range(1, count + 1)]
    scaffold, index = scaffold_from_anchors(scan.anchors)
    assert len(index) == 108
    xml = parse_to_akn(scaffold, cfg.code, date=example["year"], number=example["number"])
    root = etree.fromstring(xml.encode())
    ns = {"a": root.nsmap[None]}
    chapters = root.xpath("//a:body/a:chapter", namespaces=ns)
    assert len(chapters) == 16
    assert [len(chapter.findall("a:section", ns)) for chapter in chapters] == _COUNTS
    parsed_sections = root.xpath("//a:section", namespaces=ns)
    assert {section.get("eId") for section in parsed_sections} == {a.akn_eid for a in sections}
