"""Generate-validate-fix loop for LLM JSON output."""

from __future__ import annotations

import contextlib
import json
from typing import TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from codify.compare.prompts import build_retry_prompt
from codify.core.llm import LLMClient

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


class ComparatorValidationError(RuntimeError):
    pass


async def chat_json_validated(
    llm: LLMClient,
    prompt: str,
    schema: type[T],
    *,
    system: str | None = None,
    seed: int | None = None,
    retries: int = 3,
) -> T:
    last_output: str = ""
    last_error: str = ""
    current_prompt = prompt

    for attempt in range(retries + 1):
        try:
            raw = await llm.chat_json(current_prompt, system=system, seed=seed)
        except (ValueError, json.JSONDecodeError) as exc:
            last_output = ""
            last_error = f"transport: {exc}"
            logger.warning("comparator_llm_call_failed", attempt=attempt, error=last_error)
            current_prompt = build_retry_prompt(prompt, last_output, last_error)
            continue

        try:
            return schema.model_validate(raw)
        except ValidationError as exc:
            # Accept both a wrapped object and a bare match list.
            if isinstance(raw, list) and "matches" in schema.model_fields:
                candidates = []
                if len(raw) == 1 and isinstance(raw[0], dict):
                    candidates.append(raw[0])  # unwrap a single-element list
                candidates.append({"matches": raw})  # bare match list
                for candidate in candidates:
                    with contextlib.suppress(ValidationError):
                        return schema.model_validate(candidate)
            last_output = json.dumps(raw, ensure_ascii=False)
            last_error = str(exc)
            logger.warning("comparator_validation_failed", attempt=attempt, error=last_error[:300])
            current_prompt = build_retry_prompt(prompt, last_output, last_error)

    raise ComparatorValidationError(
        f"LLM output failed validation after {retries + 1} attempts: {last_error[:300]}"
    )


__all__ = ["ComparatorValidationError", "chat_json_validated"]
