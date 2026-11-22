---
title: Architecture
description: How Codify turns a source document into stored, searchable Akoma Ntoso, and which module does each part.
sidebar:
  order: 1
---

You end with a map of how a document moves through Codify (one Python package, `codify`, one command of the same name, one PostgreSQL database) and which module does each step.

![The pipeline, top to bottom, with the bundle files each stage leaves. A source PDF (5 scanned pages in the worked example) goes to Read pages, which calls a model once per page (5 reads) and leaves pages/page-001.png onwards, source.txt, page_spans.json and segmentation.json; Read metadata calls a model once (title, date, number); Find anchors uses the config's rules (26 anchors) and leaves anchors.jsonl and ambiguity.jsonl; Build scaffold holds the 26 part and section headings, bodies empty, in scaffold.bluebell; Fill text calls a model once per window (3 windows) and its coverage is measured in coverage.json; Parse turns Bluebell into Akoma Ntoso; Enrich runs 11 passes, one with a model; Validate reports 1 finding, grade warning, in validator.json. The output is Akoma Ntoso XML, /akn/xa/act/1992/7, in final.akn.xml, beside manifest.json and events.jsonl, which cover the whole run. The jurisdiction config (xa/config.json) feeds every stage from Read pages to Enrich. Publisher XML skips every stage before Validate and joins there. Store puts the result in PostgreSQL by codify load, read through the command line (codify), the HTTP API (codify serve), the MCP server (codify mcp) and the web app (apps/web).](../figures/v1-pipeline.svg "The PDF lane with the bundle files each stage leaves, so you can check any stage against its evidence: a filled square marks a stage that calls a model, and every count is from the worked example's real run.")

## The shape

A source goes in; out come [Akoma Ntoso](akoma-ntoso.md) XML (AKN, the OASIS standard for legal documents) and findings about it. Storage, embedding and serving are optional.

**The structure does not come from the model.** Citations, cross-references and amendments point at a provision's identifier (its eId, for example `sec_2__subsec_1__point_h`). If a model decided where sections begin, two runs over one gazette could number a law differently. Codify finds the structure with patterns from your [jurisdiction config](../guide/write-a-jurisdiction-config.md) and asks the model only for the text under each identifier.

**The model fills a Bluebell scaffold, never XML.** [Bluebell](bluebell.md) is a plain-text notation whose parser emits AKN, so the parser decides the shape of the XML. [Structure](../guide/structure.md) follows one section of the worked example through both decisions, file by file.

**Findings do not stop a run.** The validator reports what it can measure (a numbering gap, an unreadable page, an empty provision), and the findings set the structural grade ([Validate and repair](../guide/validate-and-repair.md)). `codify load` stores a document whatever its grade; what a blocking finding means for publication is your decision.

## Lanes

The first step chooses a lane from the kind of source ([`codify/pipeline/formats/__init__.py`](../../codify/pipeline/formats/__init__.py)). A publisher's structured format has already done the structuring, so those lanes need no model and no key.

| Source | Lane | Calls a model |
| --- | --- | --- |
| PDF | Model-assisted, below | Yes |
| Plain text (`.txt`, `.md`) | The same stages without page extraction | Yes |
| A publisher's own AKN (`.xml`, `.akn`) | Normalise only: rewrite the identifying URIs to Codify's scheme, add missing eIds | No |
| FORMEX or AKN4EU XML (`.xml`, `.akn`, `.fmx`), or older acts' HTML, from the EU Publications Office, under code `eu` | Transform to AKN | No |
| Two national gazette XML formats and one international compendium's HTML, recognised from the file's content (modules in `codify/pipeline/formats/`) | Transform to AKN | No |

<details>
<summary>Which entry point reaches which lane</summary>

- `codify ingest-one` sends `.txt` and `.md` to the text lane and every other file to the PDF lane.
- `codify.pipeline.ingest_document`, which the HTTP server's upload route calls, sends XML to the deterministic lanes, and HTML only when it is a compendium instrument page or the code is `eu`. Everything else goes to the PDF lane, which needs a model; a text file sent this way fails at stage `extract`.
- From Python, `codify.pipeline.formats.pdf.ingest_text` runs the text lane.
- The HTTP server reaches the EU lane under the shipped `eu` config, by an XML upload or by `POST /runs/ingest-url` with an EUR-Lex or Publications Office address; only that route fetches, with the User-Agent in `ACQUISITION_USER_AGENT`.

</details>

## The model-assisted lane

[`codify/pipeline/formats/pdf.py`](../../codify/pipeline/formats/pdf.py) runs these stages in order; the text lane starts at Metadata. A stage that cannot continue ends the run with a `Failed` event naming it.

| Stage | What it does | Code under `codify/pipeline/` |
| --- | --- | --- |
| Extract | Reads embedded text; renders and transcribes a page with too little or unreliable text ([OCR](../guide/ocr.md)) | `enrich/ocr.py` |
| Metadata | Reads title, date, number, language | `enrich/metadata.py` |
| Regions | Classifies layout blocks, so headers and footers stay out of the body | `formats/pdf.py` |
| Structure | Finds structural markers (anchors), measures coverage, builds the Bluebell scaffold, fills it a few provisions at a time ([Structure](../guide/structure.md)) | `enrich/anchors.py`, `enrich/scaffold.py`, `enrich/structure.py` |
| Parse | Bluebell to AKN XML | `enrich/bluebell.py` |
| Enrich | Fixed passes, starting with [FRBR](frbr.md) URIs (how the document is cited) | `stages.py` and `enrich/` |
| Validate | Structural checks, one finding per problem | `enrich/validator.py` |
| Type | Loads the XML into the typed `Document` model | `codify/akn/io.py` |

<details>
<summary>Enrich passes, and when a stage fails</summary>

Enrich runs, in order: identification and FRBR URIs, metadata, placeholder marks, amendment mark-up, the enacting formula, generic containers (`hcontainer`), references, conclusions, notes, marginal notes, inline mark-up. Only inline mark-up calls a model, for definitions and references to other laws.

- Regions: a config fault ends the run at stage `regions`; any other error is logged and the step skipped.
- Enrich: a failed pass is logged and skipped, and the document completes with less mark-up. An identity that cannot be cited and an unreadable jurisdiction config raise instead; `codify ingest-one` records either in the bundle manifest as a failure at stage `raised`.

</details>

## What a run produces

The pipeline is an asynchronous generator of events ([`codify/pipeline/events.py`](../../codify/pipeline/events.py)) ending in `Complete` (the typed document and the AKN XML) or `Failed` (the stage and the error). `codify ingest-one` writes the events and their evidence to a directory called a [bundle](../reference/bundle.md). The structural validator is distinct from the AKN schema check, which only some lanes run ([two kinds of validation](akoma-ntoso.md#two-kinds-of-validation)).

## Storage

Stored law lives only in PostgreSQL, in tables that follow the FRBR distinction:

| Table | Holds |
| --- | --- |
| `jurisdictions` | Each jurisdiction code that has stored law |
| `laws` | Each work, unique on its work URI |
| `versions` | Each expression, unique on its expression URI, with the full AKN XML |
| `sections`, `provisions` | Containers and their units of text, derived from the XML, keyed by eId |
| `provision_embeddings` | A vector per provision and embedding model |

The XML in `versions` is the record; the derived rows serve reading and search and lose detail by design. A trigger refuses deletes, identity changes and unstamped XML changes on `versions` ([Database](../operate/database.md#what-the-schema-protects)).

<details>
<summary>Storing twice, and replacing a law</summary>

Storing the same document twice writes nothing: `save_document` (called by `codify load` and the HTTP server) returns the existing version with the same source hash and language, or the same expression URI. `codify.storage.supersede_document` deletes the whole law, cascading, and stores the new document under new row ids, keeping the URIs when the identity is unchanged. No earlier text is kept, so a citation to that URI now resolves to the corrected text.

</details>

[Search](../guide/search.md) fuses BM25 over `provisions` (`pg_textsearch`) with nearest-neighbour search over `provision_embeddings` (`pgvector`) in SQL.

## Module map

Paths are under [`codify/`](../../codify); smaller helpers are left out. The web app is a separate React project in [`apps/web/`](../../apps/web) that talks to the HTTP server. Two open-source Laws.Africa libraries do the AKN work: Bluebell parses the notation, Cobalt handles AKN metadata and URIs.

<details>
<summary>Packages and their responsibilities</summary>

| Package or module | Responsibility |
| --- | --- |
| `jurisdictions.py` | The jurisdiction config model and loader |
| `acquisition/` | Fetching from publishers: adapters, a rate-limited HTTP client, amendment-effect feeds |
| `pipeline/` | Format dispatch, lanes, stages, splitting a source that holds several acts |
| `akn/` | The typed AKN model, parsing and serialising, eIds, the Bluebell bridge |
| `frbr.py`, `calendar.py` | Work and expression URIs; local calendars to Gregorian dates |
| `quality/` | Corpus scanning before ingest, page-read quality, the structural grade |
| `core/` | Model client, price table, tracing, log redaction, outbound request guard |
| `embed/` | The embeddings client |
| `storage/` | Every database read and write |
| `migrations/` | The schema, as an Alembic chain |
| `search/`, `retrieve/` | Lexical tokenisation; hybrid retrieval |
| `compare/` | Provision-level comparison of two documents |
| `translate/` | Translation that keeps source structure and eIds |
| `repair/`, `adjudicate/` | Agents that propose edits for a finding, or settle a reading the scanner could not |
| `timeline.py` | Each provision's text on a date, from amendment effects |
| `lenses/` | The plugin interface for analytical layers; no detectors ship |
| `deliverable/` | Packaging laws as a zip with a manifest and quality report |
| `serve/`, `mcp/` | The HTTP server and the MCP server |
| `cli.py`, `cli_store.py` | The `codify` command |

</details>

## Status

| Capability | Status |
| --- | --- |
| Ingest a PDF or text file into a bundle | **Available**: `codify ingest-one` |
| Ingest publisher XML with no model | **Available**: HTTP upload under a shipped code, ingest by URL under `eu`, or `ingest_document`; `ingest-one` does not route XML here |
| Store a bundle or an AKN file | **Available**: `codify load` |
| Embed and search | **Available**: `codify load --embed`, uploads to `codify serve` with an embeddings endpoint, `codify search`, HTTP, MCP, the web app |
| Replace a stored law | **Library only**: `codify.storage.supersede_document` |
| Compare two documents | **Available**: `codify compare`, an MCP tool |
| Read over HTTP, MCP and the web app | **Available**: `codify serve`, `codify mcp`, `apps/web` |
| Translate | **Library only**: `codify.translate.translate.translate_document` |
| Repair a finding with an agent | **Library only**: `codify.repair.loop.repair_document`; you supply the agent run and source-page evidence |
| Point-in-time state of a law | **Library only**: `codify.timeline.state_at` |
| Package laws for bulk delivery | **Library only**: `codify.deliverable` |
| Lens detectors | **Not in the core**: the interface and registry ship; the application supplies detectors |
| Sign-in and access control | **Not in the core** |
| Runs that survive a restart | **Not in the core**: the HTTP server keeps runs in memory |
| Object storage for uploaded sources | **Not in the core**: an upload sits in a temporary directory until its run is forgotten or the server stops |

## Next

[Akoma Ntoso](akoma-ntoso.md): the standard Codify writes.
