"""Candidate selection by cosine similarity over unit-normalised vectors."""

from __future__ import annotations

from dataclasses import dataclass

from codify.akn.elements import BodyElement
from codify.compare.scaffold import provision_text
from codify.embed.client import EmbeddingClient


@dataclass(frozen=True)
class Candidate:
    provision: BodyElement
    score: float  # cosine similarity (vectors are unit-normalised → dot product)
    frbr: str = ""  # source law (the pool may span several domestic laws)


async def embed_provisions(
    provisions: list[BodyElement],
    *,
    embedding_client: EmbeddingClient,
    title: str = "",
    markers: tuple[str, ...] | None = None,
) -> list[list[float]]:
    if not provisions:
        return []
    items = [(title or (p.heading or ""), provision_text(p, markers)) for p in provisions]
    return await embedding_client.embed_documents(items)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def select_candidates(
    directive_vec: list[float],
    domestic: list[BodyElement],
    domestic_vecs: list[list[float]],
    *,
    k: int,
    sources: list[str] | None = None,
) -> list[Candidate]:
    if len(domestic) != len(domestic_vecs):
        raise ValueError("domestic and domestic_vecs must have the same length")
    if sources is not None and len(sources) != len(domestic):
        raise ValueError("sources must match domestic length")
    scored = [
        Candidate(
            provision=p,
            score=_dot(directive_vec, v),
            frbr=sources[i] if sources is not None else "",
        )
        for i, (p, v) in enumerate(zip(domestic, domestic_vecs, strict=True))
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:k]


__all__ = ["Candidate", "embed_provisions", "select_candidates"]
