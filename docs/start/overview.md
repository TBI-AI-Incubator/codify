---
title: What Codify is
description: What the open-source Codify core does, how its pipeline works, what is available today and where it stops.
sidebar:
  order: 1
---

You end with a picture of what the Codify core does, how it works and where it stops.

Codify is a Python package, `codify-core`, under the Apache 2.0 licence. It reads scanned gazettes, PDFs, plain text and publisher XML, writes structured, citable, machine-readable [Akoma Ntoso](../concepts/akoma-ntoso.md) XML (the OASIS standard for legal documents), and stores, searches and serves the result to people, applications and AI agents. It runs on your own machines with an OpenAI-compatible model endpoint you choose.

<details>
<summary>Who this guide is for</summary>

Ministries of justice, official gazettes, parliaments and law-reform bodies, and their engineers and legal drafters. You need a terminal and some Python; the [concepts](../concepts/akoma-ntoso.md) pages explain each Akoma Ntoso term.

</details>

## What you get

Akoma Ntoso XML, with every Part, section and paragraph of the source as an element with a type, number and text:

![Page 1 of the worked example's scan, pages/page-001.png, cropped from the masthead to subsection (1) of section 1, beside an abridged extract of final.akn.xml. Six numbered markers pair a printed line with its element. 1: the masthead, THE ATLANTIS GAZETTE, is a p in preface. 2: the line No. 7 of 1992 gives the work URI /akn/xa/act/1992/7, in FRBRuri under meta. 3: the recital ending in the enacting words, WHEREAS to AS FOLLOWS, is another p in preface. The formula in preamble, refersTo #codify, has no marker: Codify inserted it from the jurisdiction config. 4: the heading PART I, PRELIMINARY, is part with eId part_I and heading PRELIMINARY. 5: section 1 is section with eId part_I__sec_1 and num 1. 6: subsection (1) is point with eId part_I__sec_1__point_1.](../figures/v2-page-to-akn.svg "Each numbered marker pairs a line printed on page 1 with the element it became, from the worked example's real run.")

| Also in the output | Example or content |
| --- | --- |
| A [FRBR](../concepts/frbr.md) URI naming the law itself, apart from any version or file | `/akn/xa/act/1992/7`: country, document type, year, number |
| An element identifier (`eId`) per provision, built from its path | `part_I__sec_2`: section 2 inside Part I |
| A [bundle](../reference/bundle.md) of evidence per ingest | Page images, text read, markers found, coverage counts, findings, a grade |

<details>
<summary>Why each output matters</summary>

- **Akoma Ntoso XML**: software reads the law without guessing where a section ends.
- **FRBR URI**: an original and an amended text are versions of one work, so a citation keeps its meaning.
- **eId**: search results and cross-references point at it. It holds between runs while the provision keeps its place, and changes if it moves to another Part.
- **Bundle**: you can check the result against the source and show how it was made.

</details>

## How it works

![The pipeline, top to bottom. A source PDF (in the worked example, 5 scanned pages) is read page by page by a model (5 reads); a model reads the metadata (1 call: title, date, number); the jurisdiction config's rules find the anchors (26) and build a scaffold of the 26 part and section headings, bodies empty; a model fills the text (3 windows, one call each); the result is parsed from Bluebell to Akoma Ntoso and enriched (11 passes, one with a model); validation reports findings (1 finding, grade warning). The jurisdiction config (xa/config.json) feeds every stage from reading pages to enrich. Publisher XML skips every stage before validation and joins there. The output is Akoma Ntoso XML with the work URI /akn/xa/act/1992/7, stored in PostgreSQL by codify load and read through the command line, the HTTP API (codify serve), the MCP server (codify mcp) and the web app (apps/web).](../figures/v1-pipeline-summary.svg "How a scanned act becomes stored, served Akoma Ntoso: a filled square marks a stage that calls a model, and every count is from the worked example's real run.")

Rules from your jurisdiction config, not the model, find the structure and its identifiers, so citations hold between runs. On a scan the rules read the model's transcription, and the URI's year and number are the model's reading; check both ([First ingest](first-ingest.md)). [Architecture](../concepts/architecture.md) maps each stage to its module.

<details>
<summary>The design choices behind the stages</summary>

An identifier taken from model output could change between runs and break every citation built on it, so the scaffold and its identifiers come from the config's rules.

Each stage has a chapter in the guide. **The rules are data**: a new jurisdiction is a new file and a rule correction is an edit you can review, with no code change. **Evidence is kept**: the bundle records what each ingest read from each page and what it decided, so you can show the published law is the law of the source.

</details>

## What goes in

| Source | `codify ingest-one` | `codify load` | HTTP upload |
| --- | --- | --- | --- |
| Scanned PDF, or PDF with a text layer | yes, with a model | no | yes, with a model |
| Plain text (`.txt`, `.md`) | yes, with a model | no | no |
| Akoma Ntoso XML | no | yes, stored as it is | yes, without a model |

Some publisher XML formats have their own model-free routes; see [Acquire sources](../guide/acquire-sources.md).

## Status

| Status | Capabilities |
| --- | --- |
| **Available** | Configs and `scan-corpus`; vision reading of scans; `ingest-one`, HTTP upload, EU ingest by URL; checks and a grade; `load`, `serve`, `mcp`; `index-*`, `crawl-ocs` for a few named public sources; one-document export; web app (development server); `search` (embeddings endpoint); `compare` (chat and embeddings endpoints) |
| **Library only** | Publisher acquisition adapters; model-free structuring of clean text; OASIS schema check; repair; a law's state on a date; translation keeping identifiers; multi-document zip with manifest and quality report (`codify.deliverable`) |
| **Not in the core** | Model-free scan reading; review queue or sign-off; applying an amending act; sign-in, accounts, access control; an export command; PDF or Word rendering; container images |

<details>
<summary>What the labels mean</summary>

**Available**: a command, HTTP route or screen exists. **Library only**: Python code you call yourself. **Not in the core**: absent. Each guide chapter details its rows. `scan-corpus` measures how well a config reads your text; `ingest-one` and HTTP upload turn a PDF or text file into Akoma Ntoso; `load` stores laws and versions; `serve` is the HTTP API and `mcp` the MCP server for AI agents; the web app browses, reads, searches and uploads; `compare` matches two documents provision by provision. Checks and a grade run on every ingest. One-document export is Akoma Ntoso through the bundle file, the HTTP API or a web download. Search works through the CLI, HTTP, MCP and the web app; compare through the CLI and MCP. "A law's state on a date" is computed from amendment effects; "applying an amending act" means producing the amended text. No container image ships for the HTTP server, the MCP server or the web app.

</details>

## Where it stops

| Limit | What to do |
| --- | --- |
| Checks are structural; Codify does not certify law | Have legal experts review output against the source |
| A config is a starting point, not a guarantee | Measure it on your own documents first |
| Servers lack authentication; ingest runs live in memory | Run them on a trusted network |
| Alpha: in 0.x, a minor release can change output and interfaces | See [Versioning](../project/versioning.md) |

<details>
<summary>More on checks and configs</summary>

The checks do not show a text is complete, current or legally accurate. Codify ships synthetic configs for fictional jurisdictions (for examples and tests) and reference configs for real ones, written from public sources. Each records how far it has been validated.

</details>

:::note[On the hosted Platform]
Codify Platform, a hosted product built on this core, adds access control, an audit trail, analytics, lens detectors and a research agent, with hosting and support. The core runs without it. See [The hosted Platform](../open-core/platform.md).
:::

## Next

[Quickstart](quickstart.md): take a short act from text to a served, structured law without a model.
