"""The bundle writer's own helpers.

`ingest-one` exists so a reader is not misled about what an ingest did, which
makes a wrong artifact worse here than a missing one. These cover the pure
helpers; the pipeline itself is covered in tests/parse.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from codify.cli import _by_pass, _clear_bundle, _coverage_json, _foundry_ocr_url
from codify.core.llm import LiteLLMClient
from codify.pipeline.enrich.anchors import AnchorCoverage, StructuralAnchor
from codify.pipeline.enrich.structure import ScanTrace


def _trace(coverage: AnchorCoverage | None) -> ScanTrace:
    return ScanTrace(anchors=(), coverage=coverage, scaffold=None, fallback=None)


def test_an_unmeasurable_ratio_serialises_rather_than_raising() -> None:
    """The zero-anchor document is what the bundle is for, and it is the one
    whose ratio is None. Rounding it would kill the write after the model spend."""
    cov = AnchorCoverage(kind="article", ratio=None, captured=frozenset(), expected=frozenset())
    assert _coverage_json(_trace(cov)) == {
        "kind": "article",
        "ratio": None,
        "captured": [],
        "expected": [],
        "missing": [],
        "masked": 0,
        "unclosed": 0,
    }


def test_a_measured_ratio_reports_the_numbers_it_missed() -> None:
    cov = AnchorCoverage(
        kind="article",
        ratio=0.5,
        captured=frozenset({"1", "2"}),
        expected=frozenset({"1", "2", "3", "4"}),
    )
    out = _coverage_json(_trace(cov))
    assert out is not None
    assert out["ratio"] == 0.5
    assert out["missing"] == ["3", "4"]


def test_a_gate_that_did_not_run_reports_no_coverage_block() -> None:
    assert _coverage_json(_trace(None)) is None


def test_a_reused_directory_keeps_nothing_from_the_previous_run(tmp_path: Path) -> None:
    """A shorter or failed second run would otherwise present the first run's
    scaffold, AKN and page images as its own."""
    pages = tmp_path / "pages"
    pages.mkdir()
    written = [
        tmp_path / "manifest.json",
        tmp_path / "final.akn.xml",
        tmp_path / "scaffold.bluebell",
        tmp_path / "coverage.json",
        pages / "page-001.png",
        pages / "page-002.png",
    ]
    for f in written:
        f.write_text("stale")
    _clear_bundle(tmp_path)
    assert not [f for f in written if f.exists()]


def test_every_file_the_bundle_writes_is_also_cleared(tmp_path: Path) -> None:
    """A name the writer adds but the tuple omits survives a re-run."""
    from codify.cli import _BUNDLE_FILES

    for name in _BUNDLE_FILES:
        (tmp_path / name).write_text("stale")
    _clear_bundle(tmp_path)
    assert not [n for n in _BUNDLE_FILES if (tmp_path / n).exists()]
    assert "ambiguity.jsonl" in _BUNDLE_FILES


def test_clearing_a_bundle_leaves_everything_else_alone(tmp_path: Path) -> None:
    """The output directory is the operator's choice, so a broader delete would
    take their own files with it."""
    keep = tmp_path / "notes.md"
    keep.write_text("mine")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "reference.jpg").write_text("mine")
    _clear_bundle(tmp_path)
    assert keep.read_text() == "mine"
    assert (tmp_path / "pages" / "reference.jpg").exists()


def test_the_foundry_url_drops_the_gateway_path_suffix(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The endpoint env var carries a LiteLLM path; keeping it would send OCR to
    a 404 and every page would fall back, reading as an OCR-quality problem."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.services.ai.azure.com/api/p/openai/v1")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    assert _foundry_ocr_url() == "https://x.services.ai.azure.com/providers/mistral/azure/ocr"


def test_no_foundry_url_without_both_credentials(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.services.ai.azure.com")
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert _foundry_ocr_url() is None
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    assert _foundry_ocr_url() is None


def test_an_anchor_with_no_recorded_pass_reads_as_unattributed() -> None:
    """`anchors_by_pass` is where an attribution regression becomes visible."""
    anchor = StructuralAnchor(
        kind="article", keyword="مادة", number="1", char_offset=0, line=1, matched_text="مادة 1"
    )
    assert _by_pass((anchor,)) == {"unattributed": 1}
    from dataclasses import replace

    assert _by_pass((replace(anchor, source_pass="regex"),)) == {"regex": 1}


@pytest.fixture
def _restore_structlog():
    """structlog's configuration is global and `main` repoints it at a
    `sys.stderr` pytest later closes."""
    import structlog

    saved = structlog.get_config().copy()
    yield
    structlog.configure(**saved)


def test_each_subcommand_routes_to_its_own_handler(
    monkeypatch, tmp_path: Path, _restore_structlog: None
) -> None:
    """The dispatch table is per-parser defaults: a subcommand added without
    `set_defaults` parses cleanly and dies on `args.func`."""
    from codify import cli

    fired: list[str] = []

    async def _fake_run(args: object) -> int:
        fired.append("ingest-one")
        return 0

    # `_run` stays a coroutine so the real `asyncio.run` is used; patching that
    # globally leaks into every async test that runs after this one.
    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "_run_scan_corpus", lambda a: fired.append("scan-corpus") or 0)

    cli.main(["scan-corpus", str(tmp_path), "--jurisdiction", "ps"])
    cli.main(["ingest-one", "x.txt", "--jurisdiction", "ps", "--out", str(tmp_path)])
    assert fired == ["scan-corpus", "ingest-one"]


def test_a_mistargeted_root_exits_non_zero_without_printing(
    capsys, tmp_path: Path, _restore_structlog: None
) -> None:
    """A redirect keeps the file whatever the exit code says."""
    from codify.cli import main

    assert main(["scan-corpus", str(tmp_path), "--jurisdiction", "ps"]) == 1
    assert capsys.readouterr().out == ""
    assert main(["scan-corpus", str(tmp_path / "nope.txt"), "--jurisdiction", "ps"]) == 2


def test_the_structuring_pass_asks_for_the_counts_the_bundle_writes() -> None:
    """The artifact reads whatever that pass measured, so a field can be wired
    end to end and still always zero if the pass never asks for it. That is
    what it did: the gate saw two hidden markers where the bundle wrote none.

    Asserted on the call rather than through the pass, which needs a model.
    """
    import inspect

    from codify.jurisdictions import load_config
    from codify.pipeline.enrich import structure as structure_mod
    from codify.pipeline.enrich.anchors import (
        anchor_coverage,
        build_anchor_regex,
        scan_anchors,
    )

    source = inspect.getsource(structure_mod)
    assert "anchor_coverage(text, anchors, config, doctype, kind, with_masked=True)" in source

    config = load_config("xl")
    body = "".join(f"Pasal {i}\n(1) Ketentuan nomor {i}.\n" for i in range(1, 21))
    text = body + "Pasal 21\n(1) Satu dengan “kutip hilang.\n(2) Dua.\n(3) Tiga.\n"
    anchors = scan_anchors(text, build_anchor_regex(config, "act"), country="xl", doctype="act")

    # Off by default, so nothing pays for a count it does not read.
    assert anchor_coverage(text, anchors, config, "act", "article").masked == 0

    coverage = anchor_coverage(text, anchors, config, "act", "article", with_masked=True)
    written = _coverage_json(_trace(coverage))
    assert written is not None
    assert written["masked"] == 2, written
    assert written["unclosed"] == 1, written


async def test_the_client_is_built_off_the_proxy(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Proxy attribution rides the request body as `metadata`, which a provider's
    own OpenAI-compatible endpoint rejects with a 400; the CLI has no run to
    attribute anyway."""
    from codify import cli

    seen: dict[str, object] = {}

    class _Stop(Exception):
        pass

    def _capture(**kwargs: object) -> None:
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(cli, "create_llm_client", _capture)
    source = tmp_path / "act.txt"
    source.write_text("1. Short title.\n")
    args = argparse.Namespace(
        source=str(source),
        jurisdiction="xa",
        out=str(tmp_path / "bundle"),
        model="m",
        ocr_model="",
        quiet=True,
    )
    with pytest.raises(_Stop):
        await cli._run(args)
    assert seen["telemetry_mode"] == "direct"
    # The mode is only worth pinning because of what it drops from the request.
    direct = LiteLLMClient(base_url="http://x/v1", api_key="k", model="m", telemetry_mode="direct")
    assert direct._body() == {}
