# Codify

[![CI](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/codify-core)](https://pypi.org/project/codify-core/)
[![Python](https://img.shields.io/pypi/pyversions/codify-core)](https://pypi.org/project/codify-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Akoma Ntoso](https://img.shields.io/badge/Akoma%20Ntoso-3.0-lightgrey)](http://akomantoso.info/?page_id=27)

Codify Core converts legal documents into structured data that you can inspect,
search and reuse. It accepts PDFs, text and existing
[Akoma Ntoso XML](http://akomantoso.info/?page_id=27), an open standard for legal documents.

A structured document keeps its articles, sections, tables and references identifiable.
You can search individual provisions, link to them and compare them with another document.
Each ingestion includes the extracted text and validation findings so you can check the
result against its source.

Use Codify Core through Python, the command line, a local web app, REST or MCP.
It is Apache-2.0 licensed and runs independently of **Codify Platform**, the hosted
application. Model-assisted features require a configured model endpoint; the default
example uses Gemini, and a compatible gateway can be configured instead.

## Quick start

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/) and access to an
OpenAI-compatible chat endpoint. For PDFs that need page rendering or OCR, install
Poppler (`brew install poppler` on macOS or `apt install poppler-utils` on Debian/Ubuntu).

```bash
git clone https://github.com/TBI-AI-Incubator/codify.git
cd codify
uv sync
cp .env.example .env
```

Set `LITELLM_BASE_URL`, `LITELLM_API_KEY` and `LITELLM_MODEL` in `.env` for your
provider. Then process the included synthetic act:

```bash
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf \
  --jurisdiction xa --out bundle/
```

This writes files to `bundle/` without using a database. It makes model calls;
runtime and cost depend on the model and source document. The sample belongs to
`xa`, a fictional jurisdiction used for testing.

Open `bundle/final.akn.xml` to see the structured document. Check `anchors.jsonl`
for the detected sections and articles, `coverage.json` for provision counts,
and `validator.json` for structural findings. The bundle also retains source text,
page images and run metadata. See [working with documents](docs/usage.md) for the
file list, troubleshooting and storage commands.

For text files and PDFs with a text layer, `scan-corpus` inspects structural
anchors without model calls. See the [offline scan example](docs/usage.md#inspect-an-ingestion).

## Choose an interface

| I want to… | Start here |
| --- | --- |
| Explore the library in Python | [Notebooks](docs/notebooks/README.md), starting with Codify 101 |
| Process files, store laws, search or compare documents | [Command-line guide](docs/usage.md) |
| Upload, browse and search in a browser | [Local web app](docs/interfaces.md#web-app) |
| Connect an application | [REST API](docs/interfaces.md#rest-api) and its OpenAPI schema |
| Give an agent access to stored laws | [MCP server](docs/interfaces.md#mcp-server) |

For use in another Python project, install `codify-core` with `uv add codify-core`
or `pip install codify-core`. Optional extras are `serve`, `mcp` and `migrations`.

Storage and search use PostgreSQL with pgvector and pg_textsearch. The repository's
Docker Compose file builds a local instance. Search needs an embeddings endpoint;
comparison needs chat and embeddings endpoints. The [storage guide](docs/usage.md#store-search-compare)
explains configuration and loading a bundle.

The local servers have **no built-in authentication**. Keep them on localhost or
behind an authenticated proxy. Ingestion runs are held in memory and lost on a
server restart; documents already stored in PostgreSQL remain available.

## Jurisdictions and output quality

Jurisdiction configurations define document types, numbering, dates and structural
patterns. Codify detects a document's structure using those rules, then uses a model
to fill the provision text. Missing or incorrect rules can leave provisions unstructured;
review the detected anchors as well as the final XML.

The package includes synthetic and public reference configurations. A configuration
is not a complete legal corpus or a guarantee of extraction quality. To supply your own,
see [adding a jurisdiction](docs/jurisdictions/adding-a-jurisdiction.md) and
[custom data roots](docs/usage.md#jurisdictions).

Generated output targets Akoma Ntoso 3.0 and includes structural validation findings.
It is **not automatically checked against the full OASIS schema on every ingestion**.
Structural checks do not establish that the text is legally accurate or complete.
Review important outputs against their sources and test them in the software that
will consume them. Known differences are documented in the
[interoperability guide](docs/akn4eu-divergences.md).

## Development

Offline tests need neither a database nor a model API key:

```bash
uv sync --group dev --extra migrations --extra serve --extra mcp
uv run pytest tests -m "not integration and not live_llm" -q
```

Use a disposable database for integration tests: they can write and delete data.
See the [test guide](docs/offline-suite.md) for setup and the separate tests that
make paid model calls.

- [Architecture](docs/architecture.md)
- [OCR and transcription](docs/ocr-cascade.md)
- [Contributing](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Licence](LICENSE)

Codify is built by CentreAI at the
[Tony Blair Institute for Global Change](https://institute.global).
For the hosted Codify Platform and more about the project, visit
[codify.centreai.global](https://codify.centreai.global).
