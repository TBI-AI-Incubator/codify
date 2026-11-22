---
title: Testing
description: Run the checks continuous integration runs, offline or against a disposable database, and know what a passing run proves.
sidebar:
  order: 2
---

Run every check a pull request must pass on your own machine. With no database and no
model, the suite skips what it cannot reach, says so, and passes.

## Before you start

| You need | For |
| --- | --- |
| A source checkout and [uv](https://docs.astral.sh/uv/) | every check |
| Docker with Compose | the database tests |
| Node.js and pnpm | the web app checks only |

Install the development dependencies with the extras continuous integration (CI) uses:

```bash
uv sync --frozen --group dev --extra migrations --extra serve --extra mcp
```

Without `serve` and `mcp`, the server tests skip at import (`pytest.importorskip` in
[`tests/test_serve.py`](../../tests/test_serve.py) and
[`tests/test_mcp.py`](../../tests/test_mcp.py)).

## How the suite is divided

Two pytest markers, declared in [`pyproject.toml`](../../pyproject.toml), set apart the
tests that need something outside the process.

| Marker | Needs | In CI |
| --- | --- | --- |
| `integration` | PostgreSQL with pgvector and pg_textsearch, migrated to head | the `integration` job |
| `live_llm` | a reachable chat or embeddings model | never |
| none | nothing | the `test` job |

Two more come from [`tests/conftest.py`](../../tests/conftest.py): `jurisdiction(code)`
skips a test when that jurisdiction config is not in the tree (reason `config not
shipped`), and `requires_repo(*paths)` skips one when the repository files it names are
absent.

## Run the offline suite

This is the `test` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml). It
points the chat and embeddings endpoints at a closed port, so a call that escapes the test
fakes fails instead of reaching a provider. The direct OCR route reads
`AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY`, which the command does not close, so
unset those two first.

```bash
LLM_BASE_URL=http://127.0.0.1:1 EMBEDDING_BASE_URL=http://127.0.0.1:1 uv run --frozen pytest tests -m "not integration and not live_llm" -q
```

The run takes several minutes and ends with counts of passed, skipped and deselected
tests. Some skips are expected; `-rs` lists each with its reason.

Before collecting tests, CI also checks that the tree resolves its own configs:

```bash
uv run --frozen python -c "from codify.jurisdictions import load_config; assert load_config('xa')"
```

## Run the database tests

These tests commit data, and some drop and recreate it. Point them only at a database you
can throw away.

1. Start the repository's PostgreSQL image, built from
   [`docker/postgres/Dockerfile`](../../docker/postgres/Dockerfile). It listens on
   `127.0.0.1:5432` with user, password and database all `codify`
   ([`docker-compose.yaml`](../../docker-compose.yaml)).

   ```bash
   docker compose up --build -d --wait postgres
   ```

   For another host port, set `CODIFY_PG_PORT` and a matching `POSTGRES_URL`. With
   `POSTGRES_URL` unset, the tests use `postgresql://codify:codify@localhost:5432/codify`.

2. Apply the core migrations. Alembic reads the address from `POSTGRES_URL` and records the
   revision in `alembic_version_core`
   ([`codify/migrations/env.py`](../../codify/migrations/env.py)).

   ```bash
   uv run --frozen alembic -c alembic.ini upgrade head
   ```

   Alembic logs one `Running upgrade` line per migration; at head, only its two start-up
   lines.

3. Run the integration tests as the `integration` job does:

   ```bash
   REQUIRE_DB=1 LLM_BASE_URL=http://127.0.0.1:1 EMBEDDING_BASE_URL=http://127.0.0.1:1 uv run --frozen pytest tests -m "integration and not live_llm" -q
   ```

4. Remove the database and its volume:

   ```bash
   docker compose down --volumes
   ```

A few integration tests skip depending on what the database holds, for example a
jurisdiction it lacks (`-rs` gives each reason). The two in
[`tests/akn/test_io_real_corpus.py`](../../tests/akn/test_io_real_corpus.py) skip unless you
set `CODIFY_FETCH_AKN_SAMPLES=1`, which downloads sample documents from a public publisher.

## Read a database skip correctly

When no database answers, a run that does not select by `integration` skips the database
tests and warns at the end ([`codify/testing.py`](../../codify/testing.py),
`apply_db_gate`):

```bash
POSTGRES_URL=postgresql://codify:codify@127.0.0.1:1/codify uv run --frozen pytest tests/storage/test_spans.py -q
```

```text
ssssssssssssss                                                           [100%]
OFFLINE: skipped 14 integration test(s) needing Postgres at 127.0.0.1:1. Set REQUIRE_DB=1 (with a database up) to run them.
14 skipped in 0.87s
```

With `REQUIRE_DB=1`, the same run stops before any test runs:

```text
ERROR: REQUIRE_DB is set but no Postgres is reachable at 127.0.0.1:1. Bring the database up or unset REQUIRE_DB; the integration tests will not be silently skipped while a database is demanded.
```

The gate applies only when `-m` does not contain `integration`
([`tests/conftest.py`](../../tests/conftest.py), `_gate_integration_db`). With step 3's
`-m "integration and not live_llm"`, each test fails on its connection, `REQUIRE_DB` or
not. A few modules read `REQUIRE_DB` themselves and skip when it is unset.

## Model tests

`live_llm` tests skip unless you select them with `-m live_llm` or set `RUN_LIVE_LLM` or
`GEMINI_API_KEY` ([`tests/conftest.py`](../../tests/conftest.py), `_gate_live_llm`), so a
bare `pytest` runs them if `GEMINI_API_KEY` is in your shell. They call the endpoint in
`LLM_BASE_URL`.

