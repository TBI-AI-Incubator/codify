---
title: First ingest
description: Run one scanned act through the full pipeline with a model, then read the bundle it writes to judge the result against the source.
sidebar:
  order: 4
---

You end with one scanned act ingested into [Akoma Ntoso](../concepts/akoma-ntoso.md) XML, the evidence for each stage, and a grade.

## Before you start

- A source checkout with Poppler, installed as in [Install](install.md).
- A chat model that accepts images, behind an OpenAI-compatible endpoint. The sample is a scan with no text layer.
- For the last step only, the PostgreSQL service from [Install](install.md#start-the-database).

The source is `data/fixtures/synthetic/xa/legislation-act-1992.pdf`, the Legislation Act, 1992 of the fictional Atlantis (`xa`): five scanned gazette pages, six Parts and twenty sections.

## The idea in brief

`ingest-one` runs one document through seven stages ([Architecture](../concepts/architecture.md#the-model-assisted-lane)) and writes a directory called a bundle. A publisher must show that the published text is the source's, so the bundle keeps each intermediate result beside the page images, separating transcription faults from structuring faults.

<details>
<summary>The seven stages</summary>

| Stage | What it does | Model |
| --- | --- | --- |
| Extract | Reads each page. A usable text layer is kept; any other page is rendered to an image for the model. | for scanned pages |
| Metadata | Reads the title, date and number. | yes |
| Structure | Scans for the markers your jurisdiction config declares, builds a scaffold, then has the model fill each provision with its text. | yes |
| Parse | Parses the filled scaffold, written in [Bluebell](../concepts/bluebell.md), into XML. | no |
| Enrich | Adds identity metadata, the enacting formula, references, defined terms and other mark-up, one pass at a time. | for some passes |
| Validate | Runs structural checks and emits findings. | no |
| Type | Reads the XML back into a typed `Document`; XML that will not load fails the run. | no |

</details>

## Steps

### Point Codify at your model

Set all three variables in `.env` at the repository root before you continue.

```ini
LLM_BASE_URL=https://your-endpoint.example/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model-name
```

`.env.example` shows one public provider's values; any OpenAI-compatible endpoint works. Use the model id your endpoint expects; the manifest's `model` field records it.

Without `LLM_BASE_URL`, `ingest-one` stops before it touches the output folder, with exit status 2:

```text
no chat endpoint: set LLM_BASE_URL (and LLM_API_KEY) to an OpenAI-compatible endpoint, in the environment or the nearest .env
```

### Run the ingest

```bash
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
```

Progress goes to standard error. This trimmed output is from a recorded run through an OpenAI-compatible endpoint other than the one in `.env.example`.

```text
2026-11-14 00:15:03 [error    ] layout_pass_failed             ... error_type=OcrNotConfigured ...
2026-11-14 00:15:03 [info     ] page_diverted_to_ocr           page=1 reason=too_short split_words=None total=5
...
2026-11-14 00:15:19 [info     ] page_vision_ocr_done           chars=2237 finish=stop page=1
...
  PageExtracted
  ...
2026-11-14 00:15:31 [info     ] metadata_extracted             additional_languages=[] calendar=gregorian date=1992-05-02 language=eng number=7 title='Legislation Act, 1992'
  MetadataExtracted
2026-11-14 00:15:32 [info     ] container_heading_captured     heading=PRELIMINARY kind=part line=13 number=I
...
  AnchorsDetected
  StructureProgress
  StructureProgress
  StructureProgress
  Structured
  Parsed
...
  Enriched
  ...
2026-11-14 00:16:14 [info     ] validation_issues_found        count=1
  ValidationIssued
  Enriched
  Complete
```

The first line is expected: with no second OCR engine to cross-check, each page is read once ([OCR](../guide/ocr.md)). `reason=too_short` means the text layer was too short to trust, so the page went to the model as an image.

The manifest goes to standard output and to `bundle/manifest.json`. Model output varies between runs, so your counts and findings can differ.

```json
{
  "source": "data/fixtures/synthetic/xa/legislation-act-1992.pdf",
  "source_bytes": 1200079,
  "lane": "pdf",
  "jurisdiction": "xa",
  "metadata": {
    "title": "Legislation Act, 1992",
    "doctype": null,
    "year": "1992",
    "number": "7",
    "date": "1992-05-02"
  },
  "jurisdiction_config": {
    "code": "xa",
    "path": "data/jurisdictions/xa/config.json",
    "sha256": "f6941da7c52c53cfac95fdf17f62d151bb0e1297bedda46720ca947ff9ab76de"
  },
  "model": "...",
  ...
  "pages": 5,
  "pages_diverted_to_ocr": {
    "1": "too_short",
    ...
  },
  "pages_read_once": 5,
  "pages_unreadable": {},
  "anchors": 26,
  "anchors_by_pass": {
    "regex": 6,
    "marginal_note_units": 20
  },
  "scaffold_built": true,
  "fallback": null,
  "coverage": {
    "kind": "section",
    "state": "measured",
    "ratio": 1.0,
    ...
  },
  "ambiguity": 2,
  "ambiguity_by_kind": {
    "duplicate_number": 2
  },
  "ambiguity_blocking": 0,
  "validator_findings": 1,
  "grade": {
    "grade": "warning",
    "reason": "missing_container_title: part II has no descriptive heading; the reader shows a bare marker",
    "rubric_version": 1
  },
  "akn_bytes": 32254,
  "segmentation_failed": null,
  "failed": null
}
```

### Read the manifest first

Check `failed`, `grade`, `pages_unreadable` and `coverage.ratio` first ([field definitions](../reference/bundle.md#manifestjson)). `ungraded` means the validator did not finish, so an empty findings list proves nothing. The exit code is 1 when a stage failed, 2 with no jurisdiction config, otherwise 0, so read `grade` even on exit 0.

### Read the bundle against the source

Read in this order ([every file](../reference/bundle.md)):

1. **`source.txt` beside `pages/page-NNN.png`.** If the text is wrong, nothing later can be right.
2. **`anchors.jsonl` and `scaffold.bluebell`.** The model fills only anchored provisions; a section missing here is missing from the XML.
3. **`validator.json`.** Each finding names a check, a severity and the element.
4. **`final.akn.xml`.** The document.

<details>
<summary>The other files</summary>

`anchors.jsonl` gives each marker's `eId`, the scan pass that found it and its position (`char_offset`). `ambiguity.jsonl` holds spans where the scan had to choose, with its choice; `coverage.json` the expected and captured numbers; `events.jsonl` every pipeline event; `page_spans.json` where each page sits in `source.txt`; `segmentation.json` which acts the source holds.

</details>

The recorded run found every section. Its scaffold begins:

```text
BODY
  PART I - PRELIMINARY

    SECTION 1 - Short title and commencement

    SECTION 2 - Interpretation
...
```

and page 1 reads against the XML like this:

![Page 1 of the worked example's scan, pages/page-001.png, cropped from the masthead to subsection (1) of section 1, beside an abridged extract of final.akn.xml. Six numbered markers pair a printed line with its element. 1: the masthead, THE ATLANTIS GAZETTE, is a p in preface. 2: the line No. 7 of 1992 gives the work URI /akn/xa/act/1992/7, in FRBRuri under meta. 3: the recital ending in the enacting words, WHEREAS to AS FOLLOWS, is another p in preface. The formula in preamble, refersTo #codify, has no marker: Codify inserted it from the jurisdiction config. 4: the heading PART I, PRELIMINARY, is part with eId part_I and heading PRELIMINARY. 5: section 1 is section with eId part_I__sec_1 and num 1. 6: subsection (1) is point with eId part_I__sec_1__point_1.](../figures/v2-page-to-akn.svg "Each numbered marker pairs a line printed on page 1 with the element it became, from the worked example's real run.")

Subsection `(1)` (marker 6), like `(2)` and `(3)` after it, came out as `point`, an Akoma Ntoso list item, where the `xa` config declares `subsection`; no check reports this. The `FRBRuri` (marker 2) gives the identity `/akn/xa/act/1992/7`: country, document type, year, number ([FRBR](../concepts/frbr.md)).

### Store it

```bash
uv run codify load bundle/
```

```json
{"version_id": "20cb9d70-142f-47fc-993b-98d884a79ad8", "work_uri": "/akn/xa/act/1992/7", "embedded": 0}
```

`load` reads the jurisdiction and title from the manifest, preferring the preface's long title. `--embed` makes the provisions searchable and needs an embeddings endpoint ([Search](../guide/search.md)). To read the law over HTTP, see the [Quickstart](quickstart.md#serve-it).

## Check your result

A grade summarises the structural checks, not whether the law is right. The recorded run's one finding, in `validator.json`:

```json
[
  {
    "check": "missing_container_title",
    "severity": "warning",
    "eid": "part_II",
    "container": "part",
    "message": "part II has no descriptive heading; the reader shows a bare marker"
  }
]
```

All twenty sections are present with their text intact, but the Parts are not where the source prints them:

![Two columns from the worked example's run. On the left, as printed in source.txt: line 15, PART I — PRELIMINARY, sections 1 to 3; line 30, a cross-reference in section 2, "as published under Part II;", dropped; line 48, the printed heading PART II — PUBLICATION OF LEGISLATION, sections 4 to 8, dropped; line 83, PART III — THE PUBLISHING AUTHORITY, sections 9 to 11; line 93, a cross-reference in section 10, "with Part II, to keep the Register", kept. On the right, as recorded in scaffold.bluebell: PART I - PRELIMINARY with sections 1 to 8; PART III - THE PUBLISHING AUTHORITY with sections 9 and 10; then PART II, made from the kept cross-reference, with section 11 and no heading, which the validator reports as the warning missing_container_title. Parts IV to VI match the source.](../figures/v9-part-ii-hits.svg "From the worked example's real run: the scan kept only the last of three mentions of Part II, a cross-reference inside section 10, so the recorded Parts no longer match the printed ones.")

The `eId`s, built from the path, are wrong too: `part_I__sec_4` to `part_I__sec_8` and `part_II__sec_11`. A link built from the source's arrangement, such as `part_II__sec_4`, finds nothing. [Structure a document](../guide/structure.md#check-your-result) traces the cause and fixes it in the config.

<details>
<summary>What the scan did</summary>

The scan took the repeated number for a table-of-contents entry and its twins and kept the last. The words after it do not look like a heading, so the Part got none:

```text
[info     ] container_heading_rejected     candidate=', to keep the Register, and to do all things necessary or convenient to be done ' kind=part line=93 number=II reason=implausible
```

</details>

Coverage counts sections, so it is 1.0, and no check covers the order of Parts. Compare the Parts and Chapters in `scaffold.bluebell` with the source, and follow every `warning` back to it.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `no config for jurisdiction 'zz': looked in .../data/jurisdictions/zz/config.json`, exit code 2 | No config for that code; nothing is written. | Check the code, or write the config. |
| Exit code 1, `"failed": {"stage": "extract", "error": "APIConnectionError: Connection error."}`, grade `ungraded`, no `final.akn.xml` | The endpoint did not answer while reading a scanned page. | Check `LLM_BASE_URL` and your network, then rerun. |
| Exit code 0, `"failed": null`, grade `blocking` with `structure_halted: body_fill_failed on section: the model wrote no body ...`, on a text source | The endpoint did not answer. Text needs no page reading, so each provision got the source text copied in, and unread metadata left a placeholder URI such as `/akn/xa/act/0001/draft-...` (`uncitable_work_uri`). | Run again when the endpoint answers. Do not publish the result. |
| `"failed": {"stage": "extract", "error": "PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?"}` | Poppler is missing. | Install it ([Install](install.md)). |
| A page listed in `pages_unreadable`; grade `blocking` | The model returned no text for the page, for example after a content-filter refusal. | Set `LLM_CONTENT_FILTER_FALLBACK_MODEL` or `--fallback-model` to a second model, tried when the first refuses. |
| Sections missing from `anchors.jsonl` | The config's markers do not match this document. | See [Structure](../guide/structure.md) and [Write a jurisdiction config](../guide/write-a-jurisdiction-config.md). |

## Status and limits

| Capability | Status |
| --- | --- |
| `ingest-one` on one PDF, `.txt` or `.md` | **Available**. Any other file is read as a PDF. A rerun into the same directory replaces the first run's files. |
| Ingest a folder with one command | **Not in the core**. Run `ingest-one` per file, or queue uploads on the [HTTP server](../guide/serve-and-publish.md). |
| Store existing Akoma Ntoso XML | **Available** through `load` and HTTP upload. |
| Read scans without a model | **Not in the core** |
| `load` a bundle | **Available**. It stores the document, not the findings or grade; keep the bundle. |
| Repair a finding | **Library only** ([Validate and repair](../guide/validate-and-repair.md)) |

## Next

[Model your legal system](../guide/model-your-legal-system.md): describe your own document types, hierarchy and numbering.
