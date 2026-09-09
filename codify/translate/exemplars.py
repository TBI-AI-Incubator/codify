"""Running-exemplar pool for register locking. Each body-fill batch samples one short
deontic-rich provision, source and target, and later batches get it prepended as an
already-agreed translation. Constructed per chapter in `translate_bodies`, so a bad
exemplar cannot cross; a bounded `collections.deque`, so the earliest rolls off.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# Deontic-verb regex per source-language family: a deontic verb in the source is
# what qualifies a provision as a register exemplar for the target, and the check
# is language-scoped so ordinary prose does not false-fire.
_SOURCE_DEONTIC: dict[str, re.Pattern[str]] = {
    "eng": re.compile(r"\b(shall|must|may not|shall not|is required to)\b", re.IGNORECASE),
    "ara": re.compile(r"يجب|يتعين|يلزم|على\s+كل|لا\s+يجوز", re.UNICODE),
    "heb": re.compile(r"חייב|יש\s+ל|אסור|לא\s+יהיה", re.UNICODE),
}

_MIN_LEN = 40
_MAX_LEN = 300


@dataclass(frozen=True)
class Exemplar:
    source: str
    target: str


@dataclass
class ExemplarPool:
    """Chapter-scoped FIFO pool of translated-provision exemplars."""

    target_language: str
    source_language: str | None = None
    capacity: int = 3
    _pool: deque[Exemplar] = field(init=False)
    picks: int = 0
    scans: int = 0

    def __post_init__(self) -> None:
        if self.capacity < 1:
            raise ValueError("capacity must be >= 1")
        # Cap normalised source language once so `active` matches lookup.
        normalised = (self.source_language or "").lower()
        if normalised and normalised not in _SOURCE_DEONTIC:
            # The pool is silently inert for jurisdictions we do not carry
            # a source-deontic pattern for; surface it once at construction
            # so an operator running e.g. a French source sees the log.
            logger.info(
                "exemplar_pool_inactive",
                reason="source_language_unregistered",
                source_language=self.source_language,
            )
        self._pool = deque(maxlen=self.capacity)

    @property
    def active(self) -> bool:
        """True when the source language has a registered deontic pattern.
        A pool created with an unknown source language stays inert (no
        pick, no render block); ``translate_document`` stamps this onto
        the audit so a run is not silently un-locked."""
        return (self.source_language or "").lower() in _SOURCE_DEONTIC

    @property
    def entries(self) -> list[Exemplar]:
        return list(self._pool)

    def add(self, exemplar: Exemplar) -> None:
        self._pool.append(exemplar)

    def pick_from(
        self,
        source_units: list[dict[str, Any]],
        translated_blocks: list[dict[str, Any]],
    ) -> Exemplar | None:
        """Scan the batch for a deontic-rich provision and add it, returning the pick or
        None; `source_units` and `translated_blocks` pair by eid. Rejects an exemplar
        whose source and target are byte-equal, the signature of a fallback batch where
        the pipeline echoed source back: adding it teaches later batches to do the same.
        """
        self.scans += 1
        pattern = _SOURCE_DEONTIC.get((self.source_language or "").lower())
        if pattern is None:
            return None
        translated_by_eid = {b.get("eid"): b for b in translated_blocks}
        for source in source_units:
            eid = source.get("eid")
            src_text = source.get("body") or ""
            if not (_MIN_LEN <= len(src_text) <= _MAX_LEN):
                continue
            if not pattern.search(src_text):
                continue
            translated = translated_by_eid.get(eid)
            if translated is None:
                continue
            tgt_text = "\n".join(translated.get("lines") or []).strip()
            if not tgt_text:
                continue
            if tgt_text.strip() == src_text.strip():
                # Fallback signature: LLM failed and source text was
                # returned as the translation. Refusing here prevents the
                # pool from teaching the LLM to leave source untranslated.
                continue
            exemplar = Exemplar(source=src_text.strip(), target=tgt_text)
            self.add(exemplar)
            self.picks += 1
            return exemplar
        return None

    def render_block(self) -> str:
        """Return the prompt block. Empty string when the pool has no
        entries so the caller can noop-concatenate."""
        if not self._pool:
            return ""
        lines = ["Already-agreed translations (follow this register):", ""]
        for ex in self._pool:
            lines.append(f"  Source: {ex.source}")
            lines.append(f"  Target: {ex.target}")
            lines.append("")
        return "\n".join(lines)


__all__ = ["Exemplar", "ExemplarPool"]
