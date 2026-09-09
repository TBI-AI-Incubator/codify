"""Generate-validate-fix loop, corrective retries on Pydantic validation errors."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from codify.compare.types import LLMVerdict
from codify.compare.validation import ComparatorValidationError, chat_json_validated
from codify.core.llm import LLMClient


def _llm(*responses: dict[str, object] | list | Exception) -> AsyncMock:
    mock = AsyncMock()
    mock.chat_json = AsyncMock(side_effect=list(responses))
    return mock


async def test_validates_first_attempt() -> None:
    valid = {
        "verdict": "aligned",
        "confidence": 0.9,
        "note": "matches",
        "citations": [],
    }
    llm = _llm(valid)
    result = await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict)
    assert result.verdict == "aligned"
    assert llm.chat_json.await_count == 1


async def test_retries_after_validation_failure() -> None:
    bad = {"verdict": "invalid_label", "confidence": 0.5, "note": "x"}
    good = {"verdict": "partial", "confidence": 0.6, "note": "ok", "citations": []}
    llm = _llm(bad, good)
    result = await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict, retries=2)
    assert result.verdict == "partial"
    assert llm.chat_json.await_count == 2


async def test_retries_after_transport_value_error() -> None:
    good = {"verdict": "gap", "confidence": 0.7, "note": "no match", "citations": []}
    llm = _llm(ValueError("invalid JSON"), good)
    result = await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict, retries=2)
    assert result.verdict == "gap"
    assert llm.chat_json.await_count == 2


async def test_raises_when_retries_exhausted() -> None:
    bad = {"verdict": "nope", "confidence": 2.0}
    llm = _llm(bad, bad, bad)
    with pytest.raises(ComparatorValidationError):
        await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict, retries=2)
    assert llm.chat_json.await_count == 3


async def test_passes_seed_through() -> None:
    valid = {"verdict": "aligned", "confidence": 0.9, "note": "x", "citations": []}
    llm = _llm(valid)
    await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict, seed=42)
    assert llm.chat_json.await_args.kwargs["seed"] == 42


async def test_auto_wraps_bare_list_into_matches_field() -> None:
    """LLMs routinely emit a bare list when the schema has a `matches`
    field. The validator wraps once before retrying."""

    class ExampleMatch(BaseModel):
        label: str

    class ExampleMatches(BaseModel):
        matches: list[ExampleMatch]

    bare = [{"label": "x"}]
    llm = _llm(bare)
    result = await chat_json_validated(cast(LLMClient, llm), "p", ExampleMatches)
    assert isinstance(result, ExampleMatches)
    assert len(result.matches) == 1
    assert isinstance(result.matches[0], ExampleMatch)
    # Single call, wrap fixed the shape; no retry needed.
    assert llm.chat_json.await_count == 1


async def test_auto_wrap_skipped_for_schemas_without_matches_field() -> None:
    """LLMVerdict has no `matches` field; a bare list must fall through
    to the retry loop, not be silently wrapped."""
    bare_list = [{"verdict": "aligned"}]
    good = {"verdict": "aligned", "confidence": 0.9, "note": "x", "citations": []}
    llm = _llm(bare_list, good)
    result = await chat_json_validated(cast(LLMClient, llm), "p", LLMVerdict, retries=2)
    assert result.verdict == "aligned"
    # Two calls, bare list retried, not wrapped.
    assert llm.chat_json.await_count == 2
