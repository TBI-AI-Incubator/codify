---
title: Deployment
description: Run Codify's database, HTTP server, MCP server and web app on a server, and know what each process keeps, trusts and loses on restart.
sidebar:
  order: 1
---

You end with Codify running as a service: the database, the HTTP server, the MCP server for agents and the web app, each a supervised process behind your own reverse proxy.

## Before you start

- A source checkout with the `serve` and `mcp` extras synced ([Install](../start/install.md)).
- A migrated PostgreSQL database ([Database](database.md)).
- A chat endpoint to ingest PDFs and an embeddings endpoint for search ([Models and costs](models-and-costs.md)).
- For the web app: Node.js 22 and Corepack, as the web CI uses. `corepack enable` supplies the pnpm version in `packageManager` in `apps/web/package.json`.
- A reverse proxy (nginx, Caddy, a cloud load balancer) with your organisation's sign-in in front of it.

## The idea in brief

The core is a library with thin servers over it: each HTTP route and MCP tool is one library call ([`codify/serve/app.py`](../../codify/serve/app.py), [`codify/mcp/server.py`](../../codify/mcp/server.py)). Only PostgreSQL holds durable state. Neither server has authentication, access control or rate limiting, so the proxy must supply them. [Serve and publish](../guide/serve-and-publish.md) shows what each process offers its users.

