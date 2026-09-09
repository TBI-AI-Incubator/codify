from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ReferenceBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    # Where this reference came from: 'href' where the publisher wrote the
    # link, 'registry' where an upstream register resolved it, 'text' where one
    # of our passes minted it from prose. Every value the resolver writes to
    # `cross_references.resolution_origin` must appear here: reading a row back
    # builds this model, and a value it does not know is a ValidationError on
    # the read path, not a bad label. The URI's own
    # shape cannot say, because publisher AKN carries relative `/akn/...`
    # hrefs of exactly the form our passes mint.
    origin: Literal["href", "text", "registry"] = "href"
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    text_snippet: str

    @model_validator(mode="after")
    def _offsets_ordered(self) -> _ReferenceBase:
        if self.end_offset < self.start_offset:
            raise ValueError("end_offset must be >= start_offset")
        return self


# The `class` token marking a `<ref>` that a pass minted from prose rather than
# one the publisher wrote. Carried in `class` because AKN 3.0 allows it there
# and a bespoke attribute fails schema validation. Written by the markup pass
# and by the emitter, read by the parser: all three must agree, so it lives
# beside the field it decides.
DERIVED_REF_CLASS = "derived"


class Citation(_ReferenceBase):
    kind: Literal["citation"] = "citation"
    target_uri: str | None = None


class CrossReference(_ReferenceBase):
    kind: Literal["cross_reference"] = "cross_reference"
    target_eid: str | None = None
    target_uri: str | None = None


class AmendmentReference(_ReferenceBase):
    kind: Literal["amendment_reference"] = "amendment_reference"
    amends_uri: str
    operation: Literal["insert", "delete", "replace", "renumber"]


InlineReference: TypeAlias = Annotated[
    Citation | CrossReference | AmendmentReference,
    Field(discriminator="kind"),
]
