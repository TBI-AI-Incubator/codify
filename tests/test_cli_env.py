"""The CLI reads credentials from the environment, so it must load `.env`.

Without it the OCR credential is absent and the pipeline trusts a text layer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codify import cli


def _env_file(tmp_path: Path, body: str) -> None:
    (tmp_path / ".env").write_text(body, encoding="utf-8")


def test_main_loads_the_credential_before_it_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through `main`, not `_load_env`: dropping the call is the regression."""
    _env_file(tmp_path, "AZURE_OPENAI_ENDPOINT=https://example.invalid/x\nAZURE_OPENAI_API_KEY=k\n")
    monkeypatch.chdir(tmp_path)
    # Both, or an ambient endpoint survives `override=False` and answers instead.
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    seen: dict[str, str | None] = {}

    def _capture(args: object) -> int:
        seen["url"] = cli._foundry_ocr_url()
        return 0

    monkeypatch.setattr(cli, "_run_scan_corpus", _capture)
    assert cli.main(["scan-corpus", str(tmp_path), "--jurisdiction", "xa"]) == 0
    assert seen["url"] == "https://example.invalid/providers/mistral/azure/ocr"


def test_the_ocr_credential_reaches_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env_file(tmp_path, "AZURE_OPENAI_ENDPOINT=https://example.invalid/x\nAZURE_OPENAI_API_KEY=k\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    cli._load_env()

    assert os.environ["AZURE_OPENAI_API_KEY"] == "k"
    assert cli._foundry_ocr_url() == "https://example.invalid/providers/mistral/azure/ocr"


def test_a_real_environment_variable_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment passes credentials in the environment; the file must not win."""
    _env_file(tmp_path, "AZURE_OPENAI_API_KEY=from-file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "from-env")

    cli._load_env()

    assert os.environ["AZURE_OPENAI_API_KEY"] == "from-env"
