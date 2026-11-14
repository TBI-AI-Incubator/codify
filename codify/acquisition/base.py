"""Acquirer Protocol and document-shape models."""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from codify.provenance import OfficialRef

MediaType = Literal[
    "application/pdf", "application/xml", "application/json", "text/html", "text/plain"
]
# "card" is an adapter-fetched wrapper retained for provenance and ranked
# below "primary" when choosing the persisted source.
BodyRole = Literal["primary", "alternate", "gazette", "translation", "signature", "card"]


class DocumentRef(BaseModel):
    """Identifier for one document at one point in time."""

    model_config = ConfigDict(extra="forbid")

    jurisdiction_code: str
    doctype: str
    year: int
    number: str
    languages: list[str] = Field(default_factory=list)
    as_of: date | Literal["latest", "enacted"] = "latest"
    extra: dict[str, str] = Field(default_factory=dict)


class GazetteRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    year: int
    issue: str
    page: str | None = None
    # ISO date of the gazette issue, when the source carries it (the UA bulk
    # publication records do); absent otherwise, and the cover degrades.
    date: str | None = None


class Body(BaseModel):
    """One serialised representation, such as a source, alternate or translation."""

    model_config = ConfigDict(extra="forbid")
    media_type: MediaType
    content: bytes
    role: BodyRole = "primary"
    language: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


class AcquiredDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    ref: DocumentRef
    frbr_work_uri: str
    expression_uri: str | None = None
    bodies: list[Body] = Field(..., min_length=1)
    source_url: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    etag: str | None = None
    last_modified: str | None = None
    licence: str | None = None
    citation_alt: str | None = None
    gazette_ref: GazetteRef | None = None
    ocr_required: bool = False
    upstream_metadata: dict[str, Any] = Field(default_factory=dict)
    official: OfficialRef | None = None
    # One per further language body that has its own page at the publisher.
    official_translations: list[OfficialRef] = Field(default_factory=list)

    def primary(self) -> Body:
        for b in self.bodies:
            if b.role == "primary":
                return b
        return self.bodies[0]


def archive_date(path: Path | None) -> datetime:
    """A bulk archive's modified time: its copy or download date unless preserved; else now."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC) if path else datetime.now(UTC)
    except OSError:
        return datetime.now(UTC)


class DiscoveryQuery(BaseModel):
    """Generic query for `Acquirer.discover`. Per-portal subclasses extend it."""

    model_config = ConfigDict(extra="allow")
    doctype: str | None = None
    year_from: int | None = None
    year_to: int | None = None
    in_force_at: date | None = None
    limit: int | None = None


class CorpusManifest(BaseModel):
    """Per-jurisdiction list of refs at data/jurisdictions/{code}/corpus.json."""

    model_config = ConfigDict(extra="forbid")
    jurisdiction_code: str
    refs: list[DocumentRef]
    notes: str = ""


USER_AGENT_ENV = "ACQUISITION_USER_AGENT"


class MissingUserAgent(RuntimeError):
    """No User-Agent was given and ACQUISITION_USER_AGENT is unset."""


def require_user_agent(user_agent: str = "") -> str:
    """`user_agent`, else the caller's identity from ACQUISITION_USER_AGENT."""
    resolved = user_agent or os.environ.get(USER_AGENT_ENV, "").strip()
    if not resolved:
        raise MissingUserAgent(
            f"set {USER_AGENT_ENV} to the User-Agent your acquisition requests carry, "
            "naming you and a contact (for example 'my-crawler/1.0 (+https://example.org)')"
        )
    return resolved


