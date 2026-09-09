"""AKN 3.0 `<meta><analysis>` and `<meta><lifecycle>` domain objects.

`TextualMod` captures a single `<textualMod>` element, one of five
modification categories (textual, meaning, scope, force, efficacy) with a
type-attribute action drawn from the corresponding OASIS enum.

`LifecycleEvent` captures a single `<eventRef>` under `<lifecycle>`, an
event of type `generation`, `amendment`, or `repeal` on a given date.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AknCategory = Literal["textual", "meaning", "scope", "force", "efficacy"]

AknAction = Literal[
    # textual
    "repeal",
    "substitution",
    "insertion",
    "replacement",
    "renumbering",
    "split",
    "join",
    # meaning
    "variation",
    "termModification",
    "authenticInterpretation",
    # scope
    "exceptionOfScope",
    "extensionOfScope",
    # force
    "entryIntoForce",
    "endOfEnactment",
    "postponementOfEntryIntoForce",
    "prorogationOfForce",
    "reEnactment",
    "unconstitutionality",
    # efficacy
    "entryIntoEfficacy",
    "endOfEfficacy",
    "inapplication",
    "retroactivity",
    "extraefficacy",
    "postponementOfEfficacy",
    "prorogationOfEfficacy",
]

EventType = Literal["generation", "amendment", "repeal"]


_ACTION_TO_CATEGORY: dict[AknAction, AknCategory] = {
    # textual
    "repeal": "textual",
    "substitution": "textual",
    "insertion": "textual",
    "replacement": "textual",
    "renumbering": "textual",
    "split": "textual",
    "join": "textual",
    # meaning
    "variation": "meaning",
    "termModification": "meaning",
    "authenticInterpretation": "meaning",
    # scope
    "exceptionOfScope": "scope",
    "extensionOfScope": "scope",
    # force
    "entryIntoForce": "force",
    "endOfEnactment": "force",
    "postponementOfEntryIntoForce": "force",
    "prorogationOfForce": "force",
    "reEnactment": "force",
    "unconstitutionality": "force",
    # efficacy
    "entryIntoEfficacy": "efficacy",
    "endOfEfficacy": "efficacy",
    "inapplication": "efficacy",
    "retroactivity": "efficacy",
    "extraefficacy": "efficacy",
    "postponementOfEfficacy": "efficacy",
    "prorogationOfEfficacy": "efficacy",
}


def category_for(action: AknAction) -> AknCategory:
    """Map an AKN action value to its modification category."""
    return _ACTION_TO_CATEGORY[action]


class QuotedContent(BaseModel):
    """Preserves the unbounded `<previous>` / `<old>` / `<new>` children of a
    single `<textualMod>`. `<quotedStructure>` inner XML is flattened to a
    string; multiple blocks stay separate list entries."""

    model_config = ConfigDict(extra="forbid")

    previous: str | None = None
    old: list[str] = Field(default_factory=list)
    new: list[str] = Field(default_factory=list)


class TextualMod(BaseModel):
    """One `<textualMod>` element parsed from `<meta><analysis>`."""

    model_config = ConfigDict(extra="forbid")

    akn_category: AknCategory
    akn_action: AknAction
    source_akn_wid: str
    target_frbr_uri: str
    target_akn_wid: str | None = None
    quoted: QuotedContent | None = None
    authority_uri: str | None = None
    mod_eid_ref: str | None = None


class LifecycleEvent(BaseModel):
    """One `<eventRef>` element parsed from `<meta><lifecycle>`."""

    model_config = ConfigDict(extra="forbid")

    event_date: str  # ISO YYYY-MM-DD; kept as string to preserve source formatting
    event_type: EventType
    source_uri: str | None = None
    refers_uri: str | None = None
    originating_uri: str | None = None
