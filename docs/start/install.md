---
title: Install
description: Install Codify from a source checkout or as a package, start the PostgreSQL service, apply the migrations and set the environment.
sidebar:
  order: 3
---

You end with a working `codify` command, the bundled jurisdiction configs loading, and
a migrated PostgreSQL database.

## Before you start

| You need | For | Notes |
| --- | --- | --- |
| Python 3.12 or newer | everything | |
| [uv](https://docs.astral.sh/uv/) | the source checkout | Pins dependencies in `uv.lock`. |
| Git | the source checkout | |
| Poppler | ingesting PDFs | Renders PDF pages to images. `brew install poppler` (macOS), `apt install poppler-utils` (Debian, Ubuntu). |
| Docker with Compose | the database | |
| An OpenAI-compatible model endpoint | ingesting scans, search, comparison | Not needed here or for the [Quickstart](quickstart.md). |

## The idea in brief

The package `codify-core` imports as `codify` and installs one command, `codify`. Use a
**source checkout** to follow this guide, edit configs or run the web app; a **package
install** adds the library, configs, comparison frameworks and migration scripts to your
own project, without the samples, notebooks, web app or Compose file.

<details>
<summary>What each install contains</summary>

| | Source checkout | Package install |
| --- | --- | --- |
| The `codify` command and the Python library | yes | yes |
| Jurisdiction configs flagged `synthetic` or `public_reference` | yes, read from `data/` | yes, bundled inside the package |
| Sample documents and test fixtures | yes | no |
| Notebooks | yes | no |
| Web app (`apps/web`) | yes | no |
| Compose file and PostgreSQL image definition | yes | no |
| `codify migrate`, with the `migrations` extra | yes | yes |

</details>

<details>
<summary>Which commands need an extra, the database or a model</summary>

| Command | Extra | PostgreSQL | Model endpoint |
| --- | --- | --- | --- |
| `scan-corpus`, `segment` | none | no | no |
| `ingest-one` | none | no | chat |
| `load` | none | yes | embeddings, only with `--embed` |
| `search` | none | yes | embeddings |
| `compare` | none | only when a side is a stored version | chat and embeddings |
| `serve` | `serve` | yes | chat for PDF uploads, embeddings for search |
| `mcp` | `mcp` | yes | embeddings for search, chat and embeddings for comparison |
| Migrations | `migrations`, or the `dev` group | yes | no |

</details>

Every command and flag is in the [command reference](../reference/cli.md).

## Steps

### Clone and install

```bash
git clone https://github.com/TBI-AI-Incubator/codify.git
cd codify
uv sync --extra serve --extra mcp --extra migrations
```

`uv sync` installs exactly what you name (a later `uv sync --extra serve` removes the
`mcp` packages), so name every extra in one command. A plain `uv sync` installs the base
package and the `dev` group, which includes the migration tools.

### Check the install

```bash
uv run codify --help
```

There is no `--version` flag; read the version from the package metadata and load a
bundled config:

```bash
uv run python -c "from importlib.metadata import version; print(version('codify-core'))"
uv run python -c "from codify.jurisdictions import load_config; c = load_config('xa'); print(c.name, c.languages)"
```

```text
0.7.0
Atlantis ['eng']
```

Your version may be later. Codes beginning with `x`, such as Atlantis, are synthetic
legal systems invented for examples and tests.

### Start the database

Codify stores law in PostgreSQL 18 with pgvector (embeddings) and pg_textsearch (ranked
text search); the Compose file builds an image with both.

```bash
docker compose up -d --wait postgres
uv run codify migrate
```

```text
database is at 0029_official_sources
```

The service listens on `127.0.0.1:5432`; database, user and password are all `codify`.
The migrations record their state in `alembic_version_core`, so the schema can share a
database with another application. `docker compose stop postgres` keeps the data.

<details>
<summary>Which database a command uses, and another port</summary>

| `POSTGRES_URL` | `ENVIRONMENT` | Database used |
| --- | --- | --- |
| set | any | the URL you set |
| unset | unset, `localhost` or `development` | `postgresql://codify:codify@localhost:5432/codify` |
| unset | anything else | none: the command stops with `RuntimeError: POSTGRES_URL must be set when ENVIRONMENT is not localhost/development` |

The default applies only on a development machine, so a deployment cannot write to it
by accident. For another host port, set `CODIFY_PG_PORT` before `docker compose up` and
use the same port in `POSTGRES_URL`.

</details>

:::caution
The Compose service is a local development database with a known password. For a
database other machines reach, see [Database](../operate/database.md).
:::

### Set the environment

```bash
cp .env.example .env
```

It holds the model endpoint settings [First ingest](first-ingest.md) needs.
`codify` reads the nearest `.env` up from the working directory, and an exported variable
wins. The [environment reference](../reference/environment.md) lists every variable.

## Install as a package

```bash
pip install codify-core
```

With uv, `uv add codify-core`; name extras in brackets, as in
`pip install "codify-core[serve,mcp,migrations]"`.

PyPI can lag the `main` branch this guide describes, and an older release has fewer commands; check your version against the
[changelog](../../CHANGELOG.md). While the major version is 0 any minor release can
break things, so pin one minor range ([Versioning](../project/versioning.md)).

The package carries its migrations. With the `migrations` extra installed, build or
upgrade the schema of the database at `POSTGRES_URL` as in a checkout, without `uv run`:

```bash
codify migrate
```

```text
database is at 0029_official_sources
```

The revision it prints is the newest the installed version knows.

## Use your own jurisdiction data

Set `CODIFY_DATA_ROOT`, before Python starts, to an absolute directory holding
`jurisdictions/` to use your own configs in place of the bundled ones
([lookup order and errors](../guide/write-a-jurisdiction-config.md#choose-the-code-and-the-location)).

## Check your result

- `uv run codify --help` lists the subcommands.
- `load_config('xa')` prints `Atlantis ['eng']`.
- `uv run codify migrate`, run again, prints the same revision and changes nothing.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `No module named 'uvicorn'; install the serve extra: uv sync --extra serve` | `codify serve` without the `serve` extra. | Checkout: `uv sync` with every extra you use. Package: `pip install "codify-core[serve]"`. |
| `PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?` | Poppler is missing. | Install Poppler. |

## Status and limits

| Capability | Status |
| --- | --- |
| Install from a source checkout or PyPI | **Available** |
| Local PostgreSQL service (Compose) | **Available** |
| Migrations from a source checkout or a package install | **Available**: `codify migrate`, with the `migrations` extra |
| Containers for the HTTP server, MCP server and web app | **Not in the core**: run them as processes ([Deployment](../operate/deployment.md)) |
| A bundled model or model server | **Not in the core**: you supply the endpoint ([Models and costs](../operate/models-and-costs.md)) |

## Next

[First ingest](first-ingest.md): run a scanned act through the full pipeline with a model.