class PolitenessProfile(BaseModel):
    """Runtime rate, robots and concurrency settings derived from an adapter.

    An empty `user_agent` is resolved from ACQUISITION_USER_AGENT when a client is built.
    """

    model_config = ConfigDict(extra="forbid")
    rate_limit_per_minute: int | None = None
    concurrency: int = 4
    user_agent: str = ""
    robots_respect: bool = True

    @classmethod
    def from_adapter(
        cls,
        adapter: object,
        *,
        concurrency: int = 4,
        user_agent: str = "",
    ) -> "PolitenessProfile":
        """Build a profile from a SourceAdapter, single source of truth for
        rate_limit_per_minute and robots_respect.
        """
        return cls(
            rate_limit_per_minute=getattr(adapter, "rate_limit_per_minute", None),
            robots_respect=getattr(adapter, "robots_respect", True),
            concurrency=concurrency,
            user_agent=user_agent,
        )


class Acquirer(Protocol):
    """One per jurisdiction. fetch+enumerate are the contract; discover optional."""

    jurisdiction_code: str
    politeness: PolitenessProfile

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument: ...

    # Async generators: declared `def … -> AsyncIterator` (not `async def`) so
    # callers can `async for` the result directly rather than awaiting a coroutine.
    def enumerate(  # noqa: A003
        self, manifest: CorpusManifest
    ) -> AsyncIterator[DocumentRef]: ...

    def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]: ...


class UnknownAdapterError(Exception):
    """No registered acquirer matches the SourceAdapter.kind."""


class BaseAcquirer:
    """Shared boilerplate for concrete adapters.

    Subclasses set the class attribute JURISDICTION (and optionally
    DEFAULT_RATE / DEFAULT_BASE_URL) and implement fetch(). enumerate()
    walks the manifest by default; discover() raises NotImplementedError.
    Concrete adapters can override either.
    """

    JURISDICTION: ClassVar[str] = ""
    DEFAULT_RATE: ClassVar[int | None] = None
    DEFAULT_BASE_URL: ClassVar[str | None] = None
    _CONCURRENCY: ClassVar[int] = 4

    def __init__(self, adapter: object, *, client: object | None = None) -> None:
        if not self.JURISDICTION:
            raise TypeError(f"{type(self).__name__}.JURISDICTION must be set by subclass")
        self.jurisdiction_code = self.JURISDICTION
        profile = PolitenessProfile.from_adapter(adapter, concurrency=self._CONCURRENCY)
        if profile.rate_limit_per_minute is None and self.DEFAULT_RATE is not None:
            profile = profile.model_copy(update={"rate_limit_per_minute": self.DEFAULT_RATE})
        self.politeness = profile
        self._adapter = adapter
        base_url = getattr(adapter, "base_url", None) or self.DEFAULT_BASE_URL
        self._base = base_url.rstrip("/") if base_url else ""
        # Subclasses build their httpx client (typically via make_polite_client).
        self._client = client

    @classmethod
    def from_config(cls, jurisdiction_code: str, adapter: object) -> "BaseAcquirer":
        if jurisdiction_code != cls.JURISDICTION:
            raise ValueError(
                f"{cls.__name__} only supports jurisdiction_code={cls.JURISDICTION!r}, "
                f"got {jurisdiction_code!r}"
            )
        return cls(adapter)

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        raise NotImplementedError

    async def enumerate(  # noqa: A003
        self, manifest: CorpusManifest
    ) -> AsyncIterator[DocumentRef]:
        for ref in manifest.refs:
            yield ref

    async def discover(self, query: DiscoveryQuery) -> AsyncIterator[DocumentRef]:
        raise NotImplementedError(
            f"{type(self).__name__} does not implement discover(); use enumerate(manifest)"
        )
        if False:  # pragma: no cover  (typing: this branch makes it an async generator)
            yield


__all__ = [
    "Acquirer",
    "AcquiredDocument",
    "archive_date",
    "BaseAcquirer",
    "Body",
    "BodyRole",
    "CorpusManifest",
    "DiscoveryQuery",
    "DocumentRef",
    "GazetteRef",
    "MediaType",
    "MissingUserAgent",
    "PolitenessProfile",
    "require_user_agent",
    "USER_AGENT_ENV",
    "UnknownAdapterError",
]
