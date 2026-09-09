"""Shim over `codify.frameworks.acquis` for the historic ingest-script import."""

from __future__ import annotations

from codify.frameworks.acquis import (
    classify_directive as classify,
)
from codify.frameworks.acquis import (
    load_acquis_framework,
)


def name_for(chapter_no: int) -> str | None:
    for chapter in load_acquis_framework().chapters:
        if chapter.number == chapter_no:
            return chapter.title
    return None


CHAPTER_NAMES: dict[int, str] = {c.number: c.title for c in load_acquis_framework().chapters}

__all__ = ["CHAPTER_NAMES", "classify", "name_for"]