![A Codify deployment, top to bottom. A browser (people reading the law) and an AI agent (an MCP client) reach a reverse proxy with sign-in, which you provide and which is the only part that authenticates. The proxy forwards / to the web app's static files in apps/web/dist/, which keep nothing; /api/proxy/* to the HTTP server, codify serve on 127.0.0.1:8000, which keeps runs in memory and uploads in a temporary directory, both gone after a restart; and /mcp to the MCP server, codify mcp --http on 127.0.0.1:8001, whose sessions are in memory and gone after a restart. Both servers use PostgreSQL (POSTGRES_URL), which holds all stored law and is the only state that survives a restart. All of these sit on a private network; both servers bind 127.0.0.1 by default. Leaving the network: the chat model (LLM_BASE_URL) receives each PDF ingest's page images and text and each MCP comparison's provisions; the embeddings endpoint (EMBEDDING_BASE_URL) receives each search query and each MCP comparison; tracing (LANGFUSE_HOST), if set, receives each model call's model, token counts and cost and each ingest's source path, but no prompts or replies. From the command line, codify ingest-one also sends to the chat model and tracing, and, if AZURE_OPENAI_ENDPOINT is set, sends the whole PDF to Azure OCR for a layout pass; the servers never call Azure OCR.](../figures/v3-deployment.svg "Schematic of a deployment from the source's defaults: only the proxy authenticates, only PostgreSQL survives a restart, and each dashed arrow is something that leaves your network.")

The database is your own PostgreSQL server, or for development `docker compose up -d --wait postgres` on `127.0.0.1:5432`. An agent on the same machine can run the MCP server over standard input and output (`codify mcp`) instead of HTTP. `pnpm build` in `apps/web` writes the web app's static files, which your proxy serves.

## Steps

### Set the environment

Put the variables in each process's service manager environment, or in a `.env` file in its start directory. The `codify` command reads the nearest `.env` and never overrides a variable already set ([`codify/cli.py`](../../codify/cli.py)). `codify migrate` reads it too; Alembic run directly does not, so export `POSTGRES_URL` and `ENVIRONMENT` before you run it, or it migrates the local default database ([Database](database.md#point-codify-at-the-database)).

| Variable | Needed by | Purpose |
| --- | --- | --- |
| `POSTGRES_URL` | Both servers | The database |
| `ENVIRONMENT` | Both servers | `production` (anything but `localhost` or `development`) makes an unset `POSTGRES_URL` stop the process instead of using a local default |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | HTTP server, for PDF ingest; MCP server, for the compare tool | The chat model |
| `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL` | Both servers, for search; MCP server, for the compare tool | The embeddings model. URL and key fall back to the `LLM_*` values; the model defaults to `gemini-embedding-2` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Optional | Tracing and cost records ([Models and costs](models-and-costs.md#where-cost-is-recorded)) |

The full list is in the [environment reference](../reference/environment.md).

### Run the HTTP server

```bash
uv run codify serve --host 127.0.0.1 --port 8708
```

```text
INFO:     Started server process [43914]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8708 (Press CTRL+C to quit)
```

Run exactly one process: each keeps its own run table, so behind a load balancer a client polling `/runs/{id}` gets `no such run` from any process that did not start the run. Check it from the same machine:

```bash
curl -s localhost:8708/health
curl -s "localhost:8708/laws?jurisdiction=xa"
```

```text
{"status":"ok"}
{"items":[{"id":"21278932-9b16-4320-b50a-c832f3c61806","jurisdiction_code":"xa","title":"An Act to consolidate and reform the law relating to the making, publication and citation of the legislation of Atlantis; ...
```

The interactive API description is at `/docs` on the same port; the routes are in the [HTTP API reference](../reference/http-api.md).

### Run the MCP server

The MCP server gives AI agents read access to the same database over the Model Context Protocol: search, laws, versions, jurisdictions and a compare tool. A desktop agent on the same machine starts it over standard input and output:

```bash
uv run codify mcp
```

For agents elsewhere, serve it over streamable HTTP:

```bash
uv run codify mcp --http
```

```text
INFO:     Started server process [37741]
INFO:     Waiting for application startup.
StreamableHTTP session manager started
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:...
```

`--host` and `--port` change the address; keep it off the HTTP server's port. An MCP `initialize` request answers `200 OK` with an `mcp-session-id` header. Sessions live in process memory, so a restart ends them and a second process would not know them: run one, behind the same proxy and sign-in as the HTTP server. The tools are in the [MCP tools reference](../reference/mcp-tools.md).

Give agents the address your proxy forwards to this process, such as `https://your-host/mcp`. The web app's Connect an MCP client dialog always shows the local default, `http://127.0.0.1:8001/mcp`, which reaches the server only from its own machine.

### Build the web app

```bash
cd apps/web
pnpm install --frozen-lockfile
pnpm build
```

```text
...
✓ built in ...
```

`apps/web/dist/` now holds static files. The app calls the HTTP server under `/api/proxy` on its own origin ([`apps/web/src/lib/api.ts`](../../apps/web/src/lib/api.ts)); `pnpm dev` supplies that prefix in development. In production your proxy must:

| Rule | Why |
| --- | --- |
| Serve `dist/` for every path that is not a file, falling back to `index.html` | The app routes in the browser; reloading `/laws/...` must return the app |
| Forward `/api/proxy/*` to the HTTP server, prefix removed | `/api/proxy/laws` must reach `/laws` |
| Serve both from one origin | The HTTP server sends no CORS headers |
| Not buffer `/api/proxy/runs/*/stream` | Run progress is a Server-Sent Events stream; a buffering proxy delivers it only at the end |
| Cap request bodies at your largest gazette PDF | The upload route accepts any size and writes it to disk |

### Supervise the processes

Run each under your service manager (systemd, a container runtime, a platform's process model) with restart on failure. On `SIGTERM` the HTTP server cancels running ingests, waits for them, deletes its upload directory and closes its connections.

## Check your result

1. `GET /health` through the proxy returns `{"status":"ok"}`, and only after sign-in.
2. The web app's law list shows what you stored.
3. A PDF ingest reaches the model. Without a chat model the run fails at once (replace `<id>` with the `id` from the first response):

   ```bash
   curl -s -F file=@data/fixtures/synthetic/xa/legislation-act-1992.pdf -F jurisdiction=xa localhost:8708/runs/ingest
   curl -sN localhost:8708/runs/<id>/stream
   ```

   ```text
   {"id":"0a86e1cb-c2ff-475f-a7d6-42fe7c611f40","kind":"ingest","status":"queued",...,"lost_on_restart":true}
   event: failed
   data: {"kind": "failed", "stage": "dispatch", "error": "PDF ingestion requires an LLMClient"}

   event: end
   data: {}
   ```

Give the server an embeddings endpoint as well, and each document it stores is embedded in the same transaction and searchable at once; without one, the log shows `stored_unsearchable` and the document is found by its words only.

## What the HTTP server keeps, and loses

![A run in the HTTP server. An upload to POST /runs/ingest becomes a queued run; up to 100 can wait, and the next upload gets status 429. Two runs execute at once. A run finishes as succeeded, failed or cancelled, and the last 200 finished runs stay readable. Retry makes a new queued run with the same file. While it runs, a run writes its law to PostgreSQL, announced by the stored event. The queue, the running and finished runs and the uploaded files (a codify-uploads- directory) live only in the codify serve process and are lost on restart; PostgreSQL keeps the stored law across restarts.](../figures/v15-run-lifecycle.svg "Schematic of a run's life in codify serve, with limits read from the code: two run at once, a hundred wait, the last two hundred finished stay readable, and only PostgreSQL outlives a restart.")

The figure's three limits are constants in the code, not settings: `concurrency`, `max_queued` and `keep` on `RunTable` in [`codify/serve/runs.py`](../../codify/serve/runs.py). The refusal reads `100 runs already queued`; past 200 finished runs the oldest is forgotten.

| Behaviour | Value | Source |
| --- | --- | --- |
| A restart | Every run is marked `"lost_on_restart": true`. A successful run has already written its law. Start interrupted runs again | `Run.lost_on_restart` |
| Uploads | A `codify-uploads-` directory under the system temporary directory, each file deleted at shutdown or when its run is forgotten, so up to 200 finished runs' sources stay on disk | `create_app` in [`codify/serve/app.py`](../../codify/serve/app.py) |
| Upload size limit | None | `POST /runs/ingest` |

## Security

- **Sign-in and access control are yours.** Anyone who reaches the HTTP server can read every stored law and call `POST /runs/ingest`, which writes to your database and spends your model budget. Put an authenticating proxy in front of both servers and never expose their ports.
- **Publish read-only if that is enough.** Every route that changes anything is a `POST` under `/runs`, so a proxy that forwards only `GET` publishes the law without ingests. `GET /search` still sends each query to your embeddings endpoint.
- **Keep the servers private.** Both bind `127.0.0.1` by default; change `--host` only to a private interface the proxy uses.
- **Split database roles.** The servers need a role that reads and writes the Codify tables; run migrations as a separate role that may create extensions.
- **Know what leaves the machine.** The figure under [The idea in brief](#the-idea-in-brief) shows where your documents go. Two calls it leaves out: `POST /runs/ingest-url` makes the HTTP server fetch the act from EUR-Lex ([`codify/serve/app.py`](../../codify/serve/app.py)), and `codify load --embed` sends every provision to your embeddings endpoint ([`codify/cli_store.py`](../../codify/cli_store.py)). Langfuse's record of an ingest also carries its jurisdiction and resulting work URI, and its source path is the full path (for an upload, the temporary path) ([`codify/core/tracing.py`](../../codify/core/tracing.py), [`codify/pipeline/formats/pdf.py`](../../codify/pipeline/formats/pdf.py), [Models and costs](models-and-costs.md)).
- **Report vulnerabilities** as [Security](../project/security.md) describes.

## Keep everything in-country

Only the settings below send data off the machine. Point each at a service inside your network, or leave it unset ([environment reference](../reference/environment.md)).

| Setting | What it sends | When unset |
| --- | --- | --- |
| `LLM_BASE_URL` | Page images and text of each PDF ingest; provisions for `codify compare` and the MCP compare tool. `LLM_OCR_MODEL` and `LLM_CONTENT_FILTER_FALLBACK_MODEL` name other models on this endpoint | XML ingest, loading and reading work; PDF ingest and comparison stop |
| `EMBEDDING_BASE_URL` | Every provision on `load --embed` and server ingest; every search query | Falls back to `LLM_BASE_URL`; with neither, search stops and the server stores documents unsearchable |
| `AZURE_OPENAI_ENDPOINT` with `AZURE_OPENAI_API_KEY` | The whole PDF, from `codify ingest-one` | No second OCR engine; an `LLM_OCR_MODEL` that names it fails with `azure_foundry_ocr not configured` |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` | Each model call's model, tokens and cost; each ingest's source path, jurisdiction and work URI; no prompts or replies | No tracing. With keys set and no host, the Langfuse SDK sends to its cloud service: set `LANGFUSE_HOST` (or `LANGFUSE_BASE_URL`, which wins) to your own Langfuse |
| `ACQUISITION_USER_AGENT` | Requests to official publishers, from the acquisition commands and `POST /runs/ingest-url`; no stored law | Every such request stops before it is sent |

A model server of your own must support the routes in [Models and costs](models-and-costs.md#what-an-endpoint-must-support).

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `No module named 'uvicorn'; install the serve extra: uv sync --extra serve` | The `serve` extra is missing. | Sync it, with the other extras you use. |
| `GET /search` returns `503` with `set EMBEDDING_BASE_URL (or LLM_BASE_URL) to an embeddings endpoint` | No embeddings endpoint in the server's environment. | Set `EMBEDDING_BASE_URL` and restart. |
| An ingest fails at stage `dispatch` with `PDF ingestion requires an LLMClient` | No `LLM_BASE_URL` in the server's environment. | Set the chat variables and restart. |
| `POST /runs/ingest` returns `429` | 100 runs already queued. | Wait for runs to finish. |
| `GET /runs/{id}` returns `404 no such run` after a restart or behind several workers | Runs live in one process's memory. | Run one process; start lost runs again. |
| The web app loads but every list is empty or fails | The proxy does not forward `/api/proxy/*`, or sends it to another origin. | Apply the proxy rules under [Build the web app](#build-the-web-app). |

## Status and limits

| Capability | Status |
| --- | --- |
| HTTP server | **Available**: `codify serve` |
| MCP server over stdio or streamable HTTP | **Available**: `codify mcp`, `codify mcp --http` |
| Web app as static files | **Available**: `pnpm build` in `apps/web` |
| Database service | **Available**: Compose, for development |
| Container images for the servers and the web app | **Not in the core** |
| Authentication, access control, rate limiting | **Not in the core** |
| Runs that survive a restart, or several server processes | **Not in the core** |
| Durable storage for uploaded sources | **Not in the core** |

:::note[On the hosted Platform]
The hosted Platform provides authentication and role-based access control, and takes care of hosting, backup and upgrades.
:::

## Next

[Database](database.md): migrations, what the schema protects and the vector indexes.
