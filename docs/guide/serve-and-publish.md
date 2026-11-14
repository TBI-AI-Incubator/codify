---
title: Serve and publish
description: Serve stored law over HTTP, in the web app and to AI agents through MCP, and decide what is safe to expose beyond your own machine.
sidebar:
  order: 10
---

You end with the worked example readable over a REST API, in the web reader and through an MCP server, and a list of what is safe to expose beyond your machine.

## Before you start

- The worked example stored in a database built with `codify migrate` ([Database](../operate/database.md#run-the-migrations)), loaded as in [Search](search.md#store-and-embed-the-act) or [First ingest](../start/first-ingest.md), with `POSTGRES_URL` pointing at it.
- A source checkout with the `serve` and `mcp` extras ([Install](../start/install.md)).
- For the web app: Node.js 22 and pnpm (`corepack enable` supplies the version pinned in `apps/web/package.json`).
- `curl` and `jq`.

## The idea in brief

Three thin library layers publish one store, each built for one trusted machine ([Expose it beyond your machine](#expose-it-beyond-your-machine)): the HTTP server for your applications and the web app, the web app for people reading and searching the law, and the MCP server for AI agents (Model Context Protocol). In development the web app runs with `pnpm dev` in `apps/web`, and an agent on the same machine starts `codify mcp` itself. Exposed beyond your machine, they sit behind a proxy:

![A Codify deployment, top to bottom. A browser (people reading the law) and an AI agent (an MCP client) reach a reverse proxy with sign-in, which you provide and which is the only part that authenticates. The proxy forwards / to the web app's static files in apps/web/dist/, which keep nothing; /api/proxy/* to the HTTP server, codify serve on 127.0.0.1:8000, which keeps runs in memory and uploads in a temporary directory, both gone after a restart; and /mcp to the MCP server, codify mcp --http on 127.0.0.1:8001, whose sessions are in memory and gone after a restart. Both servers use PostgreSQL (POSTGRES_URL), which holds all stored law and is the only state that survives a restart. All of these sit on a private network; both servers bind 127.0.0.1 by default. Leaving the network: the chat model (LLM_BASE_URL) receives each PDF ingest's page images and text and each MCP comparison's provisions; the embeddings endpoint (EMBEDDING_BASE_URL) receives each search query and each MCP comparison; tracing (LANGFUSE_HOST), if set, receives each model call's model, token counts and cost and each ingest's source path, but no prompts or replies. From the command line, codify ingest-one also sends to the chat model and tracing, and, if AZURE_OPENAI_ENDPOINT is set, sends the whole PDF to Azure OCR for a layout pass; the servers never call Azure OCR.](../figures/v3-deployment.svg "Schematic of a deployment from the source's defaults: only the proxy authenticates, only PostgreSQL survives a restart, and each dashed arrow is something that leaves your network.")

Cite by [FRBR](../concepts/frbr.md) URIs (work `/akn/xa/act/1992/7`, expression `/akn/xa/act/1992/7/eng@1992-05-02`); database ids are local to one installation.

## Steps

### Start the HTTP server

It listens on `127.0.0.1:8000` (`--host`, `--port`), with interactive API documentation at `/docs`.

```bash
uv run codify serve
```

In a second terminal:

```bash
curl localhost:8000/health
```

```json
{"status":"ok"}
```

### Read the stored act

List one jurisdiction's laws; `doctype`, `year`, `q` (title), `limit` and `offset` filter and page.

```bash
curl "localhost:8000/laws?jurisdiction=xa"
```

```json
{"items":[{"id":"21278932-9b16-4320-b50a-c832f3c61806","jurisdiction_code":"xa","title":"An Act to consolidate and reform the law relating to ... and for connected and incidental purposes.","short_title":"Legislation Act, 1992","doctype":"act","status":"enacted","year":1992,"number":"7","frbr_work_uri":"/akn/xa/act/1992/7","latest_expression_date":"1992-05-02",...}],"limit":50,"offset":0}
```

Read one law by its `id` to list its stored versions, without XML:

```bash
curl localhost:8000/laws/21278932-9b16-4320-b50a-c832f3c61806
```

```json
{
  "id": "21278932-9b16-4320-b50a-c832f3c61806",
  "jurisdiction": "xa",
  "short_title": "Legislation Act, 1992",
  "work_uri": "/akn/xa/act/1992/7",
  "versions": [
    {
      "id": "20cb9d70-142f-47fc-993b-98d884a79ad8",
      "expression_uri": "/akn/xa/act/1992/7/eng@1992-05-02",
      "language": "eng",
      "expression_date": "1992-05-02",
      "structural_quality_grade": null,
      ...
    }
  ],
  ...
}
```

Your ids differ. `structural_quality_grade` is empty because `load` does not store the bundle's grade.

`GET /versions/{id}` returns [Akoma Ntoso](../concepts/akoma-ntoso.md) as `akn_xml`; saved, it matches `bundle/final.akn.xml` bar the newline `jq` adds:

```bash
curl -s localhost:8000/versions/20cb9d70-142f-47fc-993b-98d884a79ad8 | jq -r .akn_xml > act.akn.xml
```

`GET /versions/{id}/document` returns the reader's tree; the [HTTP API reference](../reference/http-api.md) lists every route and field.

### Serve another system

Other systems call the same routes through your authenticating proxy, which forwards only the routes you publish ([Deployment](../operate/deployment.md#security)). Bind the server to the private interface the proxy uses:

```bash
uv run codify serve --host 10.0.0.5 --port 8000
```

Find a law by title with `q`, or search its provisions. Each search match carries `work_uri`, `eid`, `version_id` and `law_id`, enough to [cite the provision](../concepts/frbr.md#cite-a-provision) and fetch its version:

```bash
curl "localhost:8000/laws?jurisdiction=xa&q=legislation"
curl "localhost:8000/search?q=right%20of%20access&jurisdiction=xa&k=5"
```

`/search` needs an embeddings endpoint and answers `503` without one. Generate the caller's client from `contract/openapi.json` ([Keep the contract](#keep-the-contract)).

### Ingest a document through the server

`POST /runs/ingest` takes a multipart form (file, jurisdiction, optional title) and answers `202` at once with a background run.

```bash
curl -F file=@bundle/final.akn.xml -F jurisdiction=xa localhost:8000/runs/ingest
```

```json
{"id":"d5a0f090-4d80-4c91-97cd-05157af4d590","kind":"ingest","status":"queued","created_at":"2026-11-14T01:00:03.665890Z","started_at":null,"completed_at":null,"result":null,"error":null,"retry_of":null,"events":0,"lost_on_restart":true}
```

Stream its events: past ones replay, new ones follow, and it ends with `event: end`:

```bash
curl -N localhost:8000/runs/d5a0f090-4d80-4c91-97cd-05157af4d590/stream
```

```text
event: parsed
data: {"kind": "parsed", "akn_xml_len": 32254}

event: validation_issued
data: {"kind": "validation_issued", "issue": {"check": "missing_container_title", "severity": "warning", "eid": "part_II", "container": "part", "message": "part II has no descriptive heading; the reader shows a bare marker"}}

event: stored
data: {"kind": "stored", "version_id": "20cb9d70-142f-47fc-993b-98d884a79ad8", "law_id": "21278932-9b16-4320-b50a-c832f3c61806"}

event: complete
data: {"kind": "complete", "work_uri": "/akn/xa/act/1992/7", "expression_uri": "/akn/xa/act/1992/7/eng@1992-05-02"}

event: end
data: {}
```

`stored` names the version `load` already wrote. With an embeddings endpoint set (`EMBEDDING_BASE_URL`, or `LLM_BASE_URL`), the server embeds each document in the same transaction that stores it, so [search](search.md) finds it at once; if the embedding call fails, the run fails at stage `persist` and stores nothing. With no endpoint, the run still succeeds and the server logs `stored_unsearchable` with the variable to set.

![A run in the HTTP server. An upload to POST /runs/ingest becomes a queued run; up to 100 can wait, and the next upload gets status 429. Two runs execute at once. A run finishes as succeeded, failed or cancelled, and the last 200 finished runs stay readable. Retry makes a new queued run with the same file. While it runs, a run writes its law to PostgreSQL, announced by the stored event. The queue, the running and finished runs and the uploaded files (a codify-uploads- directory) live only in the codify serve process and are lost on restart; PostgreSQL keeps the stored law across restarts.](../figures/v15-run-lifecycle.svg "Schematic of a run's life in codify serve, with limits read from the code: everything in the dashed box is lost on restart, and only PostgreSQL keeps what a run stored.")

<details>
<summary>Run status, retry, cancel, and which uploads need a model</summary>

`GET /runs/{id}` returns the `status` and, on success, a `result` with the URIs and ids. `POST /runs/{id}/retry` starts a new run with the same file once a run has finished; `POST /runs/{id}/cancel` stops a queued or running one.

The file's suffix chooses the lane ([`codify/pipeline/formats/__init__.py`](../../codify/pipeline/formats/__init__.py)):

| Upload | Lane | Needs a model |
| --- | --- | --- |
| `.xml`, `.akn`, `.fmx` | Parsed and normalised as XML | No |
| `.html`, `.htm` | Parsed without a model if in the layout one acquisition adapter writes; any HTML under `eu` takes the EU lane; other HTML takes the PDF lane | Only for other HTML |
| Anything else, including `.pdf`, `.txt` and `.md` | Read as a PDF: OCR, structuring, validation. A `.txt` or `.md` file is not a PDF, so its run fails at stage `extract`; ingest text with `ingest-one` | Yes: `LLM_BASE_URL` and the other chat settings, read once when the server's first run starts |

</details>

<details>
<summary>Ingest by URL</summary>

`POST /runs/ingest-url` takes a JSON body `{"url", "jurisdiction", "title"}` and accepts only an address on `eur-lex.europa.eu` or `publications.europa.eu`, under the jurisdiction `eu`, whose config ships. The server fetches the act itself with the User-Agent in `ACQUISITION_USER_AGENT`, then converts it on the EU lane with no model:

```bash
curl -s -H 'content-type: application/json' -d '{"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32016R0679", "jurisdiction": "eu"}' "$CODIFY/runs/ingest-url" | jq '{id, status}'
```

```text
{
  "id": "133a334b-c4f7-4665-b28a-3d71f9fcc41a",
  "status": "queued"
}
```

Any other host answers `422` with `only an EU publications URL (eur-lex or publications.europa.eu) under eu`. With `ACQUISITION_USER_AGENT` unset, the run fails before any request is sent, with `MissingUserAgent: set ACQUISITION_USER_AGENT to the User-Agent your acquisition requests carry, naming you and a contact`. The web app's Ingest screen shows a source URL field when the chosen jurisdiction is `eu`; it takes `eur-lex.europa.eu` addresses only.

</details>

### Keep the contract

`contract/openapi.json` is the server's OpenAPI schema; tests fail when it drifts, and the web app (or your client) generates TypeScript types from it. Compare it with the live one:

```bash
uv run codify serve --openapi > /tmp/openapi.json
diff /tmp/openapi.json contract/openapi.json && echo identical
```

```text
identical
```

### Run the web app

With `codify serve` running, from the repository root:

```bash
cd apps/web
pnpm install
pnpm dev
```

The app runs on `http://localhost:5174` and opens on `gb` until you choose a jurisdiction; open `http://localhost:5174/jurisdictions/xa/laws` (Atlantis on the Jurisdictions screen) for the worked example. Its development proxy at `/api/proxy` forwards to `http://localhost:8000` or `VITE_API_URL` ([`apps/web/vite.config.ts`](../../apps/web/vite.config.ts)):

```bash
curl localhost:5174/api/proxy/health
```

```json
{"status":"ok"}
```

<details>
<summary>The screens</summary>

From [`apps/web/src/router.tsx`](../../apps/web/src/router.tsx):

| Screen | What it does |
| --- | --- |
| Laws | Laws of the chosen jurisdiction, or all, 25 at a time; `doctype`, `year` and `q` in the address filter them |
| Reader | Draws a version's sections, opened at the version and provision (eId, stable element identifier) its address names (figure below); prints; downloads the Akoma Ntoso |
| Search | Hybrid search over one jurisdiction; see [Search](search.md) |
| Ingest | Uploads one file, or under `eu` takes an EUR-Lex address, and opens its run |
| Run | Follows a run's events; offers a retry on failure; opens the law on success |
| Jurisdictions | Every jurisdiction config; each opens with its first 50 laws |

</details>

![A search match in the web app links to an address in three parts. First, /laws/21278932-9b16-4320-b50a-c832f3c61806: the law, by its id in this database. Second, ?v=20cb9d70-142f-47fc-993b-98d884a79ad8: the version to open; without it the reader opens the newest. Third, #part_IV__sec_12: the provision, by its eId; the reader scrolls to it and marks it with a bar for a moment. Below, the reader shows section 12 of the worked example, Right of access, with that bar: (1) Every person has the right of access to the official text of any law in force, in a form that is complete, current and free of charge. (2) No fee shall be charged for access to the official text in electronic form, but the Publishing Authority may charge a reasonable fee, not exceeding the cost of production, for a certified printed copy.](../figures/v13-reader-address.svg "A search match's address names the law, the version and the provision, and the reader opens there; screenshot of the worked example from a real run, whose ids differ from yours.")

### Connect an AI agent through MCP

`codify mcp` serves read tools, by default over standard input and output to a client that starts it. A typical client configuration (use your own checkout and database):

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

For a client that connects by address, serve streamable HTTP on `127.0.0.1:8001/mcp` (`--host` to widen):

```bash
uv run codify mcp --http
```

The web app's sidebar dialog, Connect an MCP client, gives the same command and address; the web app itself does not serve MCP.

No tool writes to the store. The [MCP tools reference](../reference/mcp-tools.md) gives every parameter.

<details>
<summary>The tools and what they need</summary>

From [`codify/mcp/server.py`](../../codify/mcp/server.py):

| Tool | Returns | Needs |
| --- | --- | --- |
| `list_jurisdictions` | Code, name and languages of every jurisdiction config | Nothing |
| `get_jurisdiction` | Names, legal tradition, calendar, languages and document classes, without parsing patterns | Nothing |
| `list_laws` | A page of laws, filtered by jurisdiction, document type, year or title | Database |
| `get_law` | One law and every stored version | Database |
| `get_version` | One version's URIs, dates and grade; with `include_xml`, its Akoma Ntoso, cut at one million characters and flagged `akn_xml_truncated` | Database |
| `search_provisions` | Hybrid search, as in [Search](search.md); one embeddings call per query | Database and embeddings endpoint |
| `compare_versions` | A provision-by-provision comparison of two versions, one chat call per reference provision; refused above 200 reference provisions | Database, chat model and embeddings endpoint |

Failures come back as tool errors, such as `no jurisdiction 'zz'`.

</details>

### Expose it beyond your machine

| Limit | What to do |
| --- | --- |
| **No authentication**: anyone who reaches the HTTP server can ingest, and the MCP server can run comparisons on your model account | Keep the `127.0.0.1` default behind an authenticating reverse proxy; for read-only publication, forward only `GET` routes |
| **No CORS headers or static files** from `codify serve` ([`codify/serve/app.py`](../../codify/serve/app.py)) | Serve browser apps and the API from one origin |
| **No production web host**: `pnpm build` writes `apps/web/dist`, which calls `/api/proxy`, provided only by the development server | Provide `/api/proxy` yourself ([Deployment](../operate/deployment.md#build-the-web-app)) |
| **Runs are in memory**: a restart loses them (`"lost_on_restart": true`), as the run figure above shows | Treat the stored law as the record |
| **Errors can name server paths**: an unknown `GET /jurisdictions/{code}` answers 404 with the absolute config path in `detail` | Have the proxy replace error bodies |

[Deployment](../operate/deployment.md) covers running these as services.

## Check your result

- `GET /laws?jurisdiction=xa` lists the worked example under `/akn/xa/act/1992/7`.
- An MCP client lists the tools above, and `get_jurisdiction` with code `xa` returns `"name": "Atlantis"`.

## What can go wrong

| What you see | Cause | Fix |
| --- | --- | --- |
| `No module named 'uvicorn'; install the serve extra: uv sync --extra serve` | Missing `serve` extra (the MCP server says the same for `mcp`) | Install every extra in one `uv sync` ([Install](../start/install.md)) |
| A run fails with `dispatch: PDF ingestion requires an LLMClient` | PDF, `.txt` or `.md` upload with `LLM_BASE_URL` unset | Set it, restart the server, retry |
| `{"detail":"no jurisdiction config for 'zz'"}` with status 422 (404 under `/jurisdictions/{code}/laws`) | No config for that code | Use a code from `GET /jurisdictions` |
| `{"detail":"only an EU publications URL (eur-lex or publications.europa.eu) under eu"}` with status 422 | `ingest-url` with another jurisdiction or host | Upload to `/runs/ingest` |
| Status 429 | 100 runs queued | Wait |
| `{"detail":"no such run"}` with status 404 | Restart, or run older than the 200 kept | Check `GET /laws`; if absent, ingest again |
| An MCP tool error `set LLM_BASE_URL to an OpenAI-compatible chat endpoint` | `compare_versions` without a chat endpoint | Set the chat settings in the MCP server's environment |
| `{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Bad Request: Missing session ID"}}` | Plain `curl` to the MCP address | Expected; use an MCP client |

## Status and limits

| Capability | Status |
| --- | --- |
| Reading laws, versions, Akoma Ntoso and the section tree over HTTP | **Available** |
| XML upload over HTTP, with live events, retry and cancel | **Available** |
| PDF upload over HTTP | **Available**, with a chat model |
| `.txt` or `.md` upload over HTTP | **Available**, with a defect: fails at stage `extract`; use `ingest-one` |
| Ingest by URL (route and web form) | **Available**: EU publication addresses under `eu`, with `ACQUISITION_USER_AGENT` set |
| Web app | **Available** as a development server |
| MCP read tools (standard input and output, streamable HTTP) | **Available** |
| Embedding the server or MCP server in your application | **Library only**: `codify.serve.create_app`, `codify.mcp.create_server` |
| Authentication, accounts and access control | **Not in the core** |
| Durable runs that survive a restart | **Not in the core** |
| Container images or a production host | **Not in the core** |

:::note[On the hosted Platform]
The hosted Platform adds authentication with role-based access control, token issuance and a tamper-evident audit trail.
:::

## Next

[Interoperate](interoperate.md)
