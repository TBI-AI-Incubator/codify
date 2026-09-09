"""Embedding construction identities remain stable across repeated derivation."""

import pytest

from codify.storage.embeddings import embedding_model_id


@pytest.mark.parametrize("base", ["test-model", "test-model+path"])
@pytest.mark.parametrize("path_context", [False, True])
def test_construction_id_is_idempotent(base: str, path_context: bool) -> None:
    expected = "test-model+path" if path_context else "test-model"
    actual = embedding_model_id(base, path_context=path_context)
    assert actual == expected
    assert embedding_model_id(actual, path_context=path_context) == expected
