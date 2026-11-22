---
title: Bundle files
description: Every file codify ingest-one writes to its output directory, when each is written, and what each field means.
sidebar:
  order: 3
---

`codify ingest-one` runs one document through the pipeline and writes a bundle: the evidence behind each stage's counts, so diagnose an ingest here. Most files are captured during the run; after it, `source.txt` and `page_spans.json` are built from the page results (text lane: from the input), `segmentation.json` by running the segmenter on them, and the page images by rendering the PDF. Source: [`codify/cli.py`](../../codify/cli.py).

Examples come from `data/fixtures/synthetic/xa/legislation-act-1992.pdf`, ingested with a model; [Structure a document](../guide/structure.md) reads them.

## The run and the directory

```bash
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
```

- No config for the jurisdiction: prints `no config for jurisdiction '<code>': looked in <path>` to stderr, exits 2, writes nothing.
- Otherwise it creates the directory, then deletes the files it writes (those below, and `pages/page-*.png`) and leaves others alone, so a reused directory never mixes two runs.
- `.txt` or `.md` input takes the text lane; anything else is a PDF.
- The manifest goes to stdout; progress (one event name per line) and logs to stderr. `--quiet` drops the event names.
- Exit status is 1 when the manifest records a failure, otherwise 0. A document landed with blocking findings exits 0; read its `grade`.

## Files

