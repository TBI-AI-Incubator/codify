# Codify

Codify turns a statute book into law a machine can read: each act structured as
[Akoma Ntoso 3.0](http://akomantoso.info/?page_id=27),
the open standard for legal documents, addressable by
[FRBR](https://repository.ifla.org/items/54925d49-b08d-4aeb-807c-1b509ec40b55) URI and
citable to the provision, with quoted amending text lifted out of the prose into markup a
machine can address.

This repository provides all the components of the core pipeline: acquisition,
transcription, anchor-driven structuring, retrieval and comparison. Documents converted
from scans include coverage metrics and validation reports, while pre-existing Akoma Ntoso
documents run through validation alone.

The code is Apache 2.0 and fully standalone. While TBI offers commercial services built on
top of it, this core pipeline requires no external proprietary services and can be run
independently.

This package contains the domain logic for manipulating legal text, exposed as reusable
functions and classes. Application-level concerns like HTTP routing and workflow
orchestration are downstream.

## Quick start

You need:

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- `poppler` for scanned PDFs (`brew install poppler` / `apt install poppler-utils`); text
  input needs nothing
- access to a chat model behind an OpenAI-compatible endpoint. The repo makes it easy to
  add a Gemini API key, but a LiteLLM gateway will also easily work
- Docker, only for the database tests

```bash
uv sync
cp .env.example .env   # add your key
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
```

The sample command processes a five-page synthetic act from `xa`, a fictional test
jurisdiction. It takes under a minute, costs a few cents in model API calls, and outputs
locally without touching any database.

The generated `bundle/` directory contains:

| File                | Contents                                                       |
| ------------------- | -------------------------------------------------------------- |
| `pages/`            | Rendered page images fed to the vision model                   |
| `source.txt`        | Extracted text transcription                                   |
| `anchors.jsonl`     | Structural markers detected during the scanning pass           |
| `coverage.json`     | Provision count metrics (expected vs. captured)                |
| `scaffold.bluebell` | Structural skeleton generated prior to filling provision text  |
| `final.akn.xml`     | Generated Akoma Ntoso 3.0 document                             |
| `validator.json`    | Schema and structural validation results                       |
| `manifest.json`     | Run metadata, including model identifiers and config hashes    |

Structuring follows an anchor-driven model: a deterministic skeleton is parsed using the
jurisdiction's rules, and the LLM then fills the text within each anchored block.

**Inspection order.** Inspect `anchors.jsonl` before `final.akn.xml`. The model only
populates text inside anchored basic units (e.g., sections, articles). If a pass
identifies higher-level containers but no basic units, it logs `body_fill_skipped` and
exports only the skeleton. If no anchors are found, it logs `scaffold_no_anchors` and
preserves the source text unparsed. (Note: the bundled `xa` fixture places section numbers
in the margin, which the parser does not currently extract; it anchors six parts but no
sections.)

**Dry runs.** To inspect detected anchors across `.txt` files without making model calls
or requiring a database, run the skeleton pass directly:

```bash
codify scan-corpus <dir> --jurisdiction xa
```

The summary is JSON on stdout; findings about individual files go to stderr, so
`2>/dev/null` leaves the summary alone.

**Log messages.**

- `layout_pass_failed ... OcrNotConfigured`: the optional secondary OCR engine (Azure AI
  Foundry) is not configured. The pipeline reads the scan once via the model's vision
  endpoint rather than twice, and the run otherwise proceeds.
- `page_diverted_to_ocr`: the PDF page lacked an extractable text layer and was
  rasterised for OCR.

## Configuration

The CLI loads environment variables from a `.env` file in the working directory or parent
directories; explicitly exported shell variables take precedence. Note that `pytest` and
`alembic` do not read `.env` files automatically and require variables to be exported in
your environment. See `.env.example` for all available options.

| Variable                | Purpose                                                                   |
| ----------------------- | ------------------------------------------------------------------------- |
| `LITELLM_BASE_URL`      | Base URL for the OpenAI-compatible chat endpoint                          |
| `LITELLM_API_KEY`       | API key for the chat endpoint                                             |
| `LITELLM_MODEL`         | Default model used for body fill (overridden by `--model`)                |
| `POSTGRES_URL`          | Postgres connection string for migrations and tests (defaults to local Compose service) |
| `AZURE_OPENAI_ENDPOINT` | Endpoint for the optional secondary Azure AI Foundry OCR engine           |
| `AZURE_OPENAI_API_KEY`  | API key for the optional Azure OCR engine                                 |
| `LANGFUSE_PUBLIC_KEY`   | Public key for optional Langfuse tracing                                  |
| `LANGFUSE_SECRET_KEY`   | Secret key for optional Langfuse tracing                                  |

## Jurisdictions

Configurations are included in distributed package wheels if their `config.json` sets
either `synthetic` or `public_reference` to true. This filtering is enforced by
`codify/open_wheel.py` at build time to prevent the bundled set from drifting out of sync.

- **Synthetic jurisdictions**: contain mock legislation formatted to real-world
  legislative structures, allowing test suites to assert against known-good parses
  without distributing copyrighted corpora.
- **Public reference configurations**: cover jurisdictions that publish their legal texts
  openly.

By default, local source checkouts read from `./data/`, while installed package
distributions read bundled package data. To supply a custom dataset, set
`CODIFY_DATA_ROOT` to an absolute path containing `jurisdictions/` and `frameworks/`
directories before starting Python. Relative paths are rejected, and custom data roots
completely replace bundled data.

To add a jurisdiction, see `docs/jurisdictions/adding-a-jurisdiction.md`.

## Layout

- `codify/akn/`: AKN 3.0 element models, parsing, rendering, eId generation, reference
  resolution, and schema validation
- `codify/pipeline/`: End-to-end ingestion from raw bytes to AKN. Contains input format
  parsers (`formats/`) and structural enrichment passes (`enrich/`).
- `codify/acquisition/`: Source adapters, scrape manifests, and rate limiting for
  jurisdiction data sources.
- `codify/embed/`: Provider-agnostic text embedding client over OpenAI-compatible
  endpoints.
- `codify/retrieve/`: Hybrid retrieval over statutory provisions using dense embeddings
  and BM25 fused via Reciprocal Rank Fusion (RRF).
- `codify/compare/`: Statutory compliance comparator, including alignment logic, prompt
  templates, and schema-validated model outputs.
- `codify/storage/`: Typed PostgreSQL data access layer.
- `codify/lenses/`: Analysis plugins and the extensible lens registry.
- `codify/repair/`: Automated AKN repair agent performing finding-grounded, transactional
  XML edits.
- `codify/translate/`: Structure-preserving legal translation with batching, clause parity
  checks, and quality scoring.
- `codify/core/`: Shared LLM client, OpenTelemetry/Langfuse tracing, internationalization,
  and log redaction.
- `codify/frbr.py`: FRBR URI generation and parsing.
- `codify/jurisdictions.py`: Jurisdiction configuration loader and schema validator.

## Tests

The default suite requires neither a database nor an API key:

```bash
uv sync --group dev --extra migrations
uv run pytest tests -m "not integration and not live_llm" -q
```

About 4,000 tests run in a little over a minute; CI runs the same command. Tests for
configurations not shipped in this repository are skipped.

The database tests require a disposable Postgres with pgvector and pg_textsearch, which
the compose file builds:

```bash
docker compose up -d --wait postgres          # first build takes a minute or two
uv run alembic -c alembic.ini upgrade head
REQUIRE_DB=1 uv run pytest tests -m "integration and not live_llm" -q
```

Some tests commit or recreate data, so never point `POSTGRES_URL` at a database you
need. `CODIFY_PG_PORT` changes the host port if 5432 is taken; export `POSTGRES_URL` to
match. Tests marked `live_llm` call a real model through a LiteLLM gateway and incur
charges. See [the test guide](docs/offline-suite.md).

CI also runs Ruff, strict mypy and a wheel build; the exact commands are in
`.github/workflows/ci.yml`.

## Standards

Codify targets Akoma Ntoso 3.0 and validates output against the schema. FRBR URIs
identify works, expressions and manifestations. Schema validity does not guarantee an
accurate transcription or compatibility with every downstream tool; see
[interoperability scope](docs/akn4eu-divergences.md).

## More

- `docs/architecture.md`
- `docs/ocr-cascade.md`
- `docs/decisions/`: architecture decision records
- `CONTRIBUTING.md`
- `SECURITY.md`
- `AGENTS.md`: conventions for coding agents working in this repository

Codify is built by CentreAI at the
[Tony Blair Institute for Global Change](https://institute.global). The hosted product
wraps this core with tenancy, access control, an audit trail and support.
