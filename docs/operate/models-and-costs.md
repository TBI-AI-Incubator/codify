---
title: Models and costs
description: Connect Codify to a chat model and an embeddings model, know which calls a document makes, and see where the cost of those calls is recorded.
sidebar:
  order: 3
---

You end with Codify pointed at the model endpoints you chose, and you know what each must support, how many calls a document makes, and where their cost is recorded.

## Before you start

- A source checkout. See [Install](../start/install.md).
- An account with a model provider, or a model server of your own, that speaks the OpenAI-compatible HTTP API.

## The idea in brief

Codify ships no model. It sends requests, through the OpenAI SDK, to endpoints you name, so any service that implements the same routes can stand behind it ([`codify/core/llm.py`](../../codify/core/llm.py), [`codify/embed/client.py`](../../codify/embed/client.py)). The choice of provider, and of where your gazettes go, stays with you.

Models do three jobs, each on its own endpoint if you like:

| Job | Used by | Route |
| --- | --- | --- |
| Read scanned pages, read a document's title and date, write each provision's text, find definitions and references to other laws | `codify ingest-one`, PDF ingest in `codify serve` | Chat completions, with images |
| Turn provisions and queries into vectors for search | `codify load --embed`, `codify search`, search in `codify serve` and `codify mcp` | `/embeddings` |
| Pair and judge provisions when comparing two documents | `codify compare`, the MCP compare tool | Chat completions and `/embeddings` |

