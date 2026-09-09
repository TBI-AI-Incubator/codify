"""Jurisdiction atlas, documentary metadata the structuring pipeline never reads.

Supranational memberships, non-membership binding regimes, and residual legal
systems: data that describes a jurisdiction's international standing for the
atlas UI, not config the ingest pipeline consumes. Split out of
`jurisdictions.py` to keep the structuring config focused. `JurisdictionConfig`
imports these back for its field annotations; the only runtime consumer is
`apps/api/api/jurisdictions/router.py`.
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
    right shape because several jurisdictions have cycled through multiple
    suspensions (Fiji at the PIF/Commonwealth; Pakistan at the Commonwealth;
    coup states at AU/ECOWAS).
    """

    model_config = _STRICT

    suspended_from: str  # ISO 8601 date
    suspended_until: str | None = None  # None ⇒ still suspended
    reason: str = ""
    # Suspension scopes observed in practice: full rights withdrawal vs.
    # technocratic carve-outs (no decision-making, no voting, expelled from
    # meetings only). Default is the worst case.
    scope: Literal["full", "decision_making", "meetings", "voting"] = "full"


class SupranationalMembership(BaseModel):
    model_config = _LOOSE

    body: str
    # Effect spectrum, from strongest (override) to weakest (none). Legacy
    # bool values are coerced to `"direct"`/`"none"` at load time.
    direct_effect: DirectEffectLevel = "none"
    superseded_subjects: list[str] = Field(default_factory=list)
    note: str = ""
    # Loose string, year-only values like "1999" appear in corpus.
    member_since: str | None = None
    direct_effect_note: str | None = None
    direct_effect_instruments: list[str] = Field(default_factory=list)
    # Some configs use a bool; others list the specific instrument types
    # that require transposition (e.g., ["directive"]).
    transposition_required: bool | list[str] | None = None

    # --- lifecycle + carve-outs ---
    # Absent ⇒ implicitly "member".
    status: MembershipStatus | None = None
    # Only set when status is "former". ISO 8601.
    member_until: str | None = None
    # Reactivation date, for the Morocco → AU 2017 pattern (original
    # member_since is 1963, AU left 1984, rejoined_on 2017-01-30).
    rejoined_on: str | None = None
    # History of suspension / reinstatement pairs. Use for Fiji-style cycles
    # and for jurisdictions currently suspended (with suspended_until=None).
    suspension_periods: list[SuspensionPeriod] = Field(default_factory=list)
    # Structured opt-outs by subject area.
    excluded_subjects: list[str] = Field(default_factory=list)
    excluded_instruments: list[str] = Field(default_factory=list)
    # Structured but free, the treaty/protocol that creates the carve-out.
    # Examples: "Protocol 21 TFEU", "Protocol 10 Act of Accession 2003",
    # "Edinburgh Agreement 1992", "Withdrawal Agreement 2020".
    protocol_references: list[str] = Field(default_factory=list)

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
    constitutional annexes. Used for things like the Dayton Framework (BA),
    the Compact of Free Association (PW/MH), and the Lateran Concordat (VA)
    which are legally binding on a jurisdiction but are not memberships in an
    international organisation.
    """

    model_config = _STRICT

    code: str
    kind: BindingRegimeKind
    direct_effect: DirectEffectLevel = "none"
    superseded_subjects: list[str] = Field(default_factory=list)
    note: str = ""
    # Territorial / temporal scope, typically populated for SC resolutions
    # and other time-bounded regimes; absent on permanent treaty frameworks.
    territories: list[str] = Field(default_factory=list)
    in_force_from: str | None = None
    in_force_until: str | None = None
    renewable: bool = False
    reporting_cadence: ReportingCadence | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _canonicalise_code(cls, v: Any) -> Any:
        if isinstance(v, str):
            return canonicalise_body(v)
        return v


class ResidualLaw(BaseModel):
    """Another jurisdiction's corpus that is still residually applicable here.

    Used for layered legal systems where pre-independence or foreign law
    continues in force alongside native legislation. Examples:
      - VA: Italian law as suppletive in Vatican City State.
      - SY, IQ, LB: Ottoman Mejelle for general private law.
      - ZA, LS, SZ, NA: Roman-Dutch common law (but usually too embedded
            to carve out as discrete residuals).

    `source_jurisdiction` is a Codify code that must resolve to an existing
    config. Downstream consolidation/amendment agents can traverse these to
    find the source corpus.
    """

    model_config = _STRICT

    source_jurisdiction: str
    label: str
    scope: Literal["territorial", "subject_matter", "personal_status", "general"]
    territories: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    in_force_from: date | None = None
    in_force_until: date | None = None
    # Superseding acts (as FRBR URIs or free-text citations).
    superseded_by: list[str] = Field(default_factory=list)
    note: str = ""
