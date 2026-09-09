from __future__ import annotations

from typing import Any

from codify.akn import Document

_IDENTITY_FIELDS = frozenset({"id", "created_at", "updated_at"})


def strip_identity(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_identity(v) for k, v in value.items() if k not in _IDENTITY_FIELDS}
    if isinstance(value, list):
        return [strip_identity(v) for v in value]
    return value


def structurally_equal(a: Document, b: Document) -> bool:
    return strip_identity(a.model_dump(mode="json")) == strip_identity(b.model_dump(mode="json"))
