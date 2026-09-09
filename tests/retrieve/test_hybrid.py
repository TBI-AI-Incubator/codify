"""Hybrid retrieval orchestrator unit tests."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from codify.embed.client import EmbeddingClient
from codify.retrieve.hybrid import ProvisionMatch, retrieve


@pytest.fixture
def mock_session() -> AsyncSession:
    return cast(AsyncSession, AsyncMock())


@pytest.fixture
def mock_client() -> EmbeddingClient:
    client = AsyncMock()
    client.embed_one = AsyncMock(return_value=[0.0] * 768)
    return cast(EmbeddingClient, client)


async def test_no_filter_raises(mock_session: AsyncMock, mock_client: AsyncMock) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        await retrieve(mock_session, "q", embedding_client=mock_client)


async def test_two_filters_raises(mock_session: AsyncMock, mock_client: AsyncMock) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        await retrieve(
            mock_session,
            "q",
            embedding_client=mock_client,
            version_id=uuid.uuid4(),
            jurisdiction_code="al",
        )


@patch("codify.retrieve.hybrid.hybrid_search", new_callable=AsyncMock)
async def test_version_id_passes_singleton_list(
    mock_hybrid: AsyncMock, mock_session: AsyncMock, mock_client: AsyncMock
) -> None:
    mock_hybrid.return_value = []
    vid = uuid.uuid4()
    await retrieve(mock_session, "q", embedding_client=mock_client, version_id=vid)
    assert mock_hybrid.await_args is not None
    assert mock_hybrid.await_args.kwargs["version_ids"] == [vid]


@patch("codify.retrieve.hybrid.latest_version_for_law", new_callable=AsyncMock)
@patch("codify.retrieve.hybrid.hybrid_search", new_callable=AsyncMock)
async def test_law_id_resolves_via_storage(
    mock_hybrid: AsyncMock,
    mock_latest: AsyncMock,
    mock_session: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    resolved_vid = uuid.uuid4()
    mock_latest.return_value = type("V", (), {"id": resolved_vid})()
    mock_hybrid.return_value = []
    await retrieve(mock_session, "q", embedding_client=mock_client, law_id=uuid.uuid4())
    assert mock_hybrid.await_args is not None
    assert mock_hybrid.await_args.kwargs["version_ids"] == [resolved_vid]


@patch("codify.retrieve.hybrid.latest_versions_for_jurisdiction", new_callable=AsyncMock)
@patch("codify.retrieve.hybrid.hybrid_search", new_callable=AsyncMock)
async def test_jurisdiction_code_resolves_via_storage(
    mock_hybrid: AsyncMock,
    mock_latest_juris: AsyncMock,
    mock_session: AsyncMock,
    mock_client: AsyncMock,
) -> None:
    vids = [uuid.uuid4(), uuid.uuid4()]
    mock_latest_juris.return_value = vids
    mock_hybrid.return_value = []
    await retrieve(mock_session, "q", embedding_client=mock_client, jurisdiction_code="al")
    assert mock_hybrid.await_args is not None
    assert mock_hybrid.await_args.kwargs["version_ids"] == vids


@patch("codify.retrieve.hybrid.hybrid_search", new_callable=AsyncMock)
async def test_query_embedded_with_query_task(
    mock_hybrid: AsyncMock, mock_session: AsyncMock, mock_client: AsyncMock
) -> None:
    mock_hybrid.return_value = []
    await retrieve(
        mock_session, "trade secrets", embedding_client=mock_client, version_id=uuid.uuid4()
    )
    mock_client.embed_one.assert_awaited_once_with("trade secrets", task="query")


@patch("codify.retrieve.hybrid.hybrid_search", new_callable=AsyncMock)
async def test_projects_storage_rows_to_matches(
    mock_hybrid: AsyncMock, mock_session: AsyncMock, mock_client: AsyncMock
) -> None:
    pid_a, pid_b = uuid.uuid4(), uuid.uuid4()
    mock_hybrid.return_value = [
        (pid_a, "art_5", "Trade secrets shall...", 0.05),
        (pid_b, "art_6", "Confidentiality...", 0.03),
    ]
    matches = await retrieve(
        mock_session, "q", embedding_client=mock_client, version_id=uuid.uuid4()
    )
    assert matches == [
        ProvisionMatch(pid_a, 0.05, "Trade secrets shall...", "art_5"),
        ProvisionMatch(pid_b, 0.03, "Confidentiality...", "art_6"),
    ]


@patch("codify.retrieve.hybrid.latest_versions_for_jurisdiction", new_callable=AsyncMock)
async def test_empty_resolution_short_circuits_embedding(
    mock_latest_juris: AsyncMock, mock_session: AsyncMock, mock_client: AsyncMock
) -> None:
    mock_latest_juris.return_value = []
    matches = await retrieve(
        mock_session, "q", embedding_client=mock_client, jurisdiction_code="zz"
    )
    assert matches == []
    mock_client.embed_one.assert_not_awaited()
