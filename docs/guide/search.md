---
title: Search stored law
description: Embed the provisions of a stored act and search one jurisdiction by wording and by meaning, from the command line, over HTTP, through MCP or in the web app.
sidebar:
  order: 9
---

You end with the worked example stored, embedded and searchable, and a way to tell whether a search used one method or both.

## Before you start

- A migrated PostgreSQL database at `POSTGRES_URL` ([Install](../start/install.md)).
- The bundle from [your first ingest](../start/first-ingest.md) in `bundle/`, or any Akoma Ntoso file.
- An OpenAI-compatible `/embeddings` endpoint returning 768-dimension vectors, which every search surface needs.

## The idea in brief

Search returns *provisions*, the smallest unit Codify stores (a numbered subsection, paragraph or point), because legal citations point at them. Each is keyed by its eId, an element's stable identifier in [Akoma Ntoso](../concepts/akoma-ntoso.md).

Search is *hybrid*: "official text" must match exactly, and "free copies of the law" should still find the section on fees. An embedding is a vector a model computes from a text's meaning.

![Hybrid search, top to bottom. A query, such as "right of access to the official text", passes the scope: one jurisdiction, the current version of each law, normative provisions only, applied inside each arm. The lexical arm ranks provisions by BM25 on the query's words, where rare words weigh more than common ones. The dense arm calls a model to embed the query and ranks provisions by how near their embeddings are to it. Each arm passes on up to 30, ranked. Reciprocal rank fusion scores each provision 1 / (60 + lexical rank) + 1 / (60 + dense rank); an arm that did not find it adds nothing. Matches come out best first, each with its eid, score and text.](../figures/v11-hybrid-search.svg "Schematic: follow a query through the scope into both arms, then add one term per arm that found a provision, with 30 and 60 fixed in code.")

The fusion is in [`codify/storage/retrieval.py`](../../codify/storage/retrieval.py).

<details><summary>How the lexical arm reads words</summary>

Codify's own tokeniser, not the PostgreSQL stemmers, treats index and query alike: a Snowball stemmer (suffix-stripping rules for one language) where one exists, a light stemmer for Arabic, Hebrew vowel points and similar marks stripped ([`codify/search/tokeniser.py`](../../codify/search/tokeniser.py)). Query terms of three or more characters also match similar spellings in the corpus, to catch OCR errors and typing slips ([`codify/storage/lexicon.py`](../../codify/storage/lexicon.py)).

</details>

A search covers one jurisdiction (every surface requires its code, and embeddings are partitioned by it), the current version of each law, and normative provisions only; text with no legal effect, such as an explanatory note, is not ranked.

<details><summary>Units stored but never ranked</summary>

Markers for untranscribed text, table rows and runs of digits are stored but never embedded or ranked (`excluded_from_pool` in [`codify/storage/models.py`](../../codify/storage/models.py)). Earlier versions of a law stay stored.

</details>

## Steps

`load --embed`, `codify search` and `GET /search` need a model; no output is shown for them.

### Point Codify at an embeddings endpoint

Set these in `.env` (working directory or a parent) or export them; exports win.

```bash
export EMBEDDING_BASE_URL=http://localhost:8080/v1
export EMBEDDING_API_KEY=your-key
export EMBEDDING_MODEL=your-embedding-model
```

<details><summary>Fallbacks and defaults</summary>

| Variable | Falls back to | Default |
| --- | --- | --- |
| `EMBEDDING_BASE_URL` | `LLM_BASE_URL` | none: search refuses to run |
| `EMBEDDING_API_KEY` | `LLM_API_KEY` | `sk-codify-dev` |
| `EMBEDDING_MODEL` | none | `gemini-embedding-2` |

</details>

The model must support 768 dimensions, the size of the vector column ([environment reference](../reference/environment.md)).

<details><summary>What is embedded</summary>

The client always asks for 768 dimensions ([`codify/cli_store.py`](../../codify/cli_store.py)). Each provision is embedded with its section heading before its text, and vectors are normalised before storage ([`codify/storage/embeddings.py`](../../codify/storage/embeddings.py), [`codify/embed/client.py`](../../codify/embed/client.py)).

</details>

### Store and embed the act

```bash
uv run codify load bundle/ --embed
```

`embedded` counts the provisions embedded; without `--embed` it is 0:

```bash
uv run codify load bundle/
```

```json
{"version_id": "20cb9d70-142f-47fc-993b-98d884a79ad8", "work_uri": "/akn/xa/act/1992/7", "embedded": 0}
```

Loading again reuses the stored version, so you can add `--embed` later.

### Search from the command line

```bash
uv run codify search "right of access to the official text" --jurisdiction xa -k 5
```

It prints one JSON match per line, best first, with `eid`, fused `score` and `text`; exit 1 means no match. `-k` defaults to 10; `--language` (such as `eng`) searches that language's expression where stored, the original otherwise ([CLI reference](../reference/cli.md#search)).

### Search over HTTP, MCP or the web app

[Serve and publish](serve-and-publish.md) shows how to start each.

- **HTTP**: `GET /search?q=...&jurisdiction=xa`; matches also carry the ids, title and work URI a client needs to link ([`GET /search`](../reference/http-api.md#get-search), [`codify/serve/app.py`](../../codify/serve/app.py)).

  ```bash
  curl "localhost:8000/search?q=right%20of%20access&jurisdiction=xa&k=5"
  ```

- **MCP**: the [`search_provisions`](../reference/mcp-tools.md#search_provisions) tool ([`codify/mcp/server.py`](../../codify/mcp/server.py)).
- **Web app**: the Search screen asks for 25 matches, previews each and links it into the reader at that provision ([`apps/web/src/routes/search/index.tsx`](../../apps/web/src/routes/search/index.tsx)).

### Check the lexical arm without a model

This script runs fixed multilingual queries against the lexical arm over the synthetic test laws, then deletes what it wrote (`--keep` commits it). It reads `tests/fixtures/`: run it from a checkout, before storing the act or on an empty database.

```bash
uv run python -m codify.eval.synthetic_retrieval
```

```text
...
lexical arm, k=50, 39 queries
  ara  n=5   recall@10=1.000  recall@50=1.000  ndcg@10=0.767  mrr=0.695
  eng  n=18  recall@10=1.000  recall@50=1.000  ndcg@10=0.870  mrr=0.824
...
  recall@10=1.000  recall@50=1.000  ndcg@10=0.907  mrr=0.877  (all)
```

<details><summary>Why the ranking scores vary between runs</summary>

Recall is stable; `ndcg@10` and `mrr` can move by a few hundredths, because ties are ordered by database ids, which are new each run. A second run gave `ndcg@10=0.890` for `eng` and `0.911` for all.

</details>

## Check your result

A version is ready for the dense arm when `embedded_at` is set. Export `POSTGRES_URL` in `postgresql://` form; `psql` does not read `.env`:

```bash
psql "$POSTGRES_URL" -c "SELECT v.expression_uri, v.embedded_at IS NOT NULL AS embedded, count(p.id) AS provisions FROM versions v LEFT JOIN provisions p ON p.version_id = v.id GROUP BY v.id"
```

`embedded` from `load --embed` should equal this provision count less the units never ranked. If every search score is 0.0164 or less, no provision was found by both arms.

<details><summary>Where 0.0164 comes from</summary>

Each arm passes on at most 30 candidates. A provision found by one arm scores at most 1/61 (about 0.0164); one found by both scores between 2/90 (about 0.022) and 2/61 (about 0.033).

</details>

## What can go wrong

| What you see | Cause | Fix |
| --- | --- | --- |
| `set EMBEDDING_BASE_URL (or LLM_BASE_URL) to an embeddings endpoint` (exit 1; 503 from `/search`; an MCP error result) | No endpoint set; `load --embed` stores nothing | Set the variables in the first step |
| Traceback ending `EmbeddingError: embeddings call failed: APIConnectionError` | Endpoint does not answer (on a closed port, retries stop after about 15 seconds) | Check the URL and service |
| Every score is 0.0164 or less | Loaded without `--embed`; `EMBEDDING_MODEL` is not the model that embedded it (only vectors under the same name compare); query words absent; or the arms disagreed | Check `embedded_at` and the model name, then read each result's text |
| Results for words absent from the act | The dense arm always returns the nearest provisions, however distant | A result is not proof of a match; read it |
| No output, exit 1 | Nothing matched, or no law there; the CLI does not check the code (HTTP and MCP answer `no jurisdiction config for 'zz'`) | Check the code against `GET /jurisdictions` |
| `argument -k: 0 is not a positive integer` (exit 2) | HTTP and MCP also cap `k` at 100 | Pass 1 to 100 |
| A law ingested through `codify serve` matches only on its words, and the server log shows `stored_unsearchable` | The server had no embeddings endpoint when it stored the law | Restart it with the variables set, then download its XML ([Serve and publish](serve-and-publish.md)) and run `uv run codify load act.akn.xml --jurisdiction xa --title "Legislation Act, 1992" --embed` (bare XML has no manifest, so it needs `--jurisdiction` and `--title`); the stored version is reused |

## Status and limits

| Capability | Status |
| --- | --- |
| Embedding a stored version (`load --embed`) | **Available** |
| Hybrid search (`codify search`, `GET /search`, MCP `search_provisions`, the web Search screen) | **Available**, with an embeddings endpoint |
| Embedding a stored version by id, without its file | **Library only**: `codify.storage.embeddings.embed_and_stamp` |
| Searching one law or version, or one document type | **Library only**: `codify.retrieve.hybrid.retrieve` takes `law_id`, `version_id` and `doctype` |
| Searching several jurisdictions at once | **Library only**: pass a `codify.storage.versions.current_scope` scope and your own query vector to `codify.storage.retrieval.hybrid_search` |
| Search with no embeddings endpoint | **Not in the core**; only the evaluation script runs the lexical arm alone |
| Embedding documents ingested through `codify serve` | **Available**, with an embeddings endpoint set when the server starts |
| Candidates per arm 30, fusion constant 60, embedding batch 100 | Fixed in code |

## Next

[Serve and publish](serve-and-publish.md)
