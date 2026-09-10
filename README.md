# codify

Codify turns a statute book into law a machine can read: each act structured as Akoma
Ntoso 3.0, the open standard for legal data, addressable by FRBR URI and citable to the
provision, with quoted amending text lifted out of the prose into markup a machine can
address.

Clear, citable law is public infrastructure, and that corpus is the point. Compliance
assessment, gap analysis and the rest are what it makes possible, and each is only as good as
the structure beneath it, so this package is the structure: acquisition, transcription,
anchor-driven structuring, retrieval and comparison. A document structured from a scan carries
a coverage measurement and the validator's findings; a document already in Akoma Ntoso is
validated too, but there is no scan to measure coverage against. Confidence scores appear where
something measures them rather than as a default.

The pipeline is Apache 2.0 and stands alone. CenterAI builds commercial services around
it; nothing here needs them, and a government can run this without us.

This package is the core. Everything done to legal text lives here as functions and small
classes you can import. HTTP routing and workflow orchestration are not part of it; those
live in the service that imports this one.

## Install

```bash
uv sync
```

## Run it on one document

```bash
uv run codify ingest-one <pdf|txt> --jurisdiction <code> --out bundle/
```

The bundle holds the page images, every anchor with the pass that produced it, the
coverage measurement, the scaffold before body-fill, the final AKN and the validator
findings. Nothing is written to a database.

Structuring is anchor-driven: a deterministic skeleton from the jurisdiction config,
then a language model fills the bodies window by window. The skeleton is reproducible
without a key; the body-fill needs one.

## Jurisdictions

A configuration travels when its own `config.json` carries `synthetic` or
`public_reference`. `codify/open_wheel.py` holds that rule and the wheel's build hook
applies it, so the set follows the flags rather than a list that drifts from them. To see
what ships, read the flags or run the export.

Synthetic jurisdictions carry invented law in real legislative shapes, so the suite can
assert against known-correct structure without redistributing anyone's corpus. Public
reference configurations describe jurisdictions that publish their own law openly.

By default, a source checkout reads its own `data/`; an installed package reads its
bundled data and does not search neighbouring directories.

Operators can explicitly select a different dataset before starting Python:

```sh
CODIFY_DATA_ROOT=/absolute/path/to/data uv run codify ingest-one input.txt --jurisdiction xa --out bundle/
```

The root must contain `jurisdictions/` and `frameworks/` for consumers of both datasets.
Copy the bundled data into an operator-owned directory before adding or replacing profiles;
retain the registry and supranational metadata beside the jurisdiction directories.
A missing, relative or invalid root, or a missing requested dataset directory, raises an
error. There is no fallback or merge with bundled data when the setting is present.
Configuration files still undergo the normal schema validation when loaded.

Set this environment variable before importing Codify: consumers keep data paths and
loaded configurations in memory. Restart the process after changing it. The setting
selects runtime data only; it does not change what the build hook distributes.

To add one, see `docs/jurisdictions/adding-a-jurisdiction.md`.

## Layout

- `codify/akn/` — AKN 3.0 element model, parsing and emitting, eIds, references, schema validation
- `codify/pipeline/` — bytes to AKN. Format dispatchers under `formats/`, enrichment passes under `enrich/`
- `codify/acquisition/` — per-jurisdiction source adapters, manifests, rate limiting
- `codify/embed/` — provider-agnostic embedding client over an OpenAI-compatible endpoint
- `codify/retrieve/` — hybrid retrieval over provisions: dense plus BM25, RRF-fused
- `codify/compare/` — compliance comparator: aligner, prompts, validated model output
- `codify/storage/` — typed Postgres access
- `codify/lenses/` — generic plugin types and the lens registry
- `codify/repair/` — AKN repair agent: per-finding grounding, transactional edits
- `codify/translate/` — anchored translation: batching, clause parity, quality grading
- `codify/core/` — shared model client, tracing, i18n, log redaction

`frbr.py` builds FRBR URIs. `jurisdictions.py` loads configs.

## Tests

```bash
uv sync --group dev --extra migrations
uv run pytest tests -m "not integration and not live_llm" -q
```

This is the default CI test scope. Database and provider tests are separate;
see [the test guide](docs/offline-suite.md) for setup and limitations.

## Standards

Codify targets Akoma Ntoso 3.0 and includes schema checks. FRBR URIs identify
works, expressions and manifestations. Schema validity does not establish
accurate transcription or universal downstream compatibility. See
[interoperability scope](docs/akn4eu-divergences.md).

## More

Paths below are from the repository root.

- `docs/architecture.md`
- `docs/ocr-cascade.md`
- `docs/decisions/` — the decision records
- `CONTRIBUTING.md`
- `SECURITY.md`
- `AGENTS.md`, beside this file — conventions for a coding agent working in this package

Codify is built by CenterAI at the
[Tony Blair Institute for Global Change](https://institute.global).
The hosted product wraps this core with tenancy, access control, an audit trail and support.
