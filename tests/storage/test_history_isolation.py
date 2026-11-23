"""History is reachable only through the history package: no search, embedding or
latest-version reader names its tables or models."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "codify"
_NAMES = (
    "history_works",
    "publisher_texts",
    "dated_expressions",
    "provision_intervals",
    "provision_links",
    "history_withdrawals",
    "HistoryWork",
    "PublisherText",
    "DatedExpression",
    "ProvisionInterval",
    "ProvisionLink",
    "HistoryWithdrawal",
    "codify.history",
    "codify import history",
)
_OWNERS = (
    _ROOT / "history",
    _ROOT / "migrations",
    _ROOT / "storage" / "models.py",
)


def _owned(path: Path) -> bool:
    return any(path == owner or owner in path.parents for owner in _OWNERS)


def _readers(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*")) if p.suffix in {".py", ".sql"} and not _owned(p)]


def _mentions(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _readers(root):
        source = path.read_text(encoding="utf-8")
        if hits := [name for name in _NAMES if name in source]:
            found[str(path.relative_to(root))] = hits
    return found


def test_no_reader_outside_the_history_package_names_a_history_table() -> None:
    assert _mentions(_ROOT) == {}


def test_the_scan_reads_the_search_embedding_and_latest_version_code() -> None:
    scanned = {str(p.relative_to(_ROOT)) for p in _readers(_ROOT)}
    assert {
        "storage/versions.py",
        "storage/retrieval.py",
        "storage/embeddings.py",
        "storage/provisions.py",
    } <= scanned
    assert any(name.startswith("search/") for name in scanned)
    assert any(name.startswith("embed/") for name in scanned)


def test_the_scan_flags_a_reader_that_starts_using_history(tmp_path: Path) -> None:
    (tmp_path / "storage").mkdir()
    (tmp_path / "storage" / "versions.py").write_text(
        'SQL = "SELECT 1 FROM provision_intervals"\n', encoding="utf-8"
    )
    (tmp_path / "search.py").write_text("from codify.history import build\n", encoding="utf-8")
    assert _mentions(tmp_path) == {
        "storage/versions.py": ["provision_intervals"],
        "search.py": ["codify.history"],
    }
