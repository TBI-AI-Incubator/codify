---
title: Database
description: Prepare PostgreSQL for Codify, run the migrations, understand what the schema protects, and build the vector indexes.
sidebar:
  order: 2
---

You end with a PostgreSQL database at the latest Codify schema, with the vector indexes that make search fast, and you know which rows it refuses to change.

## Before you start

- A source checkout of Codify with the environment synced. See [Install](../start/install.md).
- A PostgreSQL 18 server you control, or Docker to run the one in the repository.
- `psql`, for the vector index steps.

## The idea in brief

All stored law lives in one PostgreSQL database. The XML of each expression is the record; the section, provision and vector rows beside it are derived from it for reading and search ([Architecture](../concepts/architecture.md#storage)).

A chain of Alembic migrations, numbered scripts that each move the schema one step forward, builds the database. Codify records its step in its own table, `alembic_version_core`, not Alembic's default `alembic_version`, so it can share a database with an application that keeps its own chain ([`codify/migrations/env.py`](../../codify/migrations/env.py)).

## What the server needs

| Requirement | Why | Where it is set |
| --- | --- | --- |
| PostgreSQL 18 | CI builds and tests the schema on the image below | [`docker/postgres/Dockerfile`](../../docker/postgres/Dockerfile), `.github/workflows/ci.yml` |
| `vector` (pgvector) | Provision embeddings are `halfvec(768)` columns with HNSW indexes | First migration |
| `pg_textsearch`, loaded at start-up | The BM25 index that ranks the lexical half of search | First migration; `shared_preload_libraries=pg_textsearch` in [`docker-compose.yaml`](../../docker-compose.yaml) |
| `pgcrypto` and `pg_trgm` | The first migration creates `pgcrypto`; `pg_trgm` gives fuzzy matching in the search-term lexicon | First migration; migration `0019` |

The migrations create all four with `CREATE EXTENSION IF NOT EXISTS` (`0001_core_baseline.sql`, `0010`, `0019`), so the role that runs them must be allowed to. A managed PostgreSQL service fits only if it offers all four and lets you preload `pg_textsearch`.

### The image in the repository

`docker compose up -d --wait postgres` builds [`docker/postgres/Dockerfile`](../../docker/postgres/Dockerfile): the pgvector image for PostgreSQL 18, pinned by digest, with `pg_textsearch` compiled from a source archive pinned by SHA-256. The service binds `127.0.0.1` only, keeps its data in a named volume, and uses `codify` as user, password and database name unless you set `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB`. It is a development database. For a server other machines reach, build the same image or install the same extensions, set your own credentials and keep it on a private network.

## Steps

### Point Codify at the database

Every command that touches the database reads `POSTGRES_URL`. Give a plain `postgresql://` URL; Codify picks the driver: `asyncpg` for the commands and servers ([`codify/settings.py`](../../codify/settings.py)), `psycopg` for the migrations ([`codify/migrations/env.py`](../../codify/migrations/env.py)).

```bash
export POSTGRES_URL=postgresql://codify:codify@localhost:5432/codify
```

Use your own user, password, host, port and database name. The `psql` commands on this page take the same URL.

Export it in the shell or the service manager. The `codify` command, `codify migrate` included, also reads `.env`; Alembic run directly reads the process environment only.

With `POSTGRES_URL` unset, Codify uses `postgresql://codify:codify@localhost:5432/codify`, but only while `ENVIRONMENT` is unset, `localhost` or `development`. On a server, set `ENVIRONMENT` to anything else, for example `production`, and a missing `POSTGRES_URL` stops the command:

```bash
env -u POSTGRES_URL ENVIRONMENT=production uv run alembic -c alembic.ini current
```

```text
...
RuntimeError: POSTGRES_URL must be set when ENVIRONMENT is not localhost/development
```

### Run the migrations

With the `migrations` extra installed, from a source checkout or a package install:

```bash
uv run codify migrate
```

```text
database is at 0029_official_sources
```

From a package install, drop `uv run`. In a checkout, Alembic does the same and lists every step on an empty database:

```bash
uv run alembic -c alembic.ini upgrade head
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_core_baseline, Core corpus schema baseline.
INFO  [alembic.runtime.migration] Running upgrade 0001_core_baseline -> 0002_drop_escalation_rung, Drop page_read_disputes.escalation_rung.
...
INFO  [alembic.runtime.migration] Running upgrade 0014_effect_publisher_id -> 0018_annex_admission, Admit annex runs/artifacts on the canonical chain without deleting evidence.
...
INFO  [alembic.runtime.migration] Running upgrade 0028_goods_code_scope -> 0029_official_sources, Add official_sources, official_anchors and official_ids.
```

Run it again after every upgrade of Codify; it applies only the missing steps. The `alembic` commands below need a source checkout, because `alembic.ini` sits at the repository root.

### Check where the database stands

```bash
uv run alembic -c alembic.ini current
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0029_official_sources (head)
```

`(head)` means nothing is waiting. Revision names change between releases; `uv run alembic -c alembic.ini heads` prints the latest your checkout knows:

```text
0029_official_sources (head)
```

## How the migrations behave

**They need a live connection.** The first migration decides at run time whether to build the schema or adopt an existing one, so Alembic's offline mode (SQL written to a file) is refused:

```text
RuntimeError: The core migration chain needs a live database connection: the baseline builds or adopts based on the existing schema, so --sql offline mode is not supported.
```

**They give up on a lock after 30 seconds.** Each migration runs with `lock_timeout = '30s'`, so it stops rather than queue behind a long query or open transaction and block every reader after it. The upgrade is one transaction: a failure rolls back every step of the command, except steps run outside the transaction (some index builds). Find the holder in `pg_stat_activity`, let it finish or stop it, then run the command again:

```text
psycopg.errors.LockNotAvailable: canceling statement due to lock timeout
...
sqlalchemy.exc.OperationalError: (psycopg.errors.LockNotAvailable) canceling statement due to lock timeout
```

**They can adopt tables another application built.** This matters only on a database shared with an application whose own chain built the corpus tables. If those tables exist, the first migration checks their shape (it looks for the BM25 index on `provisions`) and only records its version. If they are older, it stops with `Corpus tables exist but this baseline's head schema is not present ...`; bring the other chain to its head first. The tables the chain owns are `CORE_TABLES` in [`codify/migrations/env.py`](../../codify/migrations/env.py).

**The numbering has a gap.** The chain goes from `0014_effect_publisher_id` to `0018_annex_admission`; there is no 0015 to 0017. A database stamped with one of those came from a different history, and Alembic will not traverse it. Do not stamp or downgrade it into place; [`codify/migrations/recovery/annex_0017/README.md`](../../codify/migrations/recovery/annex_0017/README.md) says what to preserve first.

:::caution[Downgrades]
`alembic downgrade` works step by step, but two steps destroy work. Downgrading the first migration on a database Codify built drops every Codify table. Downgrading `0020_embeddings_by_jurisdiction` flattens the embeddings table and leaves it with no vector index, so search falls back to an exact scan until you build one by hand. Take a backup first.
:::

## What the schema protects

Citations, amendments and judgments point at an expression by its URI. If the text behind a URI could change silently, every reference into it would be in doubt, so the database enforces the rules with triggers that application code cannot bypass. They fire only on statements issued directly, not from inside another trigger (`WHEN (pg_trigger_depth() = 0)`), and a table owner can disable them.

On a `versions` row (one expression of a law), the trigger refuses a direct delete and any change to the identifying columns: id, law, expression URI, language, expression date, source hash, parent version and the two timestamps:

```bash
psql "$POSTGRES_URL" -c "update versions set expression_uri = expression_uri || 'x'"
psql "$POSTGRES_URL" -c "delete from versions"
```

```text
ERROR:  versions row is immutable (op=UPDATE, id=d52c833c-57a0-45fe-a73d-cc733f72d59e)
CONTEXT:  PL/pgSQL function enforce_versions_immutable() line 43 at RAISE
ERROR:  versions row is immutable (op=DELETE, id=d52c833c-57a0-45fe-a73d-cc733f72d59e)
CONTEXT:  PL/pgSQL function enforce_versions_immutable() line 43 at RAISE
```

A change to the XML needs a repair stamp:

```bash
psql "$POSTGRES_URL" -c "update versions set akn_xml = akn_xml || ' '"
```

```text
ERROR:  versions.akn_xml change requires repaired_at + repair_attribution to be stamped together (id=d52c833c-57a0-45fe-a73d-cc733f72d59e)
CONTEXT:  PL/pgSQL function enforce_versions_immutable() line 21 at RAISE
```

The trigger checks only that `repaired_at` and `repair_attribution` are set on the new row, so a later change passes under the old stamp: the stamp shows the text changed at least once.

`reviewed_at` may only move forward. Deleting a whole law is allowed and cascades to its versions, sections, provisions and embeddings; `codify.storage.supersede_document` replaces a law that way ([Architecture](../concepts/architecture.md#storage)). The `events` table is append-only, and examinations and page-read disputes cannot be updated once written ([`0001_core_baseline.sql`](../../codify/migrations/versions/0001_core_baseline.sql)).

## Vector indexes

Search compares a query's embedding with every provision's. An HNSW index, a graph that finds near neighbours without reading every vector, makes that fast. `provision_embeddings` is partitioned by `jurisdiction_id`, each partition has its own index, and a jurisdiction with its own partition is searched in that partition alone ([`0020_embeddings_by_jurisdiction.py`](../../codify/migrations/versions/0020_embeddings_by_jurisdiction.py), [`codify/storage/partitions.py`](../../codify/storage/partitions.py)).

![The embeddings table provision_embeddings is partitioned by jurisdiction_id and carries the parent index provision_embeddings_hnsw_idx. Below it are three kinds of partition. Jurisdictions that existed when migration 0020 ran each get a partition named provision_embeddings_p_ followed by the jurisdiction id without hyphens; its HNSW index is built by hand and then attached, and until it is attached searches on it use an exact scan. Jurisdictions added after 0020 share the DEFAULT partition, provision_embeddings_p_default, which has its HNSW index from creation; this is the partition that matters on a new database. An arrow labelled promote, with writers stopped, using promote_sql, moves one jurisdiction from DEFAULT to a partition of its own, named provision_embeddings_p_ followed by its id, whose HNSW index is copied from the parent and which is searched on its own.](../figures/v16-embedding-partitions.svg "Schematic of where each jurisdiction's vectors live after migration 0020 and how each kind of partition gets its HNSW index; names are those the migration and promote_sql use.")

### On a new database you have nothing to do

Migration 0020 creates the `DEFAULT` partition with its HNSW index. After a fresh `upgrade head`:

```bash
psql "$POSTGRES_URL" -c "SELECT indexname FROM pg_indexes WHERE tablename = 'provision_embeddings_p_default' ORDER BY 1"
```

```text
                            indexname
-----------------------------------------------------------------
 provision_embeddings_p_defaul_provision_id_model_id_jurisdi_key
 provision_embeddings_p_default_embedding_idx
 provision_embeddings_p_default_model_id_idx
 provision_embeddings_p_default_pkey
 provision_embeddings_p_default_version_id_idx
(5 rows)
```

`provision_embeddings_p_default_embedding_idx` is the HNSW index.

### A database that held embeddings before migration 0020

On a database that already has jurisdictions, 0020 creates a partition for each and copies their vectors in, but does not build their HNSW indexes: on a large corpus that takes hours, too long for a migration to hold the table. Until you build them, those partitions use an exact scan, correct and slower. Build each with `psql`, one partition at a time.

1. List the partitions and whether each has its index attached:

   ```sql
   SELECT j.code, c.relname AS partition, pg_size_pretty(pg_relation_size(c.oid)) AS size,
          c.reltuples::bigint AS rows,
          EXISTS (SELECT 1 FROM pg_index i JOIN pg_inherits h ON h.inhrelid = i.indexrelid
                  WHERE i.indrelid = c.oid
                    AND h.inhparent = 'provision_embeddings_hnsw_idx'::regclass) AS attached
   FROM pg_inherits p JOIN pg_class c ON c.oid = p.inhrelid
   JOIN jurisdictions j ON c.relname = 'provision_embeddings_p_' || replace(j.id::text, '-', '')
   WHERE p.inhparent = 'provision_embeddings'::regclass
   ORDER BY c.reltuples DESC;
   ```

   ```text
    code |                        partition                        |  size  | rows | attached
   ------+---------------------------------------------------------+--------+------+----------
    xa   | provision_embeddings_p_855f050ff8a24566b57e20cb97adf8f2 | 128 kB |   62 | f
   (1 row)
   ```

   A partition is named `provision_embeddings_p_` plus the jurisdiction's id without hyphens. On a new database the query returns no rows.

2. For each partition with `attached` false, build the index without blocking writes, attach it to the parent index and refresh the planner's statistics. Use one `psql` session, so the `SET` lines hold, and no transaction (no `BEGIN`, no `psql -1`), which `CREATE INDEX CONCURRENTLY` refuses:

   ```sql
   SET maintenance_work_mem = '2GB';
   SET max_parallel_maintenance_workers = 4;
   CREATE INDEX CONCURRENTLY provision_embeddings_p_<id>_hnsw
       ON provision_embeddings_p_<id>
       USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);
   ALTER INDEX provision_embeddings_hnsw_idx ATTACH PARTITION provision_embeddings_p_<id>_hnsw;
   ANALYZE provision_embeddings_p_<id>;
   ```

   ```text
   SET
   SET
   CREATE INDEX
   ALTER INDEX
   ANALYZE
   ```

   Keep the migration's `m` and `ef_construction` so every partition's index behaves alike. Set `maintenance_work_mem` to what your server can spare.

3. Run the step 1 query again; the partition shows `attached` true. Once all are attached, the parent index becomes valid by itself.

Start with the smallest partitions: each attach serves that jurisdiction's searches at once. `pg_stat_progress_create_index` shows a build's progress. A build interrupted by a restart leaves an `INVALID` index; drop it and build again. The same procedure is kept as an operator note in [`docs/runbooks/vector-index-build.md`](../runbooks/vector-index-build.md).

### Giving a large jurisdiction its own partition

A jurisdiction in `DEFAULT` can be moved to its own partition. Codify never does this at run time: creating or attaching a partition waits on every open writer of `provisions` and `jurisdictions`, stopping ingest and search meanwhile ([`codify/storage/partitions.py`](../../codify/storage/partitions.py)). `codify.storage.partitions.promote_sql` returns the four statements; run them in one transaction with writers stopped. Ids are random per database, so read yours with `select id from jurisdictions where code = 'xa'`. For `xa` in one example database:

```sql
BEGIN;
CREATE TABLE "provision_embeddings_p_855f050ff8a24566b57e20cb97adf8f2" (LIKE provision_embeddings INCLUDING ALL);
INSERT INTO "provision_embeddings_p_855f050ff8a24566b57e20cb97adf8f2" SELECT * FROM provision_embeddings_p_default WHERE jurisdiction_id = '855f050f-f8a2-4566-b57e-20cb97adf8f2';
DELETE FROM provision_embeddings_p_default WHERE jurisdiction_id = '855f050f-f8a2-4566-b57e-20cb97adf8f2';
ALTER TABLE provision_embeddings ATTACH PARTITION "provision_embeddings_p_855f050ff8a24566b57e20cb97adf8f2" FOR VALUES IN ('855f050f-f8a2-4566-b57e-20cb97adf8f2');
COMMIT;
```

```text
BEGIN
CREATE TABLE
INSERT 0 0
DELETE 0
ALTER TABLE
COMMIT
```

The new table copies the parent's indexes, HNSW included, so the step 1 query shows it attached at once. To print the statements for your jurisdiction:

```python
from uuid import UUID

from codify.storage.partitions import promote_sql

for statement in promote_sql(UUID("855f050f-f8a2-4566-b57e-20cb97adf8f2")):
    print(statement + ";")
```

## Backups

Codify has no backup command. Use PostgreSQL's tools (`pg_dump`, or your platform's snapshots), restore onto a server with the same four extensions and `pg_textsearch` preloaded, and back up before every upgrade, since migrations change the schema in place.

## Check your result

- `uv run alembic -c alembic.ini current` prints the head revision followed by `(head)`.
- `psql "$POSTGRES_URL" -c "select extname from pg_extension"` lists `vector`, `pgcrypto`, `pg_textsearch` and `pg_trgm`.
- `uv run codify load <bundle>` prints a `version_id`. [Quickstart](../start/quickstart.md) has a keyless example.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| From a `codify` command: `ConnectionRefusedError: [Errno 61] Connect call failed (...)` (host, port and error number vary). From the migrations: `connection to server at "...", port ... failed: could not receive data from server: Connection refused` | Nothing listens at `POSTGRES_URL`, or it is unset and nothing listens on `localhost:5432`. For a direct `alembic` run, `POSTGRES_URL` may be set only in `.env`, which Alembic does not read. | Start the server, or correct and export `POSTGRES_URL`. |
| From a `codify` command: `asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "codify"`. From the migrations: `FATAL:  password authentication failed for user "codify"` | Wrong credentials in `POSTGRES_URL`. | Correct the URL. |
| Search is slow on one jurisdiction | Its partition has no HNSW index attached. | Follow the vector index steps above. |

The `ENVIRONMENT` error is under [Point Codify at the database](#point-codify-at-the-database); lock timeout, offline mode and adoption errors are under [How the migrations behave](#how-the-migrations-behave).

## Status and limits

| Capability | Status |
| --- | --- |
| PostgreSQL image with the required extensions | **Available**: `docker/postgres/Dockerfile`, `docker compose up -d --wait postgres` |
| Migrations from a source checkout or a package install | **Available**: `codify migrate`, with the `migrations` extra |
| Checking, listing and downgrading revisions | **Available** from a source checkout: `alembic -c alembic.ini` |
| Immutable expressions and append-only events | **Available**: enforced by database triggers |
| Vector index on a new database | **Available**: the migration indexes the shared `DEFAULT` partition |
| Building indexes for partitions created by migration 0020 | **Not in the core** as a command: run the SQL procedure above with `psql` |
| Moving a jurisdiction out of `DEFAULT` | **Library only**: `codify.storage.partitions.promote_sql` returns the statements; you run them |
| Backup, restore and point-in-time recovery | **Not in the core**: use PostgreSQL's tools |
| Offline (SQL-file) migrations | **Not in the core** |

## Next

[Models and costs](models-and-costs.md): connect the chat and embeddings models.