![Each pipeline stage with the bundle files it leaves, from the worked example's run: Read pages leaves pages/page-001.png onwards, source.txt, page_spans.json and segmentation.json; Read metadata reads title, date and number, which the run summary records; Find anchors (26 anchors) leaves anchors.jsonl and ambiguity.jsonl; Build scaffold leaves scaffold.bluebell, the 26 part and section headings with bodies empty; Fill text (3 windows) leaves coverage.json, which also carries the anchor and page measurements; Parse and Enrich (11 passes) leave no file of their own, though events.jsonl records each; Validate (1 finding, grade warning) leaves validator.json; the Akoma Ntoso XML, /akn/xa/act/1992/7, is final.akn.xml, written with manifest.json and events.jsonl, which cover the whole run. Stages that call a model are marked: Read pages, Read metadata, Fill text and Enrich.](../figures/v1-pipeline.svg "Which stage leaves which file: start from the stage you doubt and open its file; counts are from the worked example's real run.")

| File | Written | Contents |
| --- | --- | --- |
| `manifest.json` | Always | Run summary: source, config, models, page reads, scan counts, coverage, grade, failure. |
| `source.txt` | Always | The text the pipeline read: PDF page texts joined by a blank line, or the text-lane input unchanged, newlines kept. Offsets elsewhere index these characters. |
| `page_spans.json` | Always | Each page's place in `source.txt`, and the running headers and footers lifted off it. |
| `segmentation.json` | Unless the segmenter raised | Which acts the source holds, under the config's segmentation rules. |
| `events.jsonl` | Always | Every pipeline event. |
| `validator.json` | Always | The findings, as a list. `[]` means none reported, not that the validator ran; check the grade. |
| `anchors.jsonl` | Always | Every anchor the scan kept; empty if none or the scan did not run. |
| `coverage.json` | Always | The coverage measurement, or `null`. |
| `ambiguity.jsonl` | Always | Every place the scan chose between two readings. |
| `scaffold.bluebell` | When a scaffold was built | The Bluebell scaffold before body-fill. |
| `final.akn.xml` | When the run completed | The Akoma Ntoso document. |
| `pages/page-NNN.png` | PDF lane, with Poppler installed | Each page at 200 DPI, from `page-001.png`. A render failure logs `page_render_failed` and the rest is still written. |

![Section 1 of the worked example, Short title and commencement, in four bundle files. In source.txt, line 17 is the marginal note Short title and commencement, line 18 the number 1 alone, and line 19 begins (1) This Act may be cited as the Legislation Act, 1992. Find anchors, by rule from the config, records it as line 2 of anchors.jsonl: kind section, keyword Short title and commencement, number 1, char_offset 959, line 17, parent_eid part_I, akn_eid part_I__sec_1, source_pass marginal_note_units; the eId is set here. Build scaffold writes a header line per anchor with no body text, and keeps the eId in memory beside each header rather than in the file: scaffold.bluebell lines 11 to 15 read PART I - PRELIMINARY, SECTION 1 - Short title and commencement and SECTION 2 - Interpretation, with blank lines where the bodies go; a note on the SECTION 1 line reads part_I__sec_1, held in memory with this header, not in the file. Fill text calls a model, which returns the lines for each eId, and the parse gives final.akn.xml from line 47: a section with eId part_I__sec_1, num 1, heading Short title and commencement, and a point with eId part_I__sec_1__point_1, num (1), whose paragraph part_I__sec_1__point_1__p_1 holds the text the model filled: This Act may be cited as the Legislation Act, 1992.](../figures/v4-four-panels.svg "To follow one provision across the files, take its line in source.txt to the anchor with that line, then search scaffold.bluebell for the anchor's heading and final.akn.xml for its eId; from the worked example's real run.")

## manifest.json

| Field | Meaning |
| --- | --- |
| `source`, `source_bytes` | The path given and its size in bytes. |
| `lane` | `pdf` or `text`. |
| `jurisdiction` | The config code resolved; case may differ from the argument. |
| `metadata` | `title`, `doctype`, `year`, `number` and `date` as the model read them, all `null` if extraction failed. `codify load` takes its default title from here. |
| `jurisdiction_config` | `code`, `path` and `sha256` of the config used, to tell apart runs against a changed config. |
| `model`, `ocr_model`, `fallback_model` | From `--model`, `--ocr-model` and `--fallback-model`. `ocr_model` is `null` on the text lane and when no transcription engine was named (the vision route on the body-fill model). |
| `elapsed_seconds` | Wall-clock time of the run. |
| `pages` | Page images written. `null` on the text lane; `0` if rendering failed. |
| `pages_diverted_to_ocr` | Page number to why its text layer was not trusted ([Read scanned pages](../guide/ocr.md) lists the reasons). This and the next two are `null` on the text lane. |
| `pages_read_once` | Pages with no second engine read to compare against. |
| `pages_unreadable` | Page number to why it came back with no text. That content is missing; the validator reports `unreadable_page`. |
| `anchors`, `anchors_by_pass` | Anchors kept, and how many each pass produced. |
| `scaffold_built` | Whether `scaffold.bluebell` was written. |
| `fallback` | `null` on the normal path. Otherwise the route the structurer took: `verbatim_section` (no anchors, all text in one section), `empty` (no anchors, no text), or a gate that refused the document: `coverage_gate`, `invariant_gate` or `source_truncation`. |
| `coverage` | The same object as `coverage.json`. |
| `ambiguity`, `ambiguity_by_kind`, `ambiguity_blocking` | Ambiguity spans: total, per kind, and unresolved spans of a blocking kind. |
| `validator_findings` | Entries in `validator.json`. |
| `grade` | `grade` (`clean`, `warning`, `blocking` or `ungraded`), `reason` (first finding at the deciding severity) and `rubric_version`. `ungraded` means the validator did not finish; a structure halt still grades `blocking`. See [Validation checks](validation-checks.md#severity-and-grade). |
| `akn_bytes` | Length of the final document; `0` if none. |
| `segmentation_failed` | The segmenter's error, which explains a missing `segmentation.json`; otherwise `null`. |
| `failed` | `null`, or `stage` and `error` of the failed stage. Stage `raised` means an exception escaped the pipeline. |

The worked example's grade:

```json
"validator_findings": 1,
"grade": {
  "grade": "warning",
  "reason": "missing_container_title: part II has no descriptive heading; the reader shows a bare marker",
  "rubric_version": 1
},
...
"failed": null
```

## page_spans.json

```json
{
  "page_count": 5,
  "pages": [
    {
      "page": 1,
      "method": "vision_ocr",
      "start": 0,
      "end": 2237
    },
    ...
  ],
  "furniture": []
}
```

| Field | Meaning |
| --- | --- |
| `page_count` | Pages in the source, including any with no text. `0` on the text lane. |
| `pages` | Per page with body text: number, reading method (such as `text_extraction` or `vision_ocr`), and `[start, end)` range in `source.txt`, spans separated by two newlines. |
| `furniture` | Per page, the `header` and `footer` lines removed from the body text. |

`codify segment` refuses a file whose spans do not tile `source.txt`.

## segmentation.json

```json
{
  "jurisdiction_config": { "code": "xa", ... },
  "outcome": "single",
  "front_matter": null,
  "segments": [
    { "start": 0, "end": 10990, "first_page": 1, "last_page": 5, "heading": "", "key": "", "signals": [] }
  ],
  "held": [],
  "reconciliation": []
}
```

| Field | Meaning |
| --- | --- |
| `outcome` | `single` when the config declares no segmentation rules (as for `xa`); `decided` when every boundary was settled; `abstained` when a region was held. |
| `front_matter` | `[start, end)` of a masthead and contents before the first act, or `null`. |
| `segments` | Each act: range in `source.txt`, first and last page, heading, key, and the signals that agreed on its boundary. |
| `held` | Unsettled regions, with a `reason`; never folded into a neighbour. |
| `reconciliation` | Each contents entry and heading compared, with `status` and a readable `reads` line. |

Ingest structures the whole source as one document whatever this file says. `codify segment` rewrites it under the current config, with no model:

```bash
uv run codify segment bundle/
```

```text
single: 1 acts, 0 held
```

## anchors.jsonl

```json
{"kind": "part", "keyword": "PART", "number": "I", "char_offset": 935, "line": 13, "matched_text": "PART I", "depth": 0, "parent_eid": null, "akn_eid": "part_I", "akn_wid": "part_I", "quoted_amendment": false, "amendment_item": false, "amended_article": false, "heading": "PRELIMINARY", "source_pass": "regex"}
```

| Field | Meaning |
| --- | --- |
| `kind` | The Akoma Ntoso element the anchor opens, such as `part` or `section`. |
| `keyword`, `number` | The marker word as matched (the note, for a marginal-note unit) and the number. |
| `char_offset`, `line` | Start offset and line in the text the structurer read (`source.txt` for Latin-script text without layout regions). |
| `matched_text` | The matched text, cut to 120 characters. |
| `depth`, `parent_eid` | Nesting depth and the enclosing anchor's eId. |
| `akn_eid`, `akn_wid` | The eId (built from the element's place in the hierarchy) and wId (kept from the original version). See [identifiers](../concepts/akoma-ntoso.md#identifiers-eid-and-wid). |
| `quoted_amendment` | Inside text an amendment quotes: it belongs to the amended act, not this document's hierarchy. |
| `amendment_item`, `amended_article` | An amendment item that introduces another act's article, and that article. |
| `heading` | A container's heading or a section's marginal note; `null` if none. |
| `source_pass` | The scan pass that produced the anchor. |

## coverage.json

[Structure a document](../guide/structure.md#steps) shows the worked example's values.

| Field | Meaning |
| --- | --- |
| `kind` | The basic unit measured: the provision the config numbers through the document, such as `section` or `article`. |
| `state` | `measured`; `captured_unmeasured` (units found, but no markers in the text to count them against); or `empty` (neither). |
| `anchor_ratio` | Captured basic-unit numbers over the numbers the text shows. `null` unless `state` is `measured`. |
| `captured`, `expected`, `missing` | The number sets behind `anchor_ratio`, as sorted strings. |
| `masked`, `unclosed` | Markers hidden by unclosed quotation marks, and how many such quotes. Non-zero `masked` makes `anchor_ratio` untrustworthy. |
| `container` | `present` (grouping headings counted in the text), `found` (those that produced a container) and `grouping_declared` (whether the class declares a grouping level). A heading starting with its keyword counts only when a digit or Arabic-script ordinal word follows, so Roman-numbered headings such as `PART I` read `0`. `codify scan-corpus` measures each grouping kind as `container_recall`. |
| `body_fill` | `windows`, `calls`, `calls_failed`, `expected` (units with body text in the source), `verbatim` (eIds whose body is copied source text), `empty` (eIds with no body), `model_filled` and `ratio`. Absent when body-fill did not run. |
| `pages` | PDF lane only: `total`, the `unreadable` page numbers, and the share read. |
| `ratio` | The lowest of `anchor_ratio`, `body_fill.ratio` and `pages.ratio`, so a good measurement cannot hide a bad one. |

A three-section synthetic text (not shipped), model unreachable: the scan was complete, no body was written, so `ratio` is 0.

```json
{"kind": "section", "state": "measured", "ratio": 0.0, "anchor_ratio": 1.0, ..., "body_fill": {"windows": 1, "calls": 6, "calls_failed": 6, "expected": 3, "verbatim": ["part_I__sec_1", "part_I__sec_2", "part_II__sec_4"], "empty": [], "model_filled": 0, "ratio": 0.0}}
```

## ambiguity.jsonl

```json
{"kind": "duplicate_number", "start": 3485, "end": 3492, "eid": "", "emitted_by": "drop_toc_duplicates", "resolved": true, "detail": {"kind": "part", "number": "II", "reads_as": "toc_twin", "kept_at": 6593, "container": []}}
```

| Field | Meaning |
| --- | --- |
| `kind` | One of `duplicate_number`, `toc_without_body`, `numbering_gap`, `orphan_text`, `unmatched_marker`, `untwinned_tail`, `adoption_suppressed`, `tail_excluded`, `act_boundary_suspected`. An unresolved `duplicate_number` or `act_boundary_suspected` span is blocking: `ingest-one` lands the document with a `structure_halted` finding. Other kinds are informational. |
| `start`, `end` | The span in the text the scan was given. Right-to-left text is normalised first, so offsets may not match `page_spans.json`. |
| `eid` | The element concerned, if any. |
| `emitted_by` | The scan pass that made the choice. |
| `resolved` | `true` when the pass applied a defensible rule. |
| `detail` | The evidence, which varies by pass. |

## events.jsonl

One event per line, with a `kind` field:

| `kind` | Fields |
| --- | --- |
| `page_extracted` | `page`, `method` (`text` or `ocr`), `text_len`, `divert_reason`, `finish_reason`, `unreadable`, `skipped` |
| `metadata_extracted` | `metadata`: everything the model read, including fields the manifest leaves out |
| `anchors_detected` | `summary` (anchors per kind), `total` |
| `structure_progress` | `fraction`, and `label` such as `window 1/3`. Retries of empty units keep counting, so the label can pass the total (`window 10/3`) while `fraction` stays 1.0. |
| `structured` | `bluebell_len` |
| `parsed` | `akn_xml_len` |
| `enriched` | `pass_name`: `cobalt`, `akn_meta`, `placeholders`, `amendments`, `enacting`, `hcontainers`, `references`, `conclusions`, `notes`, `asides`, `inline_markup`, then `validator` when validation finished |
| `validation_issued` | `issue`, one finding |
| `complete` | `document`, the parsed document (FRBR identifiers, language, dates, body, attachments, recitals, textual modifications, lifecycle events), and `akn_xml`, a second copy of the final document |
| `failed` | `stage`, `error` |

## validator.json

Findings in the order produced, structure halts first. Each has `check`, `severity` and `message`; [Validation checks](validation-checks.md) gives the rest.

## Status

| Capability | Status |
| --- | --- |
| Write a bundle for one document | **Available**: `codify ingest-one` |
| Rewrite `segmentation.json` from a bundle | **Available**: `codify segment` |
| Store a bundle's document in PostgreSQL | **Available**: `codify load <bundle>` stores `final.akn.xml` with defaults from the manifest; findings and grade are not stored |
| Write a bundle for a run started over HTTP | **Not in the core**: server runs stream events and store the document, with no bundle |
