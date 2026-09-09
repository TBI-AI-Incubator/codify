"""Embedding generation over an OpenAI-compatible gateway; L2-normalised."""

from codify.embed.client import (
    EmbeddingClient,
    EmbeddingError,
    TaskType,
)
from codify.storage.embeddings import embed_version_provisions

__all__ = [
    "EmbeddingClient",
    "EmbeddingError",
    "TaskType",
    "embed_version_provisions",
]
