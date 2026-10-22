"""A unit the document marks out of the pool stays out of comparison text, and the
marked scope is walked once per document rather than once per candidate."""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from codify.akn.document import Document
from codify.akn.elements import Article, Paragraph, Point
from codify.compare import compare
from codify.core.llm import LLMClient
from codify.embed.client import EmbeddingClient

_DUTY = "the Secretariat to collect the registers"


def _reference() -> Document:
    # A marked point inside an unmarked paragraph: the paragraph is compared, the point not.
    paragraph = Paragraph(
        akn_eid="sec_I__para_1",
        akn_type="paragraph",
        position=0,
        intro="Adherents should keep registers, and:",
        children=[
            Point(akn_eid="sec_I__para_1__point_a", akn_type="point", position=0,
                  text="publish them annually"),
            Point(akn_eid="sec_I__para_1__point_b", akn_type="point", position=1,
                  refers_to="#nonAdherentDuty", text=_DUTY),
        ],
    )  # fmt: skip
    return Document(
        frbr_work_uri="/akn/oecd/act/recommendation/2020/9002",
        frbr_expression_uri="/akn/oecd/act/recommendation/2020/9002/eng@2020-05-05",
        language="eng",
        expression_date=date(2020, 5, 5),
        body=[paragraph],
    )


def _domestic() -> Document:
    return Document(
        frbr_work_uri="/akn/xz/act/2020/1",
        frbr_expression_uri="/akn/xz/act/2020/1/eng@2020-01-01",
        language="eng",
        expression_date=date(2020, 1, 1),
        body=[
            Article(akn_eid="art_1", akn_type="article", position=0, text="Registers are kept."),
        ],
    )


async def _run(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str], int]:
    import codify.quality.sentinels as sentinels

    walks = 0
    real = sentinels.marked_scope

    def counted(*args: Any) -> Any:
        nonlocal walks
        walks += 1
        return real(*args)

    monkeypatch.setattr(sentinels, "marked_scope", counted)
    embedded: list[str] = []

    async def _embed(items: list[tuple[str, str]]) -> list[list[float]]:
        embedded.extend(text for _, text in items)
        return [[1.0, 0.0] for _ in items]

    embedding = AsyncMock()
    embedding.embed_documents = AsyncMock(side_effect=_embed)
    llm = AsyncMock()
    llm.chat_json = AsyncMock(
        return_value={
            "verdict": "aligned",
            "confidence": 0.9,
            "note": "n",
            "citations": [],
            "actionable": True,
            "provision_kind": "obligation",
            "clause_method": "normal",
        }
    )
    await compare(
        _reference(),
        _domestic(),
        llm=cast(LLMClient, llm),
        embedding_client=cast(EmbeddingClient, embedding),
    )
    prompts = [str(call.args[0]) for call in llm.chat_json.await_args_list]
    return embedded, prompts, walks


async def test_a_marked_point_stays_out_of_its_parents_comparison_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedded, prompts, _ = await _run(monkeypatch)
    assert [t for t in embedded if "publish them annually" in t]
    assert [t for t in embedded if _DUTY in t] == []
    assert prompts and all("publish them annually" in p for p in prompts)
    assert [p for p in prompts if _DUTY in p] == []


async def test_the_marked_scope_is_walked_once_per_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, walks = await _run(monkeypatch)
    # The reference's scope; the domestic law's jurisdiction lists no marker.
    assert walks == 1
