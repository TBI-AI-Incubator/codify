# Changelog

## Unreleased — private extraction candidate

No release has been published from this repository. The package version is a
candidate identifier, not evidence of a released tag or a benchmark run.

- Standalone Python library and CLI with selected public-reference and synthetic
  jurisdiction configurations. Missing configurations raise
  `JurisdictionDataMissing`; `try_load_config` supports optional lookup.
- Acquisition, AKN structuring and validation, retrieval, comparison, translation
  and optional PostgreSQL storage are included.
- Commercial lens implementations and catalogues are excluded. Generic
  scheme-match reads accept an optional `remediation_templates` mapping; callers
  requiring enrichment must provide it explicitly.
- Test fixtures and documentation have been adapted for a standalone checkout.
  Database and provider tests remain separate from the default offline CI.
- `to_gregorian_year(..., month=)` read a Gregorian month of a Bikram Samvat
  year the wrong way round, filing April to December under the later Gregorian
  year. Corrected against the calendar (1 Baisakh 2080 was 14 April 2023). No
  caller passed a month before this change, so no stored year moved; the new
  `month_grid` keyword names the grid a month is on and defaults to Gregorian.
- A three-digit year files as a four-digit URI segment (`622` under `/0622/`),
  where it previously produced a URI `is_citable_work_uri` refused. Verified
  against the demo holdings on 17 September 2026: no stored law has a year
  below 1000 and no work URI carries a one- to three-digit year segment, so no
  shipped corpus moves.

Prior monorepo benchmark numbers are not results for this candidate. The
synthetic retrieval evaluator can be run separately against a disposable
PostgreSQL database; it does not measure accuracy on a real statute book.
