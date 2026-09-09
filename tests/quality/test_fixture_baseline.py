"""The committed fixture baseline gates scanner drift.

Five committed synthetic documents in the Arabic (Zerzura) scanner shapes: one a
full synthetic law, four written to exercise illegible, mangled-keyword,
marker-form and table-of-contents cases. It catches a change in the scanner, not
a change in any corpus, and carries no real legislation.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "tests/fixtures/corpus"
BASELINE = REPO / "docs/corpus-baselines/xz-fixture-scan.json"

_REGENERATE = (
    f"uv run codify scan-corpus {CORPUS.relative_to(REPO)} "
    f"--jurisdiction xz > {BASELINE.relative_to(REPO)}"
)


def test_the_fixture_scan_matches_its_pinned_baseline() -> None:
    """A scanner change that moves these numbers has to move the baseline in the
    same commit, so the diff is on the reviewer's screen rather than discovered
    a month later."""
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "codify.cli",
            "scan-corpus",
            str(CORPUS),
            "--jurisdiction",
            "xz",
            "--check",
            str(BASELINE),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    # "DRIFT" only when the scan ran and disagreed; anything else is a crash
    # reported as drift, which sends the reader looking for a scanner change
    # that never happened.
    what = "drifted" if "DRIFT" in result.stderr else "failed to run"
    assert result.returncode == 0, (
        f"fixture baseline {what}:\n{result.stderr}\nRegenerate with:\n  {_REGENERATE}"
    )


def test_the_fixture_corpus_is_still_there() -> None:
    """`scan-corpus` on an empty directory exits 1 with nothing on stdout, which
    a redirect would happily write over the baseline as an empty file."""
    # A count, not a truthiness check: one surviving fixture would pass the
    # latter and silently shrink what the baseline covers.
    assert len(list(CORPUS.glob("*.txt"))) == 5, f"expected 5 fixtures under {CORPUS}"
