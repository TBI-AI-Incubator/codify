"""What the server answers with, as models: the OpenAPI schema is generated from
these, and the UI's types from that."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, HttpUrl

from codify.storage.laws import LawSummary

Status = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class Health(BaseModel):
    status: Literal["ok"]


class JurisdictionSummary(BaseModel):
    code: str
    name: str
    languages: list[str]


class LawPage(BaseModel):
    items: list[LawSummary]
    limit: int
    offset: int


class VersionSummary(BaseModel):
    id: uuid.UUID
    law_id: uuid.UUID
    expression_uri: str
    language: str
    expression_date: date | None
    ingested_at: datetime | None
    structural_quality_grade: str | None


class VersionDetail(VersionSummary):
    akn_xml: str


class LawDetail(BaseModel):
    id: uuid.UUID
    jurisdiction: str
    title: str
    short_title: str | None
    doctype: str
    status: str
    year: int | None
    number: str | None
    work_uri: str
    versions: list[VersionSummary]


class SearchMatch(BaseModel):
    provision_id: uuid.UUID
    eid: str
    score: float
    text: str


class SearchResult(BaseModel):
    query: str
    matches: list[SearchMatch]


class IngestUrl(BaseModel):
    url: HttpUrl  # a URL only: a local path is the upload endpoint's job
    jurisdiction: str
    title: str | None = None


class RunSnapshot(BaseModel):
    id: uuid.UUID
    kind: str
    status: Status
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    result: dict[str, Any] | None
    error: str | None
    retry_of: uuid.UUID | None
    events: int
    lost_on_restart: bool


class Cancelled(BaseModel):
    cancelled: uuid.UUID
