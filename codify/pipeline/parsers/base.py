"""Structured-parser contract: deterministic per-jurisdiction converters.

A structured parser turns a well-structured upstream representation (e.g.
the Verkhovna Rada's anchored HTML) into Bluebell text plus the machine
layers the representation carries, resolved references, amendment
annotations, and the upstream-anchor↔eId map. It is the deterministic
counterpart to the universal LLM structuring lane.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from codify.jurisdictions import JurisdictionConfig


class StructuredRef(BaseModel):
    """One upstream-resolved reference found in the document body."""

    source_anchor: str  # upstream paragraph anchor, e.g. "n123"
    source_eid: str | None = None  # eId of the containing unit, once mapped
    href: str  # raw upstream target, e.g. "/go/889-19"
    label: str  # link text
    context: str = ""  # surrounding sentence fragment


class AmendmentAnnotation(BaseModel):
    """One editorial amendment note attached to a unit."""

    target_eid: str  # amended unit in this document
    akn_action: str  # insertion | substitution | repeal | variation
    amender_href: str  # upstream target of the amending act
    amender_label: str  # e.g. "№ 3384-IX від 20.09.2023"
    text: str  # full annotation text


class StructuredParse(BaseModel):
    """Everything a structured parser recovers from one document."""

    bluebell_text: str  # with inline {{>href label}} refs and {{*…}} remarks
    metadata: dict[str, Any] = Field(default_factory=dict)
    anchor_summary: dict[str, int] = Field(default_factory=dict)
    refs: list[StructuredRef] = Field(default_factory=list)
    amendments: list[AmendmentAnnotation] = Field(default_factory=list)
    anchor_eid_map: dict[str, str] = Field(default_factory=dict)  # "n123" -> "art_2"


class StructuredParser(Protocol):
    """Deterministic converter for one upstream representation."""

    name: str
    media_suffixes: tuple[str, ...]  # e.g. (".html", ".htm")

    def parse(
        self, content: bytes, *, config: JurisdictionConfig, doctype: str
    ) -> StructuredParse: ...
