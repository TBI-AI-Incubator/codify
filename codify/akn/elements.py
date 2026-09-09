from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from codify.akn.references import InlineReference


class ElementBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Subclasses override `kind` with a Literal; declared here so static
    # analysers can resolve `element.kind` on the base type.
    kind: str
    id: UUID = Field(default_factory=uuid4)
    akn_eid: str
    # Work-scoped id (AKN 3.0 Naming Convention). Stable across renumbering;
    # equal to akn_eid when no renumbering has occurred.
    akn_wid: str = ""
    akn_type: str = Field(
        description=(
            "AKN-XML element name as authored — may be jurisdiction-localised "
            "(e.g. 'Kreu' for chapter in Albanian) and is not guaranteed to equal `kind`."
        )
    )
    position: int
    number: str | None = None
    heading: str | None = None
    text: str = ""
    intro: str = ""
    wrap_up: str = ""
    references: list[InlineReference] = Field(default_factory=list)
    children: list[BodyElement] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _default_wid_to_eid(self) -> ElementBase:
        # Guarantee akn_wid is non-empty for every constructed element.
        if not self.akn_wid:
            object.__setattr__(self, "akn_wid", self.akn_eid)
        return self


class Title(ElementBase):
    kind: Literal["title"] = "title"


class Chapter(ElementBase):
    kind: Literal["chapter"] = "chapter"


class Section(ElementBase):
    kind: Literal["section"] = "section"


class Article(ElementBase):
    kind: Literal["article"] = "article"


class Paragraph(ElementBase):
    kind: Literal["paragraph"] = "paragraph"


class Subparagraph(ElementBase):
    kind: Literal["subparagraph"] = "subparagraph"


class Point(ElementBase):
    kind: Literal["point"] = "point"


BodyElement: TypeAlias = Annotated[
    Title | Chapter | Section | Article | Paragraph | Subparagraph | Point,
    Field(discriminator="kind"),
]


# Resolve `list[BodyElement]` self-reference deferred by `from __future__ import annotations`.
for _cls in (ElementBase, Title, Chapter, Section, Article, Paragraph, Subparagraph, Point):
    _cls.model_rebuild()
