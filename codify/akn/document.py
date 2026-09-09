from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from codify.akn.analysis import LifecycleEvent, TextualMod
from codify.akn.elements import BodyElement


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    frbr_work_uri: str
    frbr_expression_uri: str
    language: str
    expression_date: date
    # The work's own date, when the document states one. Optional because the
    # expression date was standing in for it, which wrote a derived date over a
    # real enactment date every time an amended version was re-emitted.
    work_date: date | None = None
    body: list[BodyElement] = Field(default_factory=list)
    # Attachment content, kept out of `body` because it restates the body's
    # numbering and merging the two would collide every eId.
    attachments: list[BodyElement] = Field(default_factory=list)
    textual_mods: list[TextualMod] = Field(default_factory=list)
    lifecycle_events: list[LifecycleEvent] = Field(default_factory=list)
