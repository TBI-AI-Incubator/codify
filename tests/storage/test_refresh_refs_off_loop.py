"""Re-extracting references keeps its parsing and markup off the caller's event loop."""

from __future__ import annotations

import threading

import pytest

from codify.pipeline.enrich import references
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.storage import refresh_refs

_SOURCE = (
    "BODY\n\nSECTION 1\n\n"
    "  As {{>/akn/xa/act/2001/3 the Freedom of Information Act, 2001}} requires.\n"
)


@pytest.mark.asyncio
async def test_the_deterministic_read_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran_on: dict[str, int] = {}
    real_references, real_parse = references.emit_references, refresh_refs.parse_akn

    def spy_references(xml: str, country: str) -> str:
        ran_on["markup"] = threading.get_ident()
        return real_references(xml, country)

    def spy_parse(xml: str) -> object:
        ran_on["parse"] = threading.get_ident()
        return real_parse(xml)

    monkeypatch.setattr(references, "emit_references", spy_references)
    monkeypatch.setattr(refresh_refs, "parse_akn", spy_parse)

    akn = parse_to_akn(_SOURCE, "xa", doctype="act", date="2024-03-05", number="4")
    rows, _ = await refresh_refs._extract_reference_rows(akn, "xa", "act", {})

    assert rows == []
    assert set(ran_on) == {"markup", "parse"}
    assert threading.get_ident() not in ran_on.values()
