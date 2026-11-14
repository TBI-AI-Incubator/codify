# Codify

[![CI](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/TBI-AI-Incubator/codify/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/codify-core)](https://pypi.org/project/codify-core/)
[![Python](https://img.shields.io/pypi/pyversions/codify-core)](https://pypi.org/project/codify-core/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Codify turns a country's legislation into structured, citable, machine-readable law. It
reads scanned gazettes, PDFs, plain text and publisher XML, writes
[Akoma Ntoso](docs/concepts/akoma-ntoso.md) XML (the OASIS standard for legal
documents), and stores, searches and serves the result through a command line, an HTTP
API, an MCP server for AI agents and a local web app.

Rules in a per-jurisdiction config find each law's structure and give every provision
an identifier built from its place in the law. A model you choose reads scanned pages,
reads the title, date and number that build the law's identifier, and fills in the
text. Every run keeps its evidence (page images, transcribed text, detected markers,
findings and a grade) so you can check the result against the source.

This repository is the open-source core, on PyPI as `codify-core` under the Apache 2.0
licence. It runs on your own machines without the hosted Codify Platform.

## Try it

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/) and Git. This step
needs no model and no API key:

```bash
git clone https://github.com/TBI-AI-Incubator/codify.git
cd codify
uv sync
uv run python -c "from codify.jurisdictions import load_config; print(load_config('xa').name)"
```

```text
Atlantis
```

`xa` is Atlantis, a fictional jurisdiction for examples and tests. From here:

| To | Read |
| --- | --- |
| See what Codify does and where it stops | [What Codify is](docs/start/overview.md) |
| Take a short act from text to a served law, with no model | [Quickstart](docs/start/quickstart.md) |
| Install from a checkout or PyPI, and set up PostgreSQL | [Install](docs/start/install.md) |
| Run a scanned act through the full pipeline with a model | [First ingest](docs/start/first-ingest.md) |
| Explore the library in Python | [Notebooks](docs/notebooks/README.md) |

## Limits

- The checks are structural: they do not show a text is complete, current or legally
  accurate. People who know the law review the output against its source.
- Scanned pages need a vision-capable model behind an OpenAI-compatible endpoint; there
  is no built-in OCR engine.
- `codify serve` and `codify mcp` have no authentication: keep them on localhost or
  behind a proxy that authenticates. Server ingest runs are held in memory and lost on
  restart; stored documents stay in PostgreSQL.
- Alpha: while the major version is 0, a minor release can change output and
  interfaces. See [VERSIONING.md](VERSIONING.md).

## Project

- [Contributing](CONTRIBUTING.md), [code of conduct](CODE_OF_CONDUCT.md) and
  [security policy](SECURITY.md)
- [Changelog](CHANGELOG.md) and [licence](LICENSE)

Codify is built by CentreAI at the
[Tony Blair Institute for Global Change](https://institute.global).
For the hosted Codify Platform, visit [codify.centreai.global](https://codify.centreai.global).
