---
title: Security
description: How to report a vulnerability, which releases receive fixes, and what Codify protects against and leaves to the system you deploy it in.
sidebar:
  order: 4
---

The core is a library and two small servers; sign-in, network policy and backups are
yours to provide. Read this page before you expose any part of Codify beyond your own
machine.

## Report a vulnerability

Report privately; do not open a public issue.

1. On GitHub, open the repository's **Security** tab and choose **Report a vulnerability**.
2. Describe the class of problem and give a minimal, non-destructive way to reproduce it
   against a local install.
3. Leave out credentials, personal data, real case documents and exploit code aimed at a
   running system.

The supported versions are `main` and the latest minor release on PyPI. Fixes land on
`main` first and ship in the next release; the advisory names the fixed version.

## The trust model

| Part | Who can use it | What protects it |
| --- | --- | --- |
| The library and CLI | whoever runs the process | the operating system account |
| `codify serve` (HTTP) | anyone who can reach its port | nothing in the core: no sign-in, no roles |
| `codify mcp --http` | anyone who can reach its port | nothing in the core |
| `codify mcp` over standard input and output | the program that launched it | the launching program |
| The web app in `apps/web` | anyone who can load it | nothing in the core |
| The PostgreSQL database | anyone with its address and credentials | the database's own authentication |

## The servers have no authentication

`codify serve` and `codify mcp` register no authentication and no CORS middleware
([`codify/serve/app.py`](../../codify/serve/app.py),
[`codify/mcp/server.py`](../../codify/mcp/server.py)). Anyone who reaches the port can:

- read every law, version and provision in the database;
- upload a file and start an ingest run, which calls your model provider at your cost
  (`POST /runs/ingest`);
- cancel or retry other people's runs;
- through MCP, ask for a comparison, which makes model calls on up to 200 provisions per
  request (`COMPARE_CAP_PROVISIONS` in [`codify/mcp/server.py`](../../codify/mcp/server.py)).

Uploads have no size cap. Each is written in full to a temporary directory and kept for
retries until its run drops out of the last 200 finished runs or the server stops.

Both servers bind to `127.0.0.1` unless you pass `--host`
([`codify/cli_store.py`](../../codify/cli_store.py)), so by default only the same machine
can reach them. Start a server on the default port, 8000, in one terminal:

```bash
uv run codify serve
```

Then, from a second terminal:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/laws
```

```text
{"status":"ok"}
200
```

The second request carries no credentials and succeeds.

A cross-origin preflight (`OPTIONS /laws` with an `Origin` header) gets
`405 Method Not Allowed` and no `Access-Control-*` headers, so another site's page cannot
read responses. It can still send them: `POST /runs/ingest` takes an ordinary multipart
form, which needs no preflight, and the server does not check `Origin` (a cross-origin
`POST` with no body gets the usual `422`). So any page open in a browser on the same
machine can start an ingest run on a local server, unless the browser blocks public sites
from reaching local addresses.

Before you bind either server to another address, put it behind a proxy or gateway that
authenticates every request, and keep the upload and run routes from read-only users. See
[Deployment](../operate/deployment.md).

The run queue is in memory: two runs execute at once, at most 100 wait, and the server
answers `429` when the queue is full ([`codify/serve/runs.py`](../../codify/serve/runs.py)).
A restart loses every queued and finished run.

## Untrusted documents

Gazettes, publisher feeds and uploads are untrusted input.

| Risk | What the core does | Source |
| --- | --- | --- |
| XML external entities and entity expansion | The validator, the publisher-XML readers and storage parse with entity resolution, DTD loading and network access off, and refuse any DOCTYPE. Other modules (parts of enrichment, repair and translation) use lxml's default parser, which loads no external entities but accepts a DOCTYPE | `safe_parser`, `parse_xml` in [`codify/akn/_schema.py`](../../codify/akn/_schema.py) |
| Fetches redirected to internal addresses | On every redirect hop, resolves each hostname once, refuses private, loopback, link-local and cloud metadata addresses, then connects to the address it checked | [`codify/core/security.py`](../../codify/core/security.py), `GuardedTransport` in [`codify/acquisition/politeness.py`](../../codify/acquisition/politeness.py) |
| Instructions hidden in a provision | The comparison prompt fences each provision with a random marker, and flags for review a verdict whose note looks like injected instructions | [`codify/compare/prompts.py`](../../codify/compare/prompts.py) |

The DOCTYPE refusal, on a document that tries to read a local file:

```bash
uv run python -c "from codify.akn._schema import parse_xml; parse_xml('<!DOCTYPE x [<!ENTITY e SYSTEM \"file:///etc/passwd\">]><x>&e;</x>')"
```

```text
...
lxml.etree.XMLSyntaxError: XML declares a DOCTYPE; refusing to parse it
```

The address guard, on a loopback address, a cloud metadata address and a metadata
hostname:

```bash
uv run python -c "
from codify.core.security import ssrf_guard
for u in ['http://127.0.0.1:5432/', 'http://169.254.169.254/latest/meta-data/', 'http://metadata.google.internal/']:
    try: ssrf_guard(u)
    except Exception as e: print(type(e).__name__ + ':', e)