## Parallel runs

`pytest-xdist` is in the development group. Under `-n`, each worker clones the database
named in `POSTGRES_URL` (`CREATE DATABASE ... TEMPLATE`), points itself at the clone and
drops it at exit (`configure_worker_db` in [`codify/testing.py`](../../codify/testing.py)).
So:

- A parallel run needs a reachable database, even for the offline suite. Without
  `POSTGRES_URL` every worker stops with `RuntimeError: PYTEST_XDIST_WORKER is set but POSTGRES_URL is unset; xdist workers would share one database and collide. Set POSTGRES_URL.`
  With it set but no database answering, the run stops at conftest import with
  `ConftestImportFailure: OperationalError: connection failed`.
- The database user needs permission to create databases.
- `CODIFY_TEST_SHARE_DB=1` makes every worker use the base database and clone nothing, so
  an offline parallel run works with no database up.

A single-process run never clones, so two at once share one database.

## The other checks a pull request runs

| Check | Command | Source |
| --- | --- | --- |
| Lint | `uv run --frozen --with 'ruff==0.15.12' ruff check --config open-repo-ruff.toml .` | `ci.yml`, `lint` |
| Formatting | `uv run --frozen --with 'ruff==0.15.12' ruff format --check --config open-repo-ruff.toml .` | `ci.yml`, `lint` |
| Types | see below | `ci.yml`, `typecheck` |
| Package build | see below | `ci.yml`, `build` |
| Web app | see below | `web.yml` |
| Lexicon migration | `uv run --frozen pytest tests/test_core_migration_0019_lexicon.py -q`, with a migrated database | `lexicon-integration.yml`, on changes under `codify/migrations/` |

Pass the configuration files as shown: `ruff` or `mypy` with no flags reads other settings
and reports other results. The pinned versions are copied from `ci.yml`; if they differ,
`ci.yml` wins.

The type check, strict over the `codify` package, prints `Success: no issues found` and
the number of files checked when clean:

```bash
uv run --frozen --with 'mypy==2.1.0' --with 'types-PyYAML==6.0.12.20260408' --with 'types-regex==2026.5.9.20260518' --with 'types-cachetools==7.0.0.20260518' mypy --config-file open-repo-mypy.ini codify
```

The build check builds the wheel and source distribution into `dist/` (which git ignores),
refuses either if it holds generated files, environment files or private module paths
([`scripts/check_distribution.py`](../../scripts/check_distribution.py)), then imports the
wheel in isolation and loads a synthetic config and the framework data from it. Copy that
import step from the last `run` block of the `build` job in `ci.yml`.

```bash
uv build
uv run python scripts/check_distribution.py dist
```

```text
Building source distribution...
Building wheel from source distribution...
...
Checked 2 distributions
```

The web app's checks run from `apps/web` when it or the HTTP contract changes, on
Node.js 22 as in CI and the pnpm version pinned in `apps/web/package.json`
(`corepack enable` selects it). The second command regenerates the TypeScript types from
the committed OpenAPI schema and fails on any difference.

```bash
pnpm install --frozen-lockfile
pnpm generate:contract && git diff --exit-code src/lib/contract.d.ts
pnpm typecheck
pnpm test
pnpm build
```

## Contract and baseline tests

Three offline tests guard what others depend on.

| Test | What it holds | When it fails |
| --- | --- | --- |
| `tests/test_serve.py::test_the_committed_openapi_schema_matches_the_server` | [`contract/openapi.json`](../../contract/openapi.json) equals the schema the server produces | Regenerate with `uv run codify serve --openapi > contract/openapi.json`, then rerun the web contract step. |
| `tests/contract/test_product_surface.py` | every `module:symbol` in [`codify/contract/product_surface.txt`](../../codify/contract/product_surface.txt) still imports | You renamed or removed a symbol that a downstream application imports. See [Versioning](versioning.md). |
| `tests/quality/test_fixture_baseline.py` | the anchor scan over `tests/fixtures/corpus` matches [`docs/corpus-baselines/xz-fixture-scan.json`](../../docs/corpus-baselines/xz-fixture-scan.json) | Review the scanner change, then regenerate the baseline as its [README](../../docs/corpus-baselines/README.md) describes. |

Check the baseline without pytest, a model or a database:

```bash
uv run --frozen codify scan-corpus tests/fixtures/corpus --jurisdiction xz --check docs/corpus-baselines/xz-fixture-scan.json
```

```text
...
docs/corpus-baselines/xz-fixture-scan.json matches (5 documents).
```

On a difference it prints `DRIFT in` the baseline file, each changed key with expected and
measured values, and exits 1.

## What a passing run proves

A green suite shows the code behaves as its tests describe on synthetic and fixture
documents. It does not show that a config reads your gazette correctly, that a model
transcribes your scans faithfully, or that the Akoma Ntoso for a real statute book is
accurate. Measure those on your own documents
([Validate and repair](../guide/validate-and-repair.md)).

## Status

| Capability | Status |
| --- | --- |
| Offline suite, database suite, lint, types, build and web checks | **Available**: the commands above |
| Parallel runs with per-worker databases | **Available**: `pytest -n`, with a reachable database |
| Model tests | **Available**: opt-in, never in CI |
| Notebook execution | **Not in the core**: the test suite does not run the notebooks; see the [notebooks README](../../docs/notebooks/README.md) |

## Next

[Versioning](versioning.md): what counts as a breaking change, and how a release is cut.
