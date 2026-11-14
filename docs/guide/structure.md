---
title: Structure a document
description: Turn extracted text into Akoma Ntoso with a rule-based anchor scan and a model that fills each provision, then read the bundle to check the result.
sidebar:
  order: 5
---

You end with the worked example structured into `final.akn.xml`, checked against the printed Act, and a config fix for the one defect the check finds.

## Before you start

- A jurisdiction config, such as the synthetic `xa` used here ([Write a jurisdiction config](write-a-jurisdiction-config.md)).
- A chat model behind an OpenAI-compatible endpoint, as in [First ingest](../start/first-ingest.md). A scanned source also needs a model that reads images ([Read scanned pages](ocr.md)).
- Poppler, to render the bundle's page images.

## The idea in brief

Structuring decides where each provision starts, which container holds it, and its eId, such as `part_I__sec_3` ([Akoma Ntoso](../concepts/akoma-ntoso.md)). Citations, amendments, translations and search address provisions by eId, so it must be the same on every run. So the hierarchy never passes through the model: Codify derives it by rule from the config, and the model only transcribes text under fixed markers.

1. **Anchor scan.** A regular expression built from the config's hierarchy terms finds each structural marker (anchor). Later passes add markers with no keyword, drop contents-page entries and cross-references, and record each choice between two readings as an ambiguity span.
2. **Gates.** If the scan captures fewer than the config's `min_anchor_coverage` (default 0.80) of the basic units in the text (the article, section or paragraph numbered through the document), the document lands for repair with a blocking `coverage_below_floor` finding; the run fails only when markers are present and none is captured.
3. **Scaffold.** Anchors become a [Bluebell](../concepts/bluebell.md) scaffold: one header line per anchor, no body text.
4. **Body-fill.** The model returns the lines under each eId, in windows of consecutive basic units.

<details><summary>How body-fill handles bad replies</summary>

A body-fill reply naming an eId outside its window is discarded. An empty unit is retried in windows of two units, then one, then gets its source text copied in unchanged.

</details>

Bluebell parses the scaffold into Akoma Ntoso, enrichment adds metadata and inline mark-up, and the validator runs ([Architecture](../concepts/architecture.md)).

![Section 1 of the worked example, Short title and commencement, in four bundle files. In source.txt, line 17 is the marginal note Short title and commencement, line 18 the number 1 alone, and line 19 begins (1) This Act may be cited as the Legislation Act, 1992. Find anchors, by rule from the config, records it as line 2 of anchors.jsonl: kind section, keyword Short title and commencement, number 1, char_offset 959, line 17, parent_eid part_I, akn_eid part_I__sec_1, source_pass marginal_note_units; the eId is set here. Build scaffold writes a header line per anchor with no body text, and keeps the eId in memory beside each header rather than in the file: scaffold.bluebell lines 11 to 15 read PART I - PRELIMINARY, SECTION 1 - Short title and commencement and SECTION 2 - Interpretation, with blank lines where the bodies go; a note on the SECTION 1 line reads part_I__sec_1, held in memory with this header, not in the file. Fill text calls a model, which returns the lines for each eId, and the parse gives final.akn.xml from line 47: a section with eId part_I__sec_1, num 1, heading Short title and commencement, and a point with eId part_I__sec_1__point_1, num (1), whose paragraph part_I__sec_1__point_1__p_1 holds the text the model filled: This Act may be cited as the Legislation Act, 1992.](../figures/v4-four-panels.svg "One section of the worked example's real run in four bundle files: the eId is set when the anchor is found, held in memory beside the scaffold, and written into the XML.")

## Steps

:::tip
For a text file or a PDF with a text layer, run `codify scan-corpus <directory> --jurisdiction <code>` and fix the config from `anchors.jsonl` and `ambiguity.jsonl` before any model call. The worked example has no text layer, so it finds nothing and exits 1.
:::

1. Run the pipeline. This calls the model.

   ```bash
   uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
   ```

   Events print to stderr among the log lines:

   ```text
     PageExtracted
     ...
     MetadataExtracted
     AnchorsDetected
     StructureProgress
     ...
     Complete
   ```

   The manifest goes to stdout and `bundle/manifest.json`; the [bundle reference](../reference/bundle.md) describes every file.

2. Read the scan in the manifest.

   ```json
   {
     ...
     "anchors": 26,
     "anchors_by_pass": {
       "regex": 6,
       "marginal_note_units": 20
     },
     "scaffold_built": true,
     "fallback": null,
     ...
   }
   ```

   The keyword pass found the six Parts and `marginal_note_units` the twenty sections. `anchors.jsonl` holds one anchor per line.

   <details><summary>About the passes</summary>

   `marginal_note_units` finds a number alone under a marginal note, and runs because `xa` sets `display.heading_type` to `marginal_note`. Each line of `anchors.jsonl` gives kind, number, offset, eId and pass.

   </details>

