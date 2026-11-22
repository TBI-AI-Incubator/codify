"""The synchronous enrich passes run off the caller's event loop."""

from __future__ import annotations

import threading

import pytest

from codify.pipeline import stages
from codify.pipeline.enrich.bluebell import parse_to_akn


class _NoModel:
    def __getattr__(self, name: str) -> object:
        raise RuntimeError("no model in this test")


async def test_the_sync_passes_run_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    ran_on: list[int] = []
    real = stages.emit_references

    def spy(xml: str, country: str) -> str:
        ran_on.append(threading.get_ident())
        return real(xml, country)

    monkeypatch.setattr(stages, "emit_references", spy)
    reported: list[tuple[str, int]] = []

    akn = parse_to_akn("BODY\n\nSECTION 1\n\n  Text.\n", "xa", "act", "2024-03-05", "4")
    desc = stages.Descriptors(
        title="Test", raw_date="2024-03-05", number="4", year="2024", language="eng", doctype="act"
    )
    await stages.run_enrich_passes(
        akn,
        llm=_NoModel(),  # type: ignore[arg-type]
        jurisdiction_code="xa",
        desc=desc,
        on_pass=lambda name: reported.append((name, threading.get_ident())),
        skip_external_refs=True,
    )

    here = threading.get_ident()
    assert ran_on and here not in ran_on
    # The callback keeps its order and stays on the caller's thread.
    names = [name for name, _ in reported]
    assert names[:2] == ["cobalt", "akn_meta"] and names[-1] == "inline_markup"
    assert "references" in names
    assert {thread for _, thread in reported} == {here}
