"""Jurisdiction atlas, documentary metadata the structuring pipeline never reads.

Supranational memberships, non-membership binding regimes, and residual legal
systems: data that describes a jurisdiction's international standing for the
atlas UI, not config the ingest pipeline consumes. Split out of
`jurisdictions.py` to keep the structuring config focused. `JurisdictionConfig`
imports these back for its field annotations; the runtime consumer is the host
application's jurisdictions route.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Own copies of the strictness dicts (jurisdictions.py has its own); defined
# here so this module has no import-time dependency on jurisdictions.py.
_STRICT = ConfigDict(extra="forbid", populate_by_name=True)
_LOOSE = ConfigDict(extra="allow", populate_by_name=True)


# --- Supranational body registry ---------------------------------------------


@lru_cache(maxsize=1)
def _load_supranational_bodies() -> dict[str, dict[str, Any]]:
    """Canonical_code → body record. Absence raises: an empty map resolves no
    alias, so every supranational reference silently stops being recognised."""
    from .jurisdictions import (  # deferred: avoids an import cycle
        JURISDICTIONS_DIR,
        JurisdictionDataMissing,
    )

    path = JURISDICTIONS_DIR / "supranational_bodies.json"
    if not path.exists():
        raise JurisdictionDataMissing(f"no supranational body registry at {path}")
    data = json.loads(path.read_text())
    return {b["code"]: b for b in data.get("bodies", []) if isinstance(b, dict)}


@lru_cache(maxsize=1)
def _load_supranational_alias_map() -> dict[str, str]:
    """Build alias → canonical code map (lowercased keys for case-insensitive match)."""
    bodies = _load_supranational_bodies()
    out: dict[str, str] = {}
    for code, rec in bodies.items():
        out[code.lower()] = code
        for alias in rec.get("aliases", []) or []:
            out[alias.lower()] = code
    return out


def canonicalise_body(body: str) -> str:
    """Canonicalise a supranational body identifier via the alias map.

    Known aliases are mapped to their canonical code. Unknown bodies are
    returned unchanged so the validator's `UNKNOWN_BODY` check can flag them
    loudly, earlier versions quietly hyphen-normalised unknowns, which
    masked typos.
    """
    alias_map = _load_supranational_alias_map()
    if not alias_map:
        return body
    key = body.lower()
    return alias_map.get(key, body)


# Supranational / binding-regime legal effect, ordered strongest to weakest.
#   override, supersedes conflicting domestic law on covered subjects
#                            (OHADA Uniform Acts, Dayton Constitution annex)
#   direct, directly applicable without domestic transposition,
#                            does not necessarily override contrary law
#                            (EU/UEMOA/CEMAC Regulations, Compact defence clauses)
#   indirect_transposition, binding as to result, needs domestic transposition
#                            (EU Directives, EAC Acts, ECHR via HR Act-style laws)
#   procedural, creates procedural obligations only
#                            (Schengen, Interpol, cooperation regimes)
#   advisory, non-binding soft law / model instruments
#                            (SADC Model Laws, AU resolutions, Venice Opinions)
#   none, treaty obligation without automatic domestic effect
#                            (UN, WTO, most bilateral cooperation)
DirectEffectLevel = Literal[
    "override",
    "direct",
    "indirect_transposition",
    "procedural",
    "operational",
    "advisory",
    "none",
]

BindingRegimeKind = Literal[
    "treaty_framework",
    "bilateral_regime",
    "constitutional_annex",
    "security_council_resolution",
]

ReportingCadence = Literal["monthly", "quarterly", "six_monthly", "annual", "ad_hoc"]

# Lifecycle status of a supranational_memberships[] entry. Implicitly "member"
# when absent.
MembershipStatus = Literal[
    "member",  # normal current membership
    "suspended",  # currently suspended (see suspension_periods for history)
    "former",  # left / expelled / withdrew, see member_until
    "candidate",  # formal candidate (e.g. EU candidate after accession negotiations open)
    "potential_candidate",  # recognised aspirant, not yet candidate (e.g. Bosnia before 2022)
    "associate",  # associate member / associate state
    "observer",  # observer-only, no voting rights
    "special_status",  # sui generis (e.g. Vatican at UN)
    "never_member",  # documentary: "we want to note the relationship doesn't exist"
]


class SuspensionPeriod(BaseModel):
    """One period of suspension from a supranational body.

    Used inside `SupranationalMembership.suspension_periods` to record the
    start and (optional) end of a suspension, plus its scope. A list is the
    right shape because a jurisdiction can cycle through multiple suspensions.
    """

    model_config = _STRICT

    suspended_from: str = Field(
        description="Start of the suspension, as an ISO 8601 date.",
    )
    suspended_until: str | None = Field(
        default=None,
        description="End of the suspension, as an ISO 8601 date; None while still suspended.",
    )
    reason: str = Field(default="", description=("Why the suspension was imposed, as free text."))
    scope: Literal["full", "decision_making", "meetings", "voting"] = Field(
        default="full",
        description=(
            "What the suspension withdraws: all rights, or only decision-making, voting or "
            "attendance at meetings. Defaults to full withdrawal."
        ),
    )


class SupranationalMembership(BaseModel):
    model_config = _LOOSE

    body: str = Field(
        description=("Code of the body, canonicalised against the supranational body registry.")
    )
    direct_effect: DirectEffectLevel = Field(
        default="none",
        description=(
            "How strongly the body's law takes effect, from override (strongest) to none. "
            'Legacy boolean values are read as "direct" or "none".'
        ),
    )
    superseded_subjects: list[str] = Field(
        default_factory=list,
        description=("Subject areas where the body's law displaces domestic law."),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    member_since: str | None = Field(
        default=None,
        description=(
            "Date membership began, as a loose string, since some sources give only a year such"
            ' as "1999".'
        ),
    )
    direct_effect_note: str | None = Field(
        default=None, description=("Free-text note on `direct_effect`.")
    )
    direct_effect_instruments: list[str] = Field(
        default_factory=list, description=("The body's instrument types that have direct effect.")
    )
    transposition_required: bool | list[str] | None = Field(
        default=None,
        description=(
            "Whether the body's instruments need transposing into national law: a boolean, or a"
            ' list of the instrument types that do (for example ["directive"]).'
        ),
    )

    # --- lifecycle + carve-outs ---
    status: MembershipStatus | None = Field(
        default=None, description='Membership status; absent means "member".'
    )
    member_until: str | None = Field(
        default=None,
        description='Date membership ended, as an ISO 8601 date; set only when status is "former".',
    )
    rejoined_on: str | None = Field(
        default=None,
        description=(
            "Date a former member rejoined after its membership lapsed; `member_since` keeps "
            "the original date."
        ),
    )
    suspension_periods: list[SuspensionPeriod] = Field(
        default_factory=list,
        description=(
            "History of suspensions and reinstatements, including repeated cycles and a "
            "suspension still in force (`suspended_until` unset)."
        ),
    )
    excluded_subjects: list[str] = Field(
        default_factory=list,
        description="Subject areas the member has opted out of.",
    )
    excluded_instruments: list[str] = Field(
        default_factory=list, description=("Instruments of the body the member does not apply.")
    )
    protocol_references: list[str] = Field(
        default_factory=list,
        description=(
            "The treaties or protocols that create the carve-outs, as free text (for example "
            '"Protocol 21 TFEU").'
        ),
    )

    @field_validator("body", mode="before")
    @classmethod
    def _canonicalise_body(cls, v: Any) -> Any:
        if isinstance(v, str):
            return canonicalise_body(v)
        return v

    @field_validator("direct_effect", mode="before")
    @classmethod
    def _coerce_legacy_bool(cls, v: Any) -> Any:
        # Coerce legacy bool `direct_effect` to the canonical string literals.
        if v is True:
            return "direct"
        if v is False:
            return "none"
        return v


class BindingRegime(BaseModel):
    """Non-membership binding regime, treaty frameworks, bilateral regimes,
    constitutional annexes. Used for instruments that are legally binding on a
    jurisdiction but are not memberships in an international organisation.
    """

    model_config = _STRICT

    code: str = Field(
        description=("Code of the regime, canonicalised against the supranational body registry.")
    )
    kind: BindingRegimeKind = Field(description=("What kind of regime it is."))
    direct_effect: DirectEffectLevel = Field(
        default="none",
        description=("How strongly the regime's instruments take effect, from override to none."),
    )
    superseded_subjects: list[str] = Field(
        default_factory=list,
        description=("Subject areas where the regime's rules displace domestic law."),
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
    territories: list[str] = Field(
        default_factory=list,
        description=(
            "Territorial scope, typically set for UN Security Council resolutions and other "
            "time-bounded regimes; absent for permanent treaty frameworks."
        ),
    )
    in_force_from: str | None = Field(
        default=None, description=("Date the regime took effect, as an ISO date.")
    )
    in_force_until: str | None = Field(
        default=None, description=("Date the regime ends or ended, as an ISO date.")
    )
    renewable: bool = Field(default=False, description=("Whether the regime can be renewed."))
    reporting_cadence: ReportingCadence | None = Field(
        default=None, description=("How often the regime requires reports.")
    )

    @field_validator("code", mode="before")
    @classmethod
    def _canonicalise_code(cls, v: Any) -> Any:
        if isinstance(v, str):
            return canonicalise_body(v)
        return v


class ResidualLaw(BaseModel):
    """Another jurisdiction's corpus that is still residually applicable here.

    Used for layered legal systems where historical residual law, law inherited
    from a predecessor legal order, or foreign law continues in force alongside
    native legislation. Examples:
      - another jurisdiction's law applied as a suppletive source.
      - a historical codification still applied to general private law.
      - an inherited common law (but usually too embedded to carve out as
            discrete residuals).

    `source_jurisdiction` is a Codify code that must resolve to an existing
    config. Downstream consolidation/amendment agents can traverse these to
    find the source corpus.
    """

    model_config = _STRICT

    source_jurisdiction: str = Field(
        description=("Code of the jurisdiction whose corpus applies. It must have a config.")
    )
    label: str = Field(description=("Display name of the residual corpus."))
    scope: Literal["territorial", "subject_matter", "personal_status", "general"] = Field(
        description=(
            "What the residual law applies to: a territory, subjects, personal status or "
            "everything."
        )
    )
    territories: list[str] = Field(
        default_factory=list,
        description=("Territories the residual law applies in, when `scope` is territorial."),
    )
    subjects: list[str] = Field(
        default_factory=list,
        description=("Subject areas the residual law applies to, when `scope` is subject_matter."),
    )
    in_force_from: date | None = Field(
        default=None, description=("Date the residual law began to apply.")
    )
    in_force_until: date | None = Field(
        default=None, description=("Date the residual law stopped applying.")
    )
    superseded_by: list[str] = Field(
        default_factory=list,
        description="Superseding acts (as FRBR URIs or free-text citations).",
    )
    note: str = Field(
        default="",
        description=("Free-text note for config authors. The pipeline does not read it."),
    )
