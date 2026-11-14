# ADR 002: § (Paragraph Symbol) Maps to AKN `article`

## Status

Accepted

## Status note, 14 November 2026

The decision stands and its first consequence is complete. The `hu`
configuration maps the § level to `article` with keyword `ARTICLE` in its `act`
and `rendelet` classes, as the other eight configurations named below do.

Two details differ from the text:

- No configuration sets `eid_abbrev` on a § level. The `art` prefix follows from
  the `article` element (`EID_ABBREV` in `codify/akn/eid.py`). `eid_abbrev` is
  for a level whose element cannot imply a prefix.
- No test enforces the rule for a new § jurisdiction. Check the mapping when you
  review a new configuration.

## Context

The § symbol is used as the basic citable provision in Germanic and Central European civil law traditions: Germany (Paragraph), Austria (Paragraph), Czech Republic (paragraf), Slovakia (paragraf), Denmark (paragraf), Estonia (paragrahv), Sweden (paragraf), Finland (pykälä), and Hungary (szakasz).

During jurisdiction profiling, every profile except Hungary's mapped § to AKN `<article>`, consistent with the LegalDocML.de convention (Germany's own AKN application profile). Hungary's profile mapped § to AKN `<section>`, citing the English translation "section" used on njt.hu.

This inconsistency means the same structural element (§) produces different AKN elements depending on jurisdiction, breaking cross-jurisdictional comparability.

## Decision

**§ maps to AKN `<article>` in all jurisdictions.** The Bluebell keyword is `ARTICLE` and the eId abbreviation is `art`.

This applies to: DE, AT, CZ, SK, DK, EE, SE, FI, HU, and any future jurisdiction using § as the basic unit.

The sole exception is when a jurisdiction's Constitution uses a different term (e.g., Hungary's `Cikk` for constitutional articles); this is a different element entirely and is already modelled correctly.

## Rationale

1. **LegalDocML.de precedent**: Germany (the originator of the § convention) maps it to `article` in their official AKN profile.
2. **Structural equivalence**: § is the basic independently citable provision in all these jurisdictions. AKN `article` is the element for the basic citable provision in civil law traditions.
3. **Cross-jurisdictional consistency**: Using `section` for Hungarian § while `article` for German § makes the database internally inconsistent for structurally identical legislation.
4. **The English word "section" is misleading**: njt.hu translates § as "section" in English, but the structural role is that of a civil law article. The local terminology should not override the structural analysis.

## Consequences

- Hungary's `act` and `rendelet` document classes change `basic_unit` from `"section"` to `"article"`, `bluebell_keyword` from `SECTION` to `ARTICLE`, and `eid_abbrev` from `sec` to `art`.
- The `alaptorvenye` (Constitution) document class is unaffected; it already uses `article` for Cikk.
- Any future § jurisdiction must use `article` mapping.
