# Changelog

## 0.2.0 — unpublished

Eleven breaks, so the minor moves, as `VERSIONING.md` prescribes while the
major is zero. Enumerated by running every year-resolution grid cell and edge
row through the 0.1.0 and 0.2.0 callers (`resolve_year`, `gregorian_year`,
`resolve_descriptors`) and grouping every differing cell; the raw table is on
the pull request. Each line names the input shape that triggers it. Verified
against the demo holdings on 17 September 2026: no jurisdiction with a
non-Gregorian calendar (af, et, ir, jp, kp, ma, np, sa, th, tw) has any law
stored, no stored law has a year below 1000, and no work URI carries a one- to
three-digit year segment, so no shipped row moves.

1. A date field the model left unlabelled is read as written; 0.1.0 converted
   its year through the jurisdiction's calendar, filing a Gregorian `1968-09-09`
   under 1425 in a Buddhist-Era jurisdiction. Converted now only where a
   declared title grammar shows the year is local.
2. `to_gregorian_year(..., month=)` read a Gregorian month of a Bikram Samvat
   year the wrong way round, filing April to December under the later year.
   Corrected against the calendar (1 Baisakh 2080 was 14 April 2023); the new
   `month_grid` keyword names the grid a month is on, defaulting to Gregorian.
3. A three-digit year is carried as a four-digit segment and stored as its
   number: `622` files under `/0622/` where 0.1.0 minted a URI
   `is_citable_work_uri` refused, and a converted year below 1000 is carried
   the same way instead of dropped.
4. The sentinels `0000` and `0001` in a year field are no year; 0.1.0 returned
   them, and `0000` crashed the document-class match.
5. A five-digit year run (`12024`, in a year or date field) is no year; 0.1.0
   converted it to `11481` and crashed the class match.
6. A date field not shaped as a date (`2024-01-01junk`, a three-digit day,
   `unknown`) states nothing: no year run and no date. 0.1.0 split it on a
   dash and converted what came first, or passed it through as the work date.
   A date-shaped value with a day the month does not hold (`2024-02-31`,
   `2511-04-31`) keeps its year run and emits no date; 0.1.0 emitted it.
7. A year field in native digits (`๒๕๑๑`) is returned in ASCII by
   `resolve_year`; 0.1.0 returned the native digits while storing `2511`.
8. A date labelled with a calendar the jurisdiction does not declare is read
   as written; 0.1.0 retried its year through the jurisdiction's own rule.
9. A work date is emitted only where its year is the URI year; 0.1.0 emitted
   `2511-09-09` beside a URI year of 2020 and `unknown` beside any year.
10. The stored year is the URI year wherever the URI has one; 0.1.0 stored
    `None` where the date field held no date while the URI took the title's
    year, and stored a raw local year where the URI took the converted one.
11. A labelled year converts by the jurisdiction's declared rule; 0.1.0 used
    the generic table for that calendar and ignored a declared epoch.
12. `provision_embeddings` is partitioned by `jurisdiction_id` and carries
    `version_id` (migration 0020): the primary key is `(id, jurisdiction_id)`,
    the unique key `(provision_id, model_id, jurisdiction_id)`, and each
    partition holds its own HNSW index, built from
    `docs/runbooks/vector-index-build.md` on a populated database. A scoped
    search reads one partition's index instead of scanning every embedding in
    scope; the dense arm's `hnsw.iterative_scan` is `relaxed_order`.
13. `upsert_embedding` reads the provision's version and jurisdiction itself.
    No partition is created at run time: a jurisdiction created after 0020
    writes to the DEFAULT partition, which has its own index, until a
    migration promotes it with `codify.storage.partitions.promote_sql`.

Three more groups of differing cells are reachable only through config fields
0.1.0 could not load, so they are capability rather than breaks: a dated line
the source states is read where a date grammar is declared; a labelled local
date converts with its month and the new-year reform where a Gregorian grid
and a reform year are declared; and such a date's month and day carry into the
work date.

## 0.1.0 — private extraction candidate

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