"
```

```text
SSRFBlocked: SSRF blocked: 127.0.0.1 in 127.0.0.0/8 (http://127.0.0.1:5432/)
SSRFBlocked: SSRF blocked: 169.254.169.254 in 169.254.0.0/16 (http://169.254.169.254/latest/meta-data/)
SSRFBlocked: SSRF blocked: blocked host metadata.google.internal (http://metadata.google.internal/)
```

The guard covers the acquisition adapters and the EUR-Lex fetcher, not the model and
embeddings endpoints you configure, which usually sit on a private network by design.

## Where your documents go

![A Codify deployment, top to bottom. A browser (people reading the law) and an AI agent (an MCP client) reach a reverse proxy with sign-in, which you provide and which is the only part that authenticates. The proxy forwards / to the web app's static files in apps/web/dist/, which keep nothing; /api/proxy/* to the HTTP server, codify serve on 127.0.0.1:8000, which keeps runs in memory and uploads in a temporary directory, both gone after a restart; and /mcp to the MCP server, codify mcp --http on 127.0.0.1:8001, whose sessions are in memory and gone after a restart. Both servers use PostgreSQL (POSTGRES_URL), which holds all stored law and is the only state that survives a restart. All of these sit on a private network; both servers bind 127.0.0.1 by default. Leaving the network: the chat model (LLM_BASE_URL) receives each PDF ingest's page images and text and each MCP comparison's provisions; the embeddings endpoint (EMBEDDING_BASE_URL) receives each search query and each MCP comparison; tracing (LANGFUSE_HOST), if set, receives each model call's model, token counts and cost and each ingest's source path, but no prompts or replies. From the command line, codify ingest-one also sends to the chat model and tracing, and, if AZURE_OPENAI_ENDPOINT is set, sends the whole PDF to Azure OCR for a layout pass; the servers never call Azure OCR.](../figures/v3-deployment.svg "Schematic of a deployment from the source's defaults: only the proxy authenticates, only PostgreSQL survives a restart, and each dashed arrow is something that leaves your network.")

Every provision embedded, for example by `codify load --embed`, also goes to
`EMBEDDING_BASE_URL`, or to `LLM_BASE_URL` when that is unset. Azure OCR needs
`AZURE_OPENAI_API_KEY` as well, and also transcribes when `--ocr-model` names a
`mistral-ocr` model. Choose each endpoint as you would any processor of official
documents. See [Models and costs](../operate/models-and-costs.md).

With `LLM_BASE_URL` unset, `ingest-one` sends nothing: it stops with an error that names the variable.

Codify imports the Langfuse tracing SDK, which sends records of model calls to the
Langfuse host you configure. Without `LANGFUSE_PUBLIC_KEY` the SDK disables itself and
says so:

```text
Authentication error: Langfuse client initialized without public_key. Client will be disabled. ...
```

## Secrets and the database

- The CLI reads credentials from the environment and the nearest `.env` file, without
  overriding a variable already set. Git ignores `.env` files, and the build check refuses
  a wheel or source distribution that contains one
  ([`scripts/check_distribution.py`](../../scripts/check_distribution.py)).
- The Compose database is a local development service: it listens on `127.0.0.1` only,
  with user, password and database name all `codify`
  ([`docker-compose.yaml`](../../docker-compose.yaml)). Do not publish its port or reuse
  its credentials.
- With `POSTGRES_URL` unset, Codify falls back to that database only while `ENVIRONMENT`
  is unset, `localhost` or `development`. Anywhere else it stops:

  ```bash
  ENVIRONMENT=production uv run python -c "from codify.settings import database_url; database_url()"
  ```

  ```text
  ...
  RuntimeError: POSTGRES_URL must be set when ENVIRONMENT is not localhost/development
  ```

## The project's own supply chain

Every third-party GitHub Action in the workflows is pinned to a commit hash. Releases
publish to PyPI through trusted publishing, so no PyPI token is stored
([Versioning](versioning.md)). Dependabot proposes updates weekly after a cooling-off
period (security updates excepted), and nothing merges automatically
([`.github/dependabot.yml`](../../.github/dependabot.yml)).

## Status

| Capability | Status |
| --- | --- |
| Private vulnerability reporting | **Available**: the repository's Security tab |
| Hardened XML parsing with DOCTYPE refusal | **Available**: the validator, the publisher-XML readers and storage |
| Address guard on outbound fetches | **Available**: acquisition adapters and the EUR-Lex fetcher |
| Production database guard | **Available**: `ENVIRONMENT` and `POSTGRES_URL` |
| Masking of emails, tokens and IP addresses in logs | **Library only**: `codify.core.redaction.redact_processor`, which the CLI does not install |
| Authentication, roles and audit for the servers and web app | **Not in the core** |
| Upload size limits | **Not in the core** |

:::note[On the hosted Platform]
Codify Platform adds authentication with role-based access control, management of which
laws each team can reach, and a tamper-evident audit trail of who read and changed what.
:::

## Next

[Decision records](decisions.md): why the core is built the way it is.
