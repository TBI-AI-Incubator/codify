"""Candidate selection, handcrafted vectors, deterministic ranking."""

from __future__ import annotations

import math
from typing import cast
from unittest.mock import AsyncMock

from codify.akn.document import Document
from codify.akn.elements import BodyElement
from codify.compare.aligner import embed_provisions, select_candidates
from codify.compare.scaffold import iter_assessable
from codify.embed.client import EmbeddingClient


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def test_select_candidates_ranks_by_dot_product() -> None:
    target = _unit([1.0, 0.0, 0.0])
    near = _unit([0.95, 0.05, 0.0])
    far = _unit([0.0, 1.0, 0.0])

    from codify.akn.elements import Article

    p_near: BodyElement = Article(akn_eid="near", akn_type="article", position=0, text="t")
    p_far: BodyElement = Article(akn_eid="far", akn_type="article", position=1, text="t")

    cands = select_candidates(target, [p_far, p_near], [far, near], k=2)

    assert [c.provision.akn_eid for c in cands] == ["near", "far"]
    assert cands[0].score > cands[1].score


def test_select_candidates_respects_k() -> None:
    from codify.akn.elements import Article

    target = [1.0, 0.0]
    provisions: list[BodyElement] = [
        Article(akn_eid=f"p{i}", akn_type="article", position=i, text="t") for i in range(5)
    ]
    vecs = [[1.0 - 0.1 * i, 0.0] for i in range(5)]
    cands = select_candidates(target, provisions, vecs, k=3)
    assert len(cands) == 3
    assert [c.provision.akn_eid for c in cands] == ["p0", "p1", "p2"]


def test_select_candidates_carries_source_frbr() -> None:
    from codify.akn.elements import Article

    target = _unit([1.0, 0.0, 0.0])
    near = _unit([0.95, 0.05, 0.0])
    far = _unit([0.0, 1.0, 0.0])
    p_near: BodyElement = Article(akn_eid="art_1", akn_type="article", position=0, text="t")
    p_far: BodyElement = Article(akn_eid="art_1", akn_type="article", position=1, text="t")

    # Same eId in two corpus laws; the winning candidate keeps its own source.
    cands = select_candidates(
        target,
        [p_far, p_near],
        [far, near],
        k=2,
        sources=["/akn/al/act/2008/9723", "/akn/al/act/2008/9901"],
    )
    assert cands[0].provision is p_near
    assert cands[0].frbr == "/akn/al/act/2008/9901"
    assert cands[1].frbr == "/akn/al/act/2008/9723"


async def test_embed_provisions_calls_embed_documents(directive_doc: Document) -> None:
    client = AsyncMock()
    client.embed_documents = AsyncMock(return_value=[[1.0, 0.0]] * 2)
    typed = cast(EmbeddingClient, client)

    provisions = list(iter_assessable(directive_doc))
    vecs = await embed_provisions(provisions, embedding_client=typed)

    assert len(vecs) == 2
    assert client.embed_documents.await_count == 1
    items = client.embed_documents.await_args.args[0]
    assert items[0][0] == "Subject matter and scope"


async def test_embed_provisions_empty_input_short_circuits() -> None:
    client = AsyncMock()
    client.embed_documents = AsyncMock()
    typed = cast(EmbeddingClient, client)
    out = await embed_provisions([], embedding_client=typed)
    assert out == []
    client.embed_documents.assert_not_awaited()
