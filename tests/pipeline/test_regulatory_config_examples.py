from __future__ import annotations

import json
from pathlib import Path

import pytest

from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors
from codify.pipeline.enrich.enacting import select_formula
from codify.pipeline.stages import resolve_doctype

_EXAMPLES = sorted((Path(__file__).parents[1] / "fixtures" / "regulatory").glob("*/examples.json"))


def test_regulatory_examples_are_present() -> None:
    assert _EXAMPLES


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_regulatory_classification_examples(path: Path) -> None:
    examples = json.loads(path.read_text())
    cfg = load_config(examples["code"])
    for case in examples["classification"]:
        assert (
            resolve_doctype(
                cfg,
                title=case["title"],
                raw_date="",
                year=examples["year"],
                source=case["source"],
            )
            == case["expected"]
        ), case["title"]


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_regulatory_identity_and_outline(path: Path) -> None:
    examples = json.loads(path.read_text())
    cfg = load_config(examples["code"])
    kind = examples["instrument_class"]
    assert kind in cfg.document_classes
    uri = build_frbr_work_uri(cfg.code, kind, examples["year"], examples["number"])
    assert uri == examples["expected_uri"]
    assert uri != build_frbr_work_uri(cfg.code, "act", examples["year"], examples["number"])
    anchors = scan_anchors(
        examples["outline"],
        build_anchor_regex(cfg, kind),
        country=cfg.code,
        doctype=kind,
    )
    assert [[a.kind, a.number] for a in anchors] == examples["expected_anchors"]


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_synthetic_regulatory_outline(path: Path) -> None:
    examples = json.loads(path.read_text())
    held = examples["synthetic_source"]
    source = (path.parent / held["path"]).read_bytes()
    cfg = load_config(examples["code"])
    kind = examples["instrument_class"]
    anchors = scan_anchors(
        source.decode(),
        build_anchor_regex(cfg, kind),
        country=cfg.code,
        doctype=kind,
    )
    assert [a.number for a in anchors if a.kind == "section"] == held["sections"]
    assert [a.number for a in anchors if a.kind == "chapter"] == held["chapters"]


@pytest.mark.parametrize("path", _EXAMPLES, ids=lambda p: p.parent.name)
def test_regulatory_formula_scope(path: Path) -> None:
    examples = json.loads(path.read_text())
    for case in examples["formula_cases"]:
        formula = select_formula(examples["code"], case["class"], f"{examples['year']}-06-01")
        assert (formula is not None) == case["expected"], case["class"]