3. Read `coverage.json`. Its `ratio` is the lowest of the anchor, body-fill and page measurements.

   ```json
   {
     "kind": "section",
     "state": "measured",
     "ratio": 1.0,
     "anchor_ratio": 1.0,
     ...
     "container": {
       "present": 0,
       "found": 0,
       "grouping_declared": true
     },
     "body_fill": {
       ...
       "model_filled": 20,
       "ratio": 1.0
     },
     "pages": {
       "total": 5,
       "unreadable": [],
       "ratio": 1.0
     }
   }
   ```

   <details><summary>Why the lowest, and why <code>container</code> reads 0</summary>

   Taking the lowest means one good measurement cannot hide a bad one. The `container` probe counts a heading only when a digit or an Arabic-script ordinal word follows the keyword, so it misses `PART I`. `scan-corpus`, below, counts Parts.

   </details>

4. Compare `scaffold.bluebell` with the printed Act, then open `final.akn.xml`.

   ```xml
   <part eId="part_I">
     <num>I</num>
     <heading>PRELIMINARY</heading>
     <section eId="part_I__sec_1">
       <num>1</num>
       <heading>Short title and commencement</heading>
       <point eId="part_I__sec_1__point_1">
         <num>(1)</num>
         <content>
           <p eId="part_I__sec_1__point_1__p_1">This Act may be cited as the Legislation Act, 1992.</p>
         </content>
       </point>
       ...
   ```

   <details><summary>Why subsection <code>(1)</code> is a <code>point</code></summary>

   The config declares no `marker_form` for a bare `(1)`, so the scan does not anchor it and scaffold assembly nests enumerated body lines as points.

   </details>

## Check your result

Coverage 1.0 means every section number was captured; it does not check containers. This scaffold has the Parts out of order:

