# Web app, REST API and MCP

[Back to the README](../README.md)

Follow the [quick start](../README.md#quick-start), then
[set up storage](usage.md#store-search-compare). Reading stored laws needs the
database; search also needs an embeddings endpoint, and ingestion needs a chat model.

The HTTP and MCP servers have no built-in authentication. Keep them on localhost
or protect them with an authenticated proxy. Ingestion runs are held in memory
and are lost on restart; successfully stored documents remain in PostgreSQL.

## Docker support

The repository includes a Docker Compose service for PostgreSQL, with the search
extensions Codify needs. It does not currently include container images or Compose
services for the REST API, MCP server or web app. Those run locally as described below.

After the quick start, start the database from the repository root:

```bash
docker compose up -d --wait postgres
uv sync --extra migrations
uv run --extra migrations alembic -c alembic.ini upgrade head
```

Check that `POSTGRES_URL` points to this local database before applying migrations.
The default is `postgresql://codify:codify@localhost:5432/codify`. Compose binds the
database to localhost and keeps its data in a named volume. If you change
`CODIFY_PG_PORT`, update the port in `POSTGRES_URL` too.

`docker compose stop postgres` stops the database without deleting its data.

## REST API

Start the server to upload documents, read stored laws and search provisions:

```bash
uv sync --extra serve
uv run --extra serve codify serve             # http://127.0.0.1:8000, docs at /docs
```

In another terminal, upload your document and follow the returned run ID:

```bash
curl -F file=@act.pdf -F jurisdiction=xa localhost:8000/runs/ingest
curl -N "localhost:8000/runs/<run-id>/stream"    # server-sent events until the run ends
```

The interactive API documentation is at `/docs`. The main routes are:

| Route | Purpose |
| --- | --- |
| `GET /jurisdictions`, `GET /jurisdictions/{code}` | List configurations or read one |
| `GET /laws`, `GET /laws/{id}` | Find laws or read their metadata |
| `GET /laws/{id}/versions` | List versions with cursor pagination |
| `GET /versions/{id}` | Read a version, including its AKN |
| `GET /versions/{id}/document` | Get the section tree used by the reader |
| `GET /search?q=...&jurisdiction=...` | Search provisions |
| `POST /runs/ingest` | Upload a file for ingestion |
| `POST /runs/ingest-url` | Ingest an EU publication URL under jurisdiction `eu` |
| `GET /runs/{id}`, `GET /runs/{id}/stream` | Check progress or follow events |
| `POST /runs/{id}/cancel`, `POST /runs/{id}/retry` | Cancel or retry a run |

Replace `<run-id>` in the example with the ID returned by the upload. The stream
replays existing events, then follows new ones. Successful ingestions are stored
in the database. The server retains the 200 most recent finished runs in memory;
run responses include `lost_on_restart` to make that limitation explicit.

URL ingestion accepts EU publication sources (EUR-Lex or publications.europa.eu).
Upload other PDFs through `/runs/ingest`.

### Contract

[`contract/openapi.json`](../contract/openapi.json) is the server's OpenAPI schema, generated from the routes'
response models (`codify/serve/schemas.py` and the library models they carry, such as
`JurisdictionConfig`); a test fails when it drifts. After a route change:

```bash
uv run --extra serve codify serve --openapi > contract/openapi.json
```

Clients can generate types from this schema. The web app uses `openapi-typescript`.

## MCP server

Expose search, document retrieval and comparison to an MCP client:

```bash
uv sync --extra mcp
uv run --extra mcp codify mcp                     # stdio, for a client that launches the server
uv run --extra mcp codify mcp --http --port 8001  # streamable HTTP at http://127.0.0.1:8001/mcp
```

For MCP clients that use a `mcpServers` configuration, add:

```json
{
  "mcpServers": {
    "codify": {
      "command": "uv",
      "args": ["run", "--extra", "mcp", "--directory", "/path/to/codify", "codify", "mcp"],
      "env": { "POSTGRES_URL": "postgresql://codify:codify@localhost:5432/codify" }
    }
  }
}
```

The tools do not write to the corpus:

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

Tool failures return an error message. `--http` binds to
localhost unless `--host` says otherwise; there is no authentication here either.

## Web app

The web app connects to `codify serve` to browse laws, search provisions and
upload documents. It needs Node 22 and pnpm 11 (`corepack enable` gives you pnpm).

```bash
uv run --extra serve codify serve          # leave running in one terminal
```

In a second terminal, from the repository root:

```bash
cd apps/web
pnpm install
pnpm dev                                  # http://localhost:5174, proxied to :8000
```

`VITE_API_URL` points the proxy at a server elsewhere. The screens: **Laws** lists
one jurisdiction or all of them, filtered by title; a law opens in the **reader**,
which renders the section tree from `/versions/{id}/document` and offers the AKN as
a download; **Search** is the server's hybrid search over one jurisdiction, each
match opening a preview and linking into the reader; **Ingest** uploads a file (or
an EU publications URL under `eu`) and follows the run's events to the stored law.
The web app has no sign-in; access control must be provided by the server or proxy.

Its types come from the contract (`pnpm generate:contract` after `codify serve
--openapi`); CI fails when the committed types drift. `pnpm typecheck`, `pnpm test`
and `pnpm build` are the gates. Codify Platform has additional interfaces that
are not included here: the
world map, corpus tiers, the Bluebell source pane, text and original-file
downloads, lenses, accounts and analytics.
