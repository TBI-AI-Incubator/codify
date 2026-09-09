"""Acquisition substrate, Acquirer registry + manifest loader."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from codify.acquisition import (
    AcquiredDocument,
    CorpusManifest,
    DiscoveryQuery,
    DocumentRef,
    PolitenessProfile,
    UnknownAdapterError,
    get_acquirer,
    register_adapter,
)
from codify.jurisdictions import JurisdictionConfigError, SourceAdapter


class _Stub:
    def __init__(self, code: str, adapter: SourceAdapter) -> None:
        self.jurisdiction_code = code
        self.politeness = PolitenessProfile()
        self._adapter = adapter

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:  # pragma: no cover
        raise NotImplementedError

    async def enumerate(  # noqa: A003
        self, manifest: CorpusManifest
    ) -> AsyncIterator[DocumentRef]:  # pragma: no cover
        if False:
            yield  # pragma: no cover

    async def discover(
        self, query: DiscoveryQuery
    ) -> AsyncIterator[DocumentRef]:  # pragma: no cover
        if False:
            yield  # pragma: no cover


def test_unknown_jurisdiction_raises() -> None:
    """A code with no config fails on the config, not the adapter. The adapter
    errors below cover a jurisdiction that exists and names no usable source."""
    with pytest.raises(JurisdictionConfigError):
        get_acquirer("zz-not-a-code")


def test_html_portal_factory_rejects_wrong_jurisdiction() -> None:
    """html_portal is registered by the UA factory; ohada uses it but the
    factory enforces the JURISDICTION constant, wrong fit raises.
    """
    with pytest.raises((UnknownAdapterError, ValueError)):
        get_acquirer("ohada")


def test_registered_factory_returns_acquirer() -> None:
    register_adapter("akn_native", lambda code, ad: cast("object", _Stub(code, ad)))  # type: ignore[arg-type]
    a = get_acquirer("gb")
    assert a.jurisdiction_code == "gb"


def test_kind_filter_picks_specific_adapter() -> None:
    register_adapter("akn_native", lambda code, ad: cast("object", _Stub(code, ad)))  # type: ignore[arg-type]
    a = get_acquirer("gb", kind="akn_native")
    assert a.jurisdiction_code == "gb"
    with pytest.raises(UnknownAdapterError):
        get_acquirer("gb", kind="eurlex_cellar")