The structure of a law never comes from a model ([Architecture](../concepts/architecture.md#the-shape)). A model writes text into a scaffold built from your jurisdiction config, so a weaker model costs text quality, not the numbering of the law.

No model is needed to scan a corpus, run the deterministic XML lanes, `codify load` without `--embed`, or use any read route except search and the MCP compare tool.

## Steps

### Name the chat model

Set three variables in the nearest `.env` (the checkout root, from a checkout), or in the process environment. Start from the example file:

```bash
cp .env.example .env
```

The chat lines in `.env` then read:

```text
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=
LLM_MODEL=gemini-3.7-flash
```

Replace them with your provider's values. The copy also points `POSTGRES_URL` at the local Compose database; change it if yours differs.

| Variable | Meaning | When unset |
| --- | --- | --- |
| `LLM_BASE_URL` | Base URL of the OpenAI-compatible endpoint | `codify ingest-one` stops with exit status 2 and `no chat endpoint: set LLM_BASE_URL (and LLM_API_KEY) ...`; `codify compare` stops with `set LLM_BASE_URL to an OpenAI-compatible chat endpoint`; `codify serve` starts, and fails each PDF ingest at stage `dispatch` |
| `LLM_API_KEY` | Bearer key | The literal `sk-codify-dev` is sent |
| `LLM_MODEL` | Model name as the endpoint knows it | `gemini-3.7-flash` |

`codify ingest-one --model` overrides `LLM_MODEL` for one run. The bundle's `manifest.json` records the configured `model`, `ocr_model` and `fallback_model`, so you can tell later which model produced a document, but not whether the fallback was called ([Bundle reference](../reference/bundle.md)).

### Name the embeddings model

| Variable | Meaning | When unset |
| --- | --- | --- |
| `EMBEDDING_BASE_URL` | Base URL of the endpoint | `LLM_BASE_URL`; with neither, `set EMBEDDING_BASE_URL (or LLM_BASE_URL) to an embeddings endpoint` |
| `EMBEDDING_API_KEY` | Bearer key | `LLM_API_KEY`, then `sk-codify-dev` |
| `EMBEDDING_MODEL` | Model name | `gemini-embedding-2` |

With neither base URL in the environment or `.env` (before the `cp` above, which sets `LLM_BASE_URL` in `.env`, or once you remove that line), `codify search` prints that message and stops before calling anything:

```bash
uv run codify search "publication" --jurisdiction xa
```

```text
set EMBEDDING_BASE_URL (or LLM_BASE_URL) to an embeddings endpoint
```

Choose the embeddings model once per database. Each vector is stored under the name of the model that made it, and search compares a query only with vectors stored under the query's model ([`codify/retrieve/hybrid.py`](../../codify/retrieve/hybrid.py), [`codify/storage/retrieval.py`](../../codify/storage/retrieval.py)). Change `EMBEDDING_MODEL` and the vector half of search finds nothing until you embed the stored laws again; the old vectors stay beside the new ones.

### Optional: a fallback model and a second OCR engine

Three settings change how scanned pages are read, in `codify ingest-one` only; `codify serve` builds its client from the three chat variables alone ([`codify/cli_store.py`](../../codify/cli_store.py)).

| Setting | Effect |
| --- | --- |
| `LLM_CONTENT_FILTER_FALLBACK_MODEL`, or `--fallback-model` | When the first model's content filter refuses a page read, the metadata read or a body-fill window, the request goes again to this model, at its price. Some providers' filters refuse published legal text as recitation. Unset, a refused page is recorded as unreadable. The definition and reference passes never use it |
| `AZURE_OPENAI_ENDPOINT` with `AZURE_OPENAI_API_KEY` | Turns on a second engine, Mistral OCR on Azure AI Foundry, called directly. Every PDF is also sent to it for page layout and a second transcription, up to 25 pages a call. `AZURE_FOUNDRY_OCR_RPM` (default 40) caps its requests per minute within one process |
| `LLM_OCR_MODEL`, or `--ocr-model` | The transcription engine. Empty: the chat model reads pages through its vision route. Another model name: page reads go to that model on the chat endpoint. A name starting `mistral-ocr` or `mistral-document`: transcription goes to the second engine, which needs the `AZURE_OPENAI_*` pair |

[OCR](../guide/ocr.md) explains the second engine and the reads in depth.

## What an endpoint must support

| Capability | Needed for | Where the code asks for it |
| --- | --- | --- |
| Image input as `data:` URLs in chat messages | Scanned pages, which in practice is most gazettes | `vision` in [`codify/core/llm.py`](../../codify/core/llm.py) |
| Structured output against a JSON schema (`response_format` with a schema) | Metadata and body-fill | `chat_schema` |
| JSON mode (`response_format: json_object`) | Definitions and references to other laws | `chat_json` |
| Embeddings with a `dimensions` parameter, returning 768 values | Search; the vector column is `halfvec(768)` | [`codify/embed/client.py`](../../codify/embed/client.py), migration `0020` |

A reasoning model that opens its reply with `<think>...</think>` is handled only on the definition and reference passes (`chat` and `chat_json`), which strip the block. Page reads (`vision`) and metadata and body-fill (`chat_schema`) do not, so such a model can break structured output and leave its thinking in page text: turn thinking output off, or choose a model that does not emit it. A model without image input can still structure text you supply (`.txt` or `.md` through `ingest-one`), but cannot read a scanned page.

Codify does not ship, configure or test a local model. A model server on your own hardware that implements these routes fits the same variables, which matters where law may not leave the country before publication.

## How many calls a document makes

![The pipeline, top to bottom, with the stages that call a model marked and the counts from the worked example's run: Read pages, 5 pages with one model read each; Read metadata, 1 call; Fill text, 3 windows with one call each; Enrich, 11 passes, one with a model. Find anchors (26 found by the rules), Build scaffold, Parse and Validate (1 finding, grade warning) call no model. The bundle does not record how many calls Enrich made. Publisher XML skips every stage before Validate and joins there. The result, /akn/xa/act/1992/7, is stored in PostgreSQL by codify load and read through the command line, the HTTP API, the MCP server and the web app.](../figures/v1-pipeline-summary.svg "Where the model calls fall in the worked example, data/fixtures/synthetic/xa/legislation-act-1992.pdf: a filled square marks a stage that calls a model, with the count from its real run except Enrich's, which the bundle does not record.")

The rule behind each count, read from the code; the worked example also had one definitions section and no second OCR engine. Its bundle records neither reference batches nor retries:

| Stage | Calls |
| --- | --- |
| Page reads | One vision call per page without a trustworthy text layer |
| Second engine, if configured | One per 25 pages |
| Metadata | One |
| Body-fill | One per window of provisions |
| Definitions | One per section whose heading marks it as definitions, if its text is longer than 50 characters |
| References to other laws | One per batch of body text, sized by characters |
| Retries | Up to two more attempts for a chat call that meets a rate limit, a timeout, a connection error or a `502`, `503` or `504`; up to five more on the second OCR engine |

The bundle's `events.jsonl` shows the counts: one `page_extracted` event per page, with `"method":"ocr"` for each model read, and one `structure_progress` event per window (`window 1/3` and so on). To budget, multiply calls by the tokens your provider reports for a typical page of your gazette. Search embeds each provision once at load time, in batches of 100, and each query once.

Calls run concurrently up to fixed limits: 35 page reads ([`ocr.py`](../../codify/pipeline/enrich/ocr.py)), 30 body-fill windows ([`structure.py`](../../codify/pipeline/enrich/structure.py)), 10 reference batches ([`inline_markup.py`](../../codify/pipeline/enrich/inline_markup.py)) and 5 embedding batches. Each chat call waits up to 600 seconds. A retry waits at least as long as a `Retry-After` header asks, up to 30 seconds (120 on the second OCR engine). If your provider's rate limit is lower, expect `429` responses and retries; no setting lowers the concurrency.

## Where cost is recorded

Most endpoints report tokens but not cost, so Codify prices calls itself from the table in [`codify/core/pricing.py`](../../codify/core/pricing.py): a price per input and output token for each chat and embeddings model named there, and a price per page for the OCR engine. A `provider/` prefix on a model name is ignored in the lookup.

The cost goes to tracing records in Langfuse, an open-source tracing service, and nowhere else: the bundle manifest holds no token counts or prices, and the database stores none. Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` to send them. Each model call is recorded with its model name, token counts and, where priced, its cost, without the prompt or reply ([`codify/core/tracing.py`](../../codify/core/tracing.py)).

A model missing from the table is recorded with no cost, and a warning is logged once per model so that unpriced does not pass for free:

```python
from codify.core.pricing import cost_details
from codify.core.tracing import direct_observation, record_usage

print(cost_details("my-local-model", {"input": 1200, "output": 300}))
with direct_observation("direct.chat", as_type="generation", model="my-local-model") as obs:
    record_usage(obs, "my-local-model", {"input": 1200, "output": 300})
```

```text
Authentication error: Langfuse client initialized without public_key. Client will be disabled. Provide a public_key parameter or set LANGFUSE_PUBLIC_KEY environment variable.
None
2026-11-14 01:03:26 [warning  ] llm_model_unpriced             model=my-local-model
```

The first line is the Langfuse library reporting it has no keys and will record nothing; the `codify` command silences it when `LANGFUSE_PUBLIC_KEY` is unset.

The prices were copied by hand, some from list prices and some from one cloud region's billing meter, and they change. Check them before you rely on the totals; add your own models to price them. Your provider's bill is the authority.

## Check your result

- Once a law is loaded with `--embed`, `uv run codify search "<a phrase from a stored law>" --jurisdiction <code>` prints matches, one JSON line each, and exits 0.
- An `ingest-one` run ends with a bundle whose `manifest.json` has `"failed": null` and names the model you set. [First ingest](../start/first-ingest.md) walks through one.
- With Langfuse configured, its dashboard lists one generation per model call, with cost for priced models.

## What can go wrong

The messages for a missing endpoint are in the variable tables under Steps; set the variables and restart any server. The unpriced warning is under Where cost is recorded.

| What you see | Cause | What to do |
| --- | --- | --- |
| `PDF ingestion requires an LLMClient` at stage `dispatch` | `codify serve` started without `LLM_BASE_URL`. | Set it and restart the server. |
| `Model returned invalid JSON: ...` or `Model returned invalid structured output: ...` in the log | The model ignored the requested output format. | Use a model the provider documents for structured output. |
| A page is unreadable with a reason starting `content_filter` | The provider's filter refused the page. | Set a fallback model from another provider. See [OCR](../guide/ocr.md). |
| Search misses provisions you expect | The stored vectors were made under a different `EMBEDDING_MODEL`. | Embed again with the model you search with: `uv run codify load <bundle> --embed` re-embeds a version already stored. |

## Status and limits

| Capability | Status |
| --- | --- |
| Any OpenAI-compatible chat and embeddings endpoint | **Available**: the variables above |
| Content-filter fallback model and second OCR engine | **Available** in `codify ingest-one` only |
| Client-side cost per call | **Library only**: `codify.core.pricing.cost_details`; visible only through Langfuse |
| Cost totals per document or per corpus | **Not in the core** |
| Settings for concurrency, timeouts and retries | **Not in the core**: constants in the code |
| A bundled or tested local model | **Not in the core** |

## Next

[What is open](../open-core/what-is-open.md): what the core contains and what it leaves out.
