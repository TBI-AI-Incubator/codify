---
title: Contributing
description: Set up a checkout, follow the rules for code and data, and know which files each change must also update.
sidebar:
  order: 1
---

Anyone can contribute; there is no contributor licence agreement. The
[code of conduct](../../CODE_OF_CONDUCT.md) governs participation.

## Before you start

| You need | For |
| --- | --- |
| Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) | everything |
| Git | everything |
| Poppler | commands that read PDFs |
| Docker with Compose | the database tests |
| Node.js (the major version in `.github/workflows/web.yml`) and pnpm | changes to the web app in `apps/web` |

## Set up a checkout

1. Clone the repository and enter it:

   ```bash
   git clone https://github.com/TBI-AI-Incubator/codify.git
   cd codify
   ```

2. Install the locked dependencies with every extra CI uses (a later `uv sync` with fewer
   extras removes them, and the server and MCP tests then skip):

   ```bash
   uv sync --frozen --group dev --extra migrations --extra serve --extra mcp
   ```

3. Check the command runs:

   ```bash
   uv run codify --help
   ```

   ```text
   usage: codify [-h]
                 {scan-corpus,ingest-one,segment,index-datadump,index-ee-archive,index-legislation-gov-uk,index-ocs,crawl-ocs,load,search,compare,serve,mcp}
                 ...

   Read what the pipeline did, one document at a time or across a whole corpus.
   ...
   ```

## Where things are

| Path | What it holds |
| --- | --- |
| `codify/` | the library, the `codify` command, the HTTP and MCP servers, and the migrations under `codify/migrations/` |
| `tests/` | the test suite; fixtures under `tests/fixtures/` |
| `data/jurisdictions/` | one directory per jurisdiction config, plus the registry |
| `data/fixtures/synthetic/` | invented documents for the synthetic jurisdictions, used in examples |
| `contract/openapi.json` | the HTTP API schema, generated from the server |
| `apps/web/` | the web app, typed from `contract/openapi.json` |
| `docs/` | this documentation; the site is built from `apps/docs/` |
| `scripts/` | build and maintenance scripts, importable by name in tests |

## Rules for code

- The core runs on its own. Never import from an application built on it, including the
  hosted Platform ([decision record 006](../decisions/006-repository-topology.md)).
- Extension points stay generic. Lenses plug in through the `codify.lenses` entry-point
  group; private detectors and catalogues stay in their own packages
  ([What is open](../open-core/what-is-open.md)).
- Every change in behaviour comes with a focused regression test that fails without it.
  Never make a failing test pass by skipping it.
- Prose in British English, identifiers in American English (`normalise` in a comment,
  `normalize` in a function name). Keep comments short and about why.
- The source is strictly typed. Run the type check from [Testing](testing.md) before you
  push.

## Rules for data

Tests and examples use invented law, so the repository stays free to redistribute and
holds no real people.

- Write new fixtures yourself, for a synthetic jurisdiction (such as `xa`). Do not commit
  real case files, scans you did not create, personal data, operational identifiers,
  credentials or private catalogue content.
- A document on a public government website is not thereby free to redistribute. Commit
  one only if its licence allows it.
- A jurisdiction config is packaged only when flagged `synthetic` or `public_reference`
  (`ships_in_open_wheel` in [`codify/open_wheel.py`](../../codify/open_wheel.py));
  [Write a jurisdiction config](../guide/write-a-jurisdiction-config.md) explains both.

## What else a change must update

CI catches most of these.

| If you change | Also do | Checked by |
| --- | --- | --- |
| anything on the public surface in [Versioning](versioning.md) | add an entry under `Unreleased` in [`CHANGELOG.md`](../../CHANGELOG.md), and say whether it is a break | review |
| an HTTP route or its models | run `uv run codify serve --openapi > contract/openapi.json`, then `pnpm generate:contract` in `apps/web` | `tests/test_serve.py`, the web workflow |
| a symbol listed in [`codify/contract/product_surface.txt`](../../codify/contract/product_surface.txt) | keep the old name working, or record the break and update the list | `tests/contract/test_product_surface.py`, the `contract` job |
| the MCP tool set | update `TOOL_NAMES` in `codify/mcp/server.py` and the test that lists them | `tests/test_mcp.py` |
| the anchor scanner | review and regenerate the [corpus baseline](../../docs/corpus-baselines/README.md) | `tests/quality/test_fixture_baseline.py` |
| the database schema | add a new migration under `codify/migrations/versions/`, and treat it as a public-surface change | the `integration` job; `lexicon-integration.yml` for migration changes |
| a jurisdiction config | run `uv run pytest tests/test_jurisdictions.py` | the `test` job |
| code a notebook runs | rerun the notebook as the [notebooks README](../../docs/notebooks/README.md) describes | review |
| a dependency | update `uv.lock` (or `apps/web/pnpm-lock.yaml`), and bring the tables in [`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md) back in line with the lockfiles | `--frozen` installs in CI; the notices by review |

## Open a pull request

1. Branch from `main`.
2. Commit with a sign-off line, which certifies the
   [Developer Certificate of Origin](https://developercertificate.org/). The project asks
   for it, though no CI check enforces it:

   ```bash
   git commit -s
   ```

3. Push and open the pull request. The template asks what changes and why, the issue it
   closes (`Closes #<number>`), and under **Assumptions** any decision made without a
   ticket.
4. The pull request must pass `lint`, `typecheck`, `test`, `build` and `contract`;
   `integration` also runs and should be green. `contract` runs the hosted Platform's
   contract test with a token forks lack; on a fork it reports that it did not run and
   passes.
5. GitHub requests a review from the code owner in `.github/CODEOWNERS`, and a Copilot
   review runs. No approval is required, but every review thread must be resolved before
   merge.

Auto-merge is off, and no workflow merges Dependabot updates.

## Licence

The project is under the [Apache License 2.0](../../LICENSE); unless you state otherwise,
your contribution is under the same licence (section 5). Third-party components keep their
own terms, listed in [`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md) with their
texts under `licenses/`.

## Status

| Item | Status |
| --- | --- |
| Lint, type, test, build and web checks on every pull request | **Available** |
| Sign-off check | **Not in the core**: no workflow checks it |
| A test that enforces the import boundary | **Not in the core**: it is a review rule |

## Next

[Testing](testing.md): run every check CI runs, locally.