![Two columns from the worked example's run. On the left, as printed in source.txt: line 15, PART I — PRELIMINARY, sections 1 to 3; line 30, a cross-reference in section 2, "as published under Part II;", dropped; line 48, the printed heading PART II — PUBLICATION OF LEGISLATION, sections 4 to 8, dropped; line 83, PART III — THE PUBLISHING AUTHORITY, sections 9 to 11; line 93, a cross-reference in section 10, "with Part II, to keep the Register", kept. On the right, as recorded in scaffold.bluebell: PART I - PRELIMINARY with sections 1 to 8; PART III - THE PUBLISHING AUTHORITY with sections 9 and 10; then PART II, made from the kept cross-reference, with section 11 and no heading, which the validator reports as the warning missing_container_title. Parts IV to VI match the source.](../figures/v9-part-ii-hits.svg "From the worked example's real run: the scan kept only the last of three mentions of Part II, a cross-reference inside section 10, so the recorded Parts no longer match the printed ones.")

The validator reports one warning, `missing_container_title` on `part_II` (grade `warning`). In `ambiguity.jsonl`, the kept mention is at 6593; the scan wrote a span for each it dropped, at 2228 and 3485:

```json
{"kind": "duplicate_number", "start": 3485, "end": 3492, "eid": "", "emitted_by": "drop_toc_duplicates", "resolved": true, "detail": {"kind": "part", "number": "II", "reads_as": "toc_twin", "kept_at": 6593, "container": []}}
```

<details><summary>Tracing the three offsets in <code>source.txt</code></summary>

2228, 3485 and 6593 are the figure's lines 30, 48 and 93, counted as lines of `source.txt`, not the anchors' `line` field. Search by the words; the offsets fall up to 17 characters before them. "PUBLICATION OF LEGISLATION" is missing from `final.akn.xml`.

</details>

The fix is in the config. `structuring.marker_boundary` defaults to `relaxed`, which accepts a marker after any white space, even mid-sentence; `line_anchored` accepts one only at line start, after a tab, or after three or more spaces. Add the key inside the existing `structuring` object; a second `"structuring"` key would replace the first:

```json
"structuring": {
  ...
  "marker_boundary": "line_anchored"
}
```

`xa` is a shipped fixture, so copy the whole `data/` directory, edit the copy, and set `CODIFY_DATA_ROOT` to its absolute path. Scan the bundle's text, with no model:

```bash
uv run codify scan-corpus bundle/ --jurisdiction xa
```

`container_recall` (heading lines in the text and anchors made from them, per grouping kind) moves from `"part": [6, 5]` to `[6, 6]`, and `spans_by_reason` from `{"toc_twin": 2}` to `{}`.

Rebuild from the bundle's text, skipping transcription (the model still runs metadata and body-fill):

```bash
uv run codify ingest-one bundle/source.txt --jurisdiction xa --out bundle-fixed/
```

The scaffold now matches the printed Act: `PART II - PUBLICATION OF LEGISLATION` holds sections 4 to 8, and section 11 sits under Part III.

<details><summary>How the text lane rebuilds</summary>

The text lane runs the PDF lane's stages after extraction. The scaffold is built before body-fill, so it is written even if the model is unreachable.

</details>

## What can go wrong

![From structure gate to grade, top to bottom. Structure gates: a gate that fires lands the document and adds a structure_halted finding of severity error; two errors, AnchorCoverageError and BodyFillError, mean it cannot land, so the run fails with no final.akn.xml and grade ungraded. Validate: each finding names its check, its severity and usually an eId; the worked example has one, missing_container_title, warning, on part_II. Grade: any error makes it blocking, any warning makes it warning, otherwise clean, and info never counts; the worked example grades warning.](../figures/v10a-structure-to-grade.svg "A gate that fires lands the document with an error finding that grades it blocking, and only the two errors under Run fails stop the run (from the source, with the worked example's real finding).")

A `structure_halted` finding's `gate` names what fired ([every gate](../reference/validation-checks.md#structure-halts)).

| Symptom | Cause | What to do |
| --- | --- | --- |
| Manifest has `"anchors": 0` and `"fallback": "verbatim_section"` | No marker found; the whole text is one `SECTION 1`. Under 10,000 characters `structureless_body` does not fire. | Read `fallback`, not the grade. |
| `structure_halted`, `"gate": "containers_only"`: `containers_only on section: no article, section or paragraph in the body; ...` | Headings anchored, no basic unit; the scaffold is the whole document. | Check how the config marks the basic unit. |
| `structure_halted`, `"gate": "coverage_below_floor"`: `coverage_below_floor on section: captured N of M section markers, P% against a 80% floor. Landed for repair rather than refused.` | The scan missed basic units the text shows. | Find them under `missing` in `coverage.json`. |
| `structure_halted`, `"gate": "body_fill_failed"`: `the model wrote no body (6 of 6 body-fill calls failed); ...` | Every model call failed; bodies are copied source text, named in `body_fill_verbatim`. | Run again when the model answers. |
| Manifest `"failed": {"stage": "structure", "error": "BodyFillError: body fill produced no text for any of 3 window(s); the scaffold is correct and the document has no body"}`, exit status 1 | Every model call failed and copying the source text failed too. No `final.akn.xml`; grade `ungraded`. | Run again when the model answers. |
| Manifest `"failed": {"stage": "structure", "error": "AnchorCoverageError: anchor coverage 0% below floor 80%: ..."}`, exit status 1 | Markers present, none captured; no `final.akn.xml`. | If the log shows `anchor_boundary_policy_void`, the text lost its line breaks under `line_anchored`, so markers sit mid-line. |
| `no chat endpoint: set LLM_BASE_URL (and LLM_API_KEY) to an OpenAI-compatible endpoint, in the environment or the nearest .env`, exit status 2 | `LLM_BASE_URL` is unset. Nothing runs and the output folder is untouched. | Set it ([Models and costs](../operate/models-and-costs.md)). |

<details><summary>Where these rows come from</summary>

Rows come from synthetic texts with the model endpoint unreachable, except `coverage_below_floor`, quoted from the source, and the last, run with `LLM_BASE_URL` unset. The `BodyFillError` row is the worked example with the model unreachable: the dropped Part II heading left a line starting `PART` in a section body, and a copied body may not open a new unit. Fixing the scan removes it.

</details>

## Status and limits

| Capability | Status |
| --- | --- |
| Structure one PDF, `.txt` or `.md` file into a bundle | **Available**: `codify ingest-one` |
| Structure an uploaded PDF, or a publisher's XML, over HTTP | **Available**, with no bundle ([Serve and publish](serve-and-publish.md)) |
| Structure an uploaded `.txt` over HTTP | **Available**, with a defect: it goes to the PDF lane and fails. Use `ingest-one` or `ingest_text` in `codify.pipeline.formats.pdf`. |
| Measure the scan with no model | **Available**: `codify scan-corpus` |
| Choose the document class for a run | **Library only**: `ingest(..., doctype=...)` in `codify.pipeline.formats.pdf`; the command uses the config's classification rules, then its default class. |
| Structure each act of a multi-act source separately | **Library only**: `codify.pipeline.segment.segment` returns the spans; `ingest-one` writes them to `segmentation.json` and structures the source as one. |
| Send a publisher's XML to a rule-based lane from the command line | **Library only**: `ingest-one` reads it as a PDF; `codify.pipeline.ingest_document` routes by suffix. |
| Set window size or model-call concurrency | **Not in the core**: both are constants in the source. |

:::note[On the hosted Platform]
The hosted Platform includes calibration: adapting Codify to a jurisdiction's documents and legal conventions.
:::

## Next

[Validate and repair](validate-and-repair.md)
