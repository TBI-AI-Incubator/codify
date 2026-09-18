# Codify

[![CI](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/codify-core)](https://pypi.org/project/codify-core/)
[![Python](https://img.shields.io/pypi/pyversions/codify-core)](https://pypi.org/project/codify-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Akoma Ntoso](https://img.shields.io/badge/Akoma%20Ntoso-3.0-lightgrey)](http://akomantoso.info/?page_id=27)

Codify turns a statute book into law a machine can read: each act structured as
[Akoma Ntoso 3.0](http://akomantoso.info/?page_id=27),
the open standard for legal documents, addressable by
[FRBR](https://repository.ifla.org/items/54925d49-b08d-4aeb-807c-1b509ec40b55) URI and
citable to the provision, with quoted amending text lifted out of the prose into markup a
machine can address.

This repository provides all the components of the core pipeline: acquisition,
transcription, anchor-driven structuring, retrieval and comparison. Documents converted
from scans include coverage metrics and validation reports, while pre-existing Akoma Ntoso
documents skip the scan passes and are normalised (identifiers, unique eIds) and validated.

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

To use the library from your own project, `uv add codify-core` (or `pip install
codify-core`); the extras are `serve`, `mcp` and `migrations`. To work in this
repository:

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
| `pages/`            | Page images rendered for inspection after the run              |
| `source.txt`        | Extracted text transcription                                   |
| `anchors.jsonl`     | Structural markers detected during the scanning pass           |
| `coverage.json`     | Provision count metrics (expected vs. captured)                |
| `scaffold.bluebell` | Structural skeleton generated prior to filling provision text  |
| `final.akn.xml`     | Generated Akoma Ntoso 3.0 document                             |
| `validator.json`    | Structural validation findings                                 |
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

**Dry runs.** To inspect detected anchors across `.txt` and text-layer `.pdf` files
without making model calls or requiring a database, run the anchor scan directly:

```bash
uv run codify scan-corpus <dir> --jurisdiction xa
```

The summary is JSON on stdout; diagnostics such as an empty sweep go to stderr, and
`--per-document` writes one row per scanned document to a path of your choosing.

**Log messages.**

- `layout_pass_failed ... OcrNotConfigured`: the optional secondary OCR engine (Azure AI
  Foundry) is not configured. The pipeline reads the scan once via the model's vision
  endpoint rather than twice, and the run otherwise proceeds.
- `page_diverted_to_ocr`: the page's extracted text layer was rejected (the event's
  `reason` names the test it failed: too short, garbled, letter-spaced, presentation
  forms, divergent from the scan, or a visible annotation) and the page was rasterised
  for OCR.

## Store, search, compare

The bundle is the end of `ingest-one`. To search it, or set it beside another version,
load it into Postgres. The compose file builds one with pgvector and pg_textsearch:

```bash
docker compose up -d --wait postgres          # first build takes a minute or two
uv run alembic -c alembic.ini upgrade head
uv run codify load bundle/ --embed
uv run codify search "right of access" --jurisdiction xa
```

`load` reads `final.akn.xml` and `manifest.json` from a bundle and writes the law, its
version and its provisions; the jurisdiction and the title come from the manifest, so a
bundle needs no flags. A bare `.akn.xml` has no manifest and needs `--jurisdiction` and
`--title`. `--embed` also embeds the provisions, which `search` needs. Loading the same
file twice returns the version it already holds.
`search` runs the hybrid retrieval (lexical and vector, fused) and prints one JSON match
per line, best first, with the provision's eId, score and text; it exits 1 when nothing
matched.

Embeddings go to an OpenAI-compatible embeddings endpoint. `EMBEDDING_BASE_URL`,
`EMBEDDING_API_KEY` and `EMBEDDING_MODEL` name it; unset, the `LITELLM_*` chat settings
are used, so one gateway can serve both.

```bash
uv run codify compare a.akn.xml b.akn.xml --out report.json
uv run codify compare <version-id> <version-id>
```

`compare` aligns the second document's provisions against the first, article by
article, through the chat model, and writes a report with a summary (aligned, partial,
gap) and every alignment. Either side is an AKN file or a stored version id. It costs a
model call per provision.

`POSTGRES_URL` names the database. Unset, it is the compose one while `ENVIRONMENT` is
unset, `localhost` or `development`; under any other value an unset URL is an error.

## Serve

The same reads and the ingest, behind HTTP, for a script or a UI that is not on the box:

```bash
uv sync --extra serve
uv run codify serve                            # http://127.0.0.1:8000, docs at /docs
curl -F file=@act.pdf -F jurisdiction=xa localhost:8000/runs/ingest
curl -N localhost:8000/runs/<run-id>/stream    # server-sent events until the run ends
```

Reads: `/jurisdictions`, `/jurisdictions/{code}`, `/laws`, `/laws/{id}`,
`/versions/{id}` (with the AKN), `/search?q=&jurisdiction=`. Ingest: `POST /runs/ingest`
(a file) or `POST /runs/ingest-url` (a URL) return a run at once; `/runs/{id}` is its
state, `/runs/{id}/stream` replays every event so far and then follows it, and
`/runs/{id}/cancel` and `/runs/{id}/retry` do what they say. A succeeded ingest is stored,
so the reads see it. `ingest-url` takes an EU publications URL (eur-lex or publications.europa.eu) under
jurisdiction `eu`, the only lane that fetches;
a PDF goes through `ingest`. Runs live in the server's memory: a restart forgets them, each
run says so (`lost_on_restart`), and the 200 most recent finished runs stay readable. There
is no authentication; bind it to localhost or put it behind something that has.

### Contract

`contract/openapi.json` is the server's OpenAPI schema, generated from the routes'
response models (`codify/serve/schemas.py` and the library models they carry, such as
`JurisdictionConfig`); a test fails when it drifts. After a route change:

```bash
uv run codify serve --openapi > contract/openapi.json
```

A client generates its types from that file (the UI does, with `openapi-typescript`),
so the schema is the one place the two agree.

### MCP server

The same reads, and `compare`, as tools for an agent over the Model Context Protocol:

```bash
uv sync --extra mcp
uv run codify mcp                     # stdio, for a client that launches the server
uv run codify mcp --http --port 8001  # streamable HTTP at http://127.0.0.1:8001/mcp
```

A client that speaks stdio launches the command itself; this is the shape most take:

```json
{
  "mcpServers": {
    "codify": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/codify", "codify", "mcp"],
      "env": { "POSTGRES_URL": "postgresql://codify:codify@localhost:5432/codify" }
    }
  }
}
```

The tools, all reads:

- `search_provisions(query, jurisdiction, language?, k?)`: hybrid search over one
  jurisdiction's provisions; matches best first, each with its provision id, eId, score
  and text. Needs the embeddings endpoint, as `search` does; unset, the tool says so.
- `list_laws(jurisdiction?, doctype?, year?, q?, limit?, offset?)`: a page of stored laws
  with their ids.
- `get_law(law_id)`: one law's fields, its jurisdiction and every stored version.
- `get_version(version_id, include_xml?)`: one version's metadata; with `include_xml`,
  the Akoma Ntoso XML, cut at a million characters and flagged when cut.
- `list_jurisdictions()`: every configured jurisdiction's code, name and languages.
- `get_jurisdiction(code)`: a jurisdiction's names, tradition, calendar, languages and
  document classes.
- `compare_versions(reference_version_id, domestic_version_id)`: the `compare` report,
  each provision of the reference assessed against the domestic version. Needs the chat
  and embeddings endpoints and costs a model call per reference provision that carries
  text; refused above 200 of them.

A failure reads back as the tool's error with a plain message. `--http` binds to
localhost unless `--host` says otherwise; there is no authentication here either.

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

A source checkout reads the repository's `data/` directory, wherever the command is
run from; an installed wheel reads the data bundled inside the package. To supply a custom dataset, set
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

For a detailed breakdown of test scopes, see [the test guide](docs/offline-suite.md).

Unit tests do not require an API key or a database:

```bash
uv sync --group dev --extra migrations
uv run pytest tests -m "not integration and not live_llm" -q
```

This runs the unit suite in about a minute and matches the standard CI check. Tests
for unbundled jurisdictions are skipped automatically.

**Note:** Integration tests require the same Postgres as
[Store, search, compare](#store-search-compare), with the pgvector and pg_textsearch
extensions:

```bash
# Start test database and apply schema migrations
docker compose up -d --wait postgres
uv run alembic -c alembic.ini upgrade head

# Run integration suite
REQUIRE_DB=1 uv run pytest tests -m "integration and not live_llm" -q
```

- **Database safety:** Integration tests write and drop data. Never set `POSTGRES_URL` to
  a production or shared database. If port 5432 is already bound locally, set
  `CODIFY_PG_PORT` and update `POSTGRES_URL` accordingly.
- **Live LLM tests:** Tests marked `live_llm` issue requests to the configured
  OpenAI-compatible gateway and incur API charges.
- **CI checks:** In addition to unit tests, CI enforces formatting, type checking, and
  wheel builds via Ruff, strict mypy, and `uv build` with the Hatchling backend (see
  `.github/workflows/ci.yml`).

## Standards

Codify targets the Akoma Ntoso 3.0 specification. Generated documents pass a structural
validator whose findings ride the bundle and the run; the OASIS schema is checked on one
acquisition route only, not on every output.
FRBR URIs identify works, expressions and manifestations, and eIds address the individual
provision within them.

Structural validity alone does not guarantee semantic fidelity to the source text or seamless
compatibility with external tooling. Internal structural conventions, such as how annex
content is inlined, can diverge from specific downstream profiles like AKN4EU or platforms
such as Indigo. You should validate intended interchange workflows using representative
documents directly within the consuming system. For details on compatibility boundaries
and known profile differences, see the
[interoperability scope](docs/akn4eu-divergences.md) documentation.

## More

- `docs/architecture.md`
- `docs/ocr-cascade.md`
- `docs/decisions/`: architecture decision records
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `AGENTS.md`: conventions for coding agents working in this repository

Codify is built by CentreAI at the
[Tony Blair Institute for Global Change](https://institute.global). For more information
on the vision and access to a hosted version, visit
[codify.centreai.global](https://codify.centreai.global).
