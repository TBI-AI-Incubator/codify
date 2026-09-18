# codify

Codify turns a statute book into law a machine can read: each act structured as Akoma
Ntoso 3.0, the open standard for legal data, addressable by FRBR URI and citable to the
provision, with quoted amending text lifted out of the prose into markup a machine can
address.

Clear, citable law is public infrastructure, and that corpus is the point. Compliance
assessment, gap analysis and the rest are what it makes possible, and each is only as good
as the structure beneath it, so this package is the structure: acquisition, transcription,
anchor-driven structuring, retrieval and comparison. A document structured from a scan
carries a coverage measurement and the validator's findings; a document already in Akoma
Ntoso is validated too, but there is no scan to measure coverage against. Confidence
scores appear where something measures them rather than as a default.

The pipeline is Apache 2.0 and stands alone. CentreAI builds commercial services around
it; nothing here needs them, and a government can run this without us.

This package is the core. Everything done to legal text lives here as functions and small
classes you can import. HTTP routing and workflow orchestration are not part of it; those
live in the service that imports this one.

## Quick start

You need:

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- `poppler` for scanned PDFs (`brew install poppler` / `apt install poppler-utils`); text
  input needs nothing
- a chat model behind an OpenAI-compatible endpoint. A Gemini API key is the shortest
  route; a LiteLLM gateway works the same way
- Docker, only for the database tests

```bash
uv sync
cp .env.example .env   # add your key
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
```

The sample is a five-page synthetic act from `xa`, a fictional jurisdiction shipped for
exactly this. The run takes under a minute and costs a few cents. `bundle/` then holds:

| File                | What it is                                                     |
| ------------------- | -------------------------------------------------------------- |
| `pages/`            | the page images the model read                                 |
| `source.txt`        | the transcription                                              |
| `anchors.jsonl`     | every structural marker found, with the pass that found it     |
| `coverage.json`     | the numbers expected against the numbers captured              |
| `scaffold.bluebell` | the deterministic skeleton, before any body text               |
| `final.akn.xml`     | the Akoma Ntoso document                                       |
| `validator.json`    | the validator's findings                                       |
| `manifest.json`     | the summary printed at the end, with the model and config hash |

Nothing is written to a database.

Read `anchors.jsonl` before `final.akn.xml`: a body is only filled under a basic unit
(section, article) the scanner anchored. A scan that found containers and no basic unit
logs `body_fill_skipped` and ships the skeleton; one that found nothing at all logs
`scaffold_no_anchors` and keeps the text verbatim. The bundled scan prints its section
numbers in the margin, which the `xa` scanner does not yet read, so today it anchors the
six parts and no sections; that is the open issue on the samples.

Two lines in the log are worth knowing. `layout_pass_failed … OcrNotConfigured` says the
optional second OCR engine (Azure AI Foundry) is not set up, so each scanned page is read
once by the chat model's vision route rather than twice; the run continues. `page_diverted_to_ocr`
says a page had no usable text layer and was rasterised. `--quiet` drops the per-event
progress; the structured log stays.

Structuring is anchor-driven: a deterministic skeleton from the jurisdiction config, then
the model fills the bodies window by window. `codify scan-corpus <dir> --jurisdiction xa`
runs the skeleton pass alone over a directory of `.txt` sources, with no key and no
database, which is the way to see what the scanner claims before spending anything.

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

`POSTGRES_URL` names the database; unset, it is the compose one.

## Configuration

The CLI reads `.env` from the working directory (or any parent); an exported variable
wins. `pytest` and `alembic` read only the environment, so export what they need.
`.env.example` lists everything.

| Variable                                        | Purpose                                                  |
| ----------------------------------------------- | -------------------------------------------------------- |
| `LITELLM_BASE_URL`, `LITELLM_API_KEY`           | the chat endpoint and its key                            |
| `LITELLM_MODEL`                                 | body-fill model; `--model` overrides it per run          |
| `POSTGRES_URL`                                  | database tests and migrations; defaults to the compose DB |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` | optional second OCR engine                               |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`    | optional tracing                                         |

## Jurisdictions

A configuration travels when its own `config.json` carries `synthetic` or
`public_reference`. `codify/open_wheel.py` holds that rule and the wheel's build hook
applies it, so the set follows the flags rather than a list that drifts from them.

Synthetic jurisdictions carry invented law in real legislative shapes, so the suite can
assert against known-correct structure without redistributing anyone's corpus. Public
reference configurations describe jurisdictions that publish their own law openly.

By default, a source checkout reads its own `data/`; an installed package reads its
bundled data. To point at another dataset, set `CODIFY_DATA_ROOT` to an absolute path
holding `jurisdictions/` and `frameworks/` before Python starts; there is no merge with
the bundled data, and a missing or relative root raises.

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

The default suite needs no database and no key:

```bash
uv sync --group dev --extra migrations
uv run pytest tests -m "not integration and not live_llm" -q
```

About 4,000 tests in a little over a minute. This is what CI runs. Tests for
configurations not shipped here skip.

The database tests want the same Postgres as [Store, search, compare](#store-search-compare):

```bash
docker compose up -d --wait postgres          # first build takes a minute or two
uv run alembic -c alembic.ini upgrade head
REQUIRE_DB=1 uv run pytest tests -m "integration and not live_llm" -q
```

Some tests commit or recreate data; never point `POSTGRES_URL` at a database you care
about. `CODIFY_PG_PORT` moves the host port if 5432 is taken; export `POSTGRES_URL` to
match. `live_llm` tests call a real model through a LiteLLM gateway and can incur charges.
See [the test guide](docs/offline-suite.md).

CI also runs Ruff, strict mypy and a wheel build; the exact commands are in
`.github/workflows/ci.yml`.

## Standards

Codify targets Akoma Ntoso 3.0 and includes schema checks. FRBR URIs identify works,
expressions and manifestations. Schema validity does not establish accurate transcription
or universal downstream compatibility. See [interoperability scope](docs/akn4eu-divergences.md).

## More

- `docs/architecture.md`
- `docs/ocr-cascade.md`
- `docs/decisions/` — the decision records
- `CONTRIBUTING.md`
- `SECURITY.md`
- `AGENTS.md`, beside this file — conventions for a coding agent working in this package

Codify is built by CentreAI at the
[Tony Blair Institute for Global Change](https://institute.global).
The hosted product wraps this core with tenancy, access control, an audit trail and support.
