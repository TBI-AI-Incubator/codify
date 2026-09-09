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

Prior monorepo benchmark numbers are not results for this candidate. The
synthetic retrieval evaluator can be run separately against a disposable
PostgreSQL database; it does not measure accuracy on a real statute book.
