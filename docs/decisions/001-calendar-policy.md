# ADR 001: Gregorian Calendar in FRBR URIs

## Status

Accepted

## Context

Six jurisdictions use non-Gregorian calendar systems: Saudi Arabia (lunar Hijri), Iran (solar Hijri/Jalali), Ethiopia (Ethiopian), Japan (imperial era), Thailand (Buddhist Era), and Morocco (dual Hijri/Gregorian). Libya historically used a "Death of the Prophet" calendar (1978-2011).

During jurisdiction profiling, each agent independently chose whether to use the local calendar or Gregorian in FRBR URIs. This produced inconsistent URIs across the database: some configs used Hijri years, others Gregorian, with no project-wide rule.

The FRBR URI year segment is immutable once documents are ingested. Changing the convention after production ingestion would require re-generating all URIs for affected jurisdictions.

## Decision

**Always use Gregorian calendar years in FRBR URIs.** The local calendar date is stored as `<FRBRalias>` in the document's metadata block.

Example for Saudi Arabia:

- URI: `/akn/sa/act/2021/132` (Gregorian year)
- FRBRalias: `1443 هـ` (Hijri year)
- `frbr.date_calendar`: `"gregorian"` (the calendar used in URIs)

The `calendar` field in the jurisdiction config records which calendar system the jurisdiction natively uses. The `frbr.date_calendar` field records which calendar is used in URIs (always `"gregorian"`).

## Rationale

1. **Laws.Africa precedent**: The most mature AKN implementation uses Gregorian for all jurisdictions, including those in Africa with non-Gregorian influences.
2. **Interoperability**: EUR-Lex, legislation.gov.uk, and the UN system all use Gregorian. Cross-referencing between jurisdictions requires a common date space.
3. **Conversion determinism**: Gregorian → Hijri/Ethiopian/Buddhist is deterministic. The reverse requires knowing which calendar variant was used.
4. **Developer ergonomics**: Most software libraries, databases, and APIs expect Gregorian dates. Non-Gregorian years in URIs create parsing friction.

## Consequences

- Non-Gregorian jurisdictions require a calendar conversion step during ingestion (Hijri/Ethiopian/Buddhist/Imperial date → Gregorian year for the URI).
- The `calendar` config field preserves the jurisdiction's native system for display and metadata purposes.
- The `<FRBRalias>` element in each document stores the authoritative local-calendar citation for legal accuracy.
- This decision does NOT affect how dates are displayed in the UI; the UI can show dates in the local calendar if the jurisdiction config's `calendar` field indicates a non-Gregorian system.
