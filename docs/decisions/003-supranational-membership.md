# ADR 003: Supranational Membership Is Config Data, Not Code

## Status

Accepted

## Status note, 14 November 2026

The data model is in the core as described. The statements about what reads that
data describe code that is not in this repository.

| Part of this record | State in the core |
| --- | --- |
| Body registry and alias resolution | Present. The loader is `_load_supranational_bodies()` in `codify/jurisdictions_atlas.py`; `codify/jurisdictions.py` re-exports `canonicalise_body` only |
| `supranational_memberships[]` on a configuration | Present and optional. It defaults to an empty list, and most shipped configurations do not carry it |
| The validator's `UNKNOWN_BODY` check | Does not exist. An unregistered body is returned unchanged and the configuration loads without a finding |
| Comparison and lens applicability read the memberships | Not in the core. `codify.compare.comparator.compare` takes two documents and no body, and nothing under `codify/compare`, `codify/lenses` or `codify/frameworks` reads the field |
| No hardcoded union | Not yet true. `eu` is a literal in format dispatch (`codify/pipeline/formats/__init__.py`) and in citation resolution (`codify/frbr.py`), and the one framework that ships is `data/frameworks/eu-acquis.yaml` |

Of the four wiring steps below, steps 1 and 2 are data changes the loader reads today. Step 3 has
nothing to attach to in the core. Step 4 needs a configuration with
`type: "supranational"` for the body before its corpus can be ingested; the
synthetic `xu` is one, and none ships with the code `eu`. `GET
/jurisdictions/{code}` on `codify serve` returns the whole configuration,
memberships included.

Two parts of the model are not described below:

- `direct_effect` is a scale, not a flag: `override`, `direct`,
  `indirect_transposition`, `procedural`, `operational`, `advisory` or `none`,
  which is the default. A boolean from an older configuration is read as
  `direct` or `none`.
- `binding_regimes[]` records a regime that binds a jurisdiction without
  membership, such as a treaty framework or a constitutional annex
  (`BindingRegime` in `codify/jurisdictions_atlas.py`). No shipped
  configuration uses it yet.

## Context

The EU-acquis comparison machinery originally assumed its supranational body was
always the EU. To support other unions (OHADA uniform acts against member states,
the African Union, a GCC framework) without a comparator rewrite each time, the
membership relationship a jurisdiction has with a supranational body must be
**data on the jurisdiction config**, and the comparison surfaces must read that
data rather than hardcode "EU".

## Decision

Supranational membership is modelled in two places, both data:

1. **A body registry**, `data/jurisdictions/supranational_bodies.json`. Each entry
   is `{code, aliases?, ...}` (`aliases` optional; codes include `un`, `eu`,
   `ohada`, `african-union`, `caricom`, `asean` and `wto`; the file is the full list).
   Loaded by `_load_supranational_bodies()` (`codify/jurisdictions.py`).

2. **Per-jurisdiction memberships**: every `data/jurisdictions/<code>/config.json`
   carries a `supranational_memberships[]` array; each entry names a `body` (a
   registry code), a lifecycle `status` (`MembershipStatus`: member, candidate,
   potential_candidate, associate, observer, suspended, former, special_status or
   never_member; implicitly `member` when absent), and optional `direct_effect` /
   `superseded_subjects`.

Identity is resolved through `canonicalise_body()` over an alias map built from the
registry; an unknown body is returned unchanged so the validator's `UNKNOWN_BODY`
check flags it loudly rather than silently normalising a typo. Comparison and lens
applicability derive from these config memberships, not a hardcoded union.

## Wiring in a second supranational body

No comparator or model change is required; it is four data/config steps:

1. **Register the body**: add a `{code, aliases?, ...}` entry to
   `supranational_bodies.json`. The alias map and `canonicalise_body()` pick it up;
   `UNKNOWN_BODY` stops flagging it.
2. **Declare memberships**: add a `supranational_memberships[]` entry (with `body`,
   `status`, and `direct_effect` where the body's instruments have direct effect) to
   each member/candidate jurisdiction's `config.json`.
3. **Point the comparison at the body**: the acquis/comparison surface already reads
   the config membership to decide applicability, so a lens for the new body's
   instruments (e.g. OHADA uniform acts) is scoped by the body `code`, not by an
   `if country == "eu"` branch.
4. **Supply the reference corpus**: ingest the body's instruments as its own
   jurisdiction (`type: "supranational"`) so member laws can be compared against them.

Steps 3 and 4 are the only ones that need new content (a lens config and the body's
corpus); steps 1 and 2 are pure config edits. The abstraction that makes this work is
that "which union, and in what status" is a property of the jurisdiction record,
never a constant in the comparison code.

## Consequences

- Adding a union is a data change plus a corpus, reviewable without touching the
  comparator.
- The lifecycle `status` vocabulary lets one config express member, candidate, and
  former relationships (with `member_until` / `suspension_periods` for history), so
  accession and withdrawal are modelled rather than hardcoded.
- The registry is the single source of truth for body identity; typos surface via
  `UNKNOWN_BODY` instead of forking a canonical code.
