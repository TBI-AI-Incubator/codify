---
title: Validation checks
description: Every structural check the validator runs, its severity, the evidence it needs, the structure halts, and what repair may do for each.
sidebar:
  order: 4
---

The structural validator checks that a document agrees with its source and with itself. It reads one Akoma Ntoso file, plus any evidence the ingest passes, and returns findings. It refuses nothing: the findings set a grade, which is advice to the document's users. Source: [`codify/pipeline/enrich/validator.py`](../../codify/pipeline/enrich/validator.py); [Validate and repair](../guide/validate-and-repair.md) shows it in use.

Do not confuse it with the schema check. `codify.akn.validate_akn` tests the file against the Akoma Ntoso 3.0 schema and raises on failure: by default Cobalt's lenient form (which accepts year-only dates), with `strict=True` the OASIS schema. `codify.pipeline.enrich.validator.validate_akn` returns the findings below. See [two kinds of validation](../concepts/akoma-ntoso.md#two-kinds-of-validation).

![From structure gate to grade, top to bottom. Structure gates: a gate that fires lands the document and adds a structure_halted finding of severity error; two errors, AnchorCoverageError and BodyFillError, mean it cannot land, so the run fails with no final.akn.xml and grade ungraded. Validate: each finding names its check, its severity and usually an eId; the worked example has one, missing_container_title, warning, on part_II. Grade: any error makes it blocking, any warning makes it warning, otherwise clean, and info never counts; the worked example grades warning.](../figures/v10a-structure-to-grade.svg "Where a finding comes from and how the grade folds it; the logic is the source's and the finding is the worked example's real one.")

## Findings

Each finding has `check`, `severity` and `message`; most name the element in `eid` (or `eids`). Other fields depend on the check, such as `kind`, `missing` and `likely` on a numbering gap. The worked example's one finding:

```json
{
  "check": "missing_container_title",
  "severity": "warning",
  "eid": "part_II",
  "container": "part",
  "message": "part II has no descriptive heading; the reader shows a bare marker"
}
```

## Severity and grade

The grade folds the findings into one word ([`codify/quality/structural_quality_grade.py`](../../codify/quality/structural_quality_grade.py)):

| Grade | When |
| --- | --- |
| `blocking` | At least one `error`. |
| `warning` | No `error`, at least one `warning`. |
| `clean` | Neither. `info` never moves the grade. |
| `ungraded` | The validator did not finish, so no findings proves nothing. |

`reason` is the first finding at the deciding severity, as `check: message`. `rubric_version` is the constant `_GRADE_RUBRIC_VERSION` in the same file.

## Evidence the checks need

Given only the XML, the validator runs every check it can derive from the document. The rest need evidence passed as a keyword argument. The last column says whether `codify ingest-one` and the server's PDF ingest pass it.

| Argument | Enables | Passed by ingest |
| --- | --- | --- |
| `expected_anchor_summary` | `anchor_count_mismatch` | Yes, when the scan found anchors |
| `expected_cover_article_numbers` | `cover_body_article_count_mismatch` | Yes, when a contents listing was found |
| `source_text` | `fabricated_formula`, `header_coverage` | Yes |
| `provenance` | `"extracted"` raises every numbering gap to `warning` and adds the leading-gap check; `"native"` relaxes the date checks for publisher XML | Yes, `"extracted"` |
| `orphaned_drops` | `orphaned_drops` | Yes |
| `container_coverage` | `container_coverage` | Yes |
| `unreadable_pages` | `unreadable_page` | Yes, when a page came back empty |
| `body_fill` | `body_fill_incomplete`, `body_fill_verbatim` | Yes |
| `anchor_coverage` | `anchor_coverage_unmeasured` | Yes |
| `closing_phrases` | `displaced_terminal_material` | No. The repair loop passes it when its caller supplies the phrases. |
| `page_yield`, `recoverable_pages` | `page_yield` | No |

## Structure halts

A structure halt is a structurer gate that would have refused the document; `ingest-one` and the server land it instead, graded `blocking` and readable. The finding carries `gate`, `kind`, `spans`, `offset`, `eid`, `ratio` and a message ending in the route to a fix. Source: [`codify/pipeline/enrich/structure.py`](../../codify/pipeline/enrich/structure.py), [`codify/quality/invariants.py`](../../codify/quality/invariants.py).

| `gate` | Fires when | Route to a fix, as the message gives it |
| --- | --- | --- |
| `coverage_below_floor` | Captured basic-unit numbers over the numbers the text shows is above zero and below `min_anchor_coverage` (default 0.80). The basic unit is the provision the config numbers through the document: article, section or paragraph. | Landed for repair |
| `markers_masked` | A quotation mark that nothing closes hid markers from the scan | The source text and its unclosed quote, not the document |
| `containers_only` | Headings were anchored and no basic unit was | The provision markers the config declares |
| `duplicate_anchor` | An unresolved `duplicate_number` ambiguity span | Landed for repair |
| `act_boundary_suspected` | The source reads as two acts | Splitting the source |
| `source_truncated` | More of the contents listing lacks a body than `source_truncation_floor` allows. Off by default (floor `0.0`). | A complete copy of the source |
| `body_fill_failed` | Every model call failed and the bodies were copied in from the source | A re-run once the model answers |

The two conditions that are not landed record `failed` with stage `structure`, write no `final.akn.xml` and grade `ungraded`.

| Error | Fires when |
| --- | --- |
| `AnchorCoverageError: anchor coverage 0% below floor ...` | Markers are present and none was captured, or the config declares `line_anchored` and the markers sit mid-line (log `anchor_boundary_policy_void`). |
| `BodyFillError: body fill produced no text for any of N window(s) ...` | Every model call failed and no source text could be copied in. |

Called directly, `text_to_bluebell_scaffolded` in `codify.pipeline.enrich.structure` defaults to `halt_policy="fail"` and raises at every gate.

## Checks

Severity is fixed unless the column gives a rule. "Articles only" checks read `article` elements and stay silent for documents numbered in sections; "Arabic script" checks read Arabic-script legislative conventions and stay silent elsewhere.

### Identity and dates

| Check | Severity | Reports |
| --- | --- | --- |
| `uncitable_work_uri` | warning | The [FRBR](../concepts/frbr.md) work URI (the identifier of the law itself) has no citable year and number, because neither the model nor the title gave a number; it fell back to a content address such as `/akn/xa/act/0001/draft-...`. |
| `identity_year_in_future` | error | The `FRBRdate` year is later than the current year. |
| `identity_year_unconverted_hijri` | error | The year falls in 1300 to 1500: a Hijri year never converted to Gregorian. Skipped for publisher XML. |
| `identity_year_implausible` | error | The year is before 1800. Skipped for publisher XML. |
| `identity_year_uri_mismatch` | error | The year in the work URI differs from `FRBRdate`, so the document is citable two ways. |

### Identifiers and references

| Check | Severity | Reports |
| --- | --- | --- |
| `eid_uniqueness` | error | An eId appears more than once. |
| `eid_unusable` | error | An eId is deeper than 12 levels or longer than 255 characters, usually a flat list built as a chain of children. Once per document. |
| `ref_resolution` | warning | An internal reference `#...` names no eId in the document. |
| `definition_completeness` | warning | A `<term>` has no `refersTo`, or names no `<TLCTerm>` in the references. |

### Numbering

| Check | Severity | Reports |
| --- | --- | --- |
| `duplicate_number` | warning | Two siblings of the same kind share a number (`article`, `section`, `rule`, `paragraph`, `point`, `subsection`), scoped by parent. |
| `number_gap` | info, or warning | Numbers missing from a run of decimal-numbered siblings, one finding per run, on the provision before the gap. `likely`: `defect` (a run of three or more, or over 30% of the range missing), `repeal` (one to three single holes), `ambiguous`, or `unreadable_page` (the host carries an unreadable-page remark). `warning` for `defect`, `unreadable_page` and every gap in extracted text; otherwise `info`. Silent if the host carries an `editorial-gap` note. At most 10 runs per kind under one parent, 50 numbers each. |
| `number_gap` with `likely: leading` | warning | Extracted text only: the first article, section or rule is numbered above 1. |
| `section_numbering` | info | Within the body, a part or a chapter, consecutive sections or articles jump by more than 2 (reading the leading digits of labels such as `2A`). |
| `anchor_count_mismatch` | error, or warning | The document holds fewer elements of a kind than the scan anchored (error), or more than twice as many (warning). |
| `cover_body_article_count_mismatch` | warning, or error | The contents listing's article count differs from the body's: warning for a difference of two, error for more. A contents listing is five or more lines in the first 6,000 characters starting `Article`, `Art.` or `المادة` and a number, with a contents keyword or making up at least half the non-empty lines. |
| `undetected_amendment_shape` | warning | Articles only. Article numbers under one parent go backwards or jump by more than 8: a quoted amendment the amendment pass missed, or lost anchors. |
| `collapsed_bis_article` | error | Arabic script, articles only. An article marked bis (`مكرر`) whose points restart at the first letter: two articles merged. |

### Hierarchy and containers

| Check | Severity | Reports |
| --- | --- | --- |
| `hierarchy_coherence` | warning | A `subsection`, `paragraph` or `subparagraph` under a parent that cannot hold it. Quoted amendment text is exempt. |
| `orphan_articles` | warning, or error | Articles only. Articles directly under the body beside containers: warning for a leading run of at most 20% of all articles (general provisions before the first chapter), otherwise error. |
| `missing_container_title` | warning | A container (`book`, `tome`, `part`, `subpart`, `title`, `subtitle`, `chapter`, `subchapter`, `division`, `subdivision`, and `section` when the document also has articles) has no heading, so the reader shows a bare marker. |
| `container_heading_restates_num` | warning | A container's heading only repeats its number. |
| `container_coverage` | warning | Under half the source's grouping headings produced containers, so a level was flattened. |
| `orphaned_drops` | warning | A scan pass removed a marker and left its text with no provision. |

### Body text

| Check | Severity | Reports |
| --- | --- | --- |
| `empty_body` | error | No provisions and no more than 200 characters of text, unless an annex carries the content. |
| `structureless_body` | warning | One provision holding more than 10,000 characters, or none holding more than 200. |
| `empty_article` | warning | Articles only. An article with a number and heading but no text. |
| `body_fill_incomplete` | error | Units with source text that have no body. |
| `body_fill_verbatim` | warning | Units whose body is copied source text, because the model wrote none or changed a table. |
| `anchor_coverage_unmeasured` | warning | Basic units were found but the text has no markers to count them against. |
| `body_artefact` | error | Model output leaked into a paragraph: a markdown fence, a JSON fragment such as `]}]`, or phrases like "as valid JSON". |
| `markup_as_text` | error | A paragraph whose whole text is element names, such as `hcontainer` or `blockList`. |
| `seam_duplication` | warning | Articles only. An article repeats its opening words after a short gap containing digits: a page break written twice. |
| `swallowed_enumerator` | warning | Arabic script. A point's letter is missing from its run but appears inside the previous point's text. |
| `ocr_garble` | warning | Arabic script. Doubled letters that never double in correct text (`ةة`, `ءء`). |
| `header_coverage` | warning | Arabic script. The source shows at least six article headers and the document has under 90% of them. |
| `money_words_mismatch` | warning | Arabic script. A money numeral disagrees with the amount written in words beside it. |
| `displaced_terminal_material` | warning | Signature or promulgation lines sit inside the last provision instead of the conclusions. Needs `closing_phrases`. |

### Pages and enacting formula

| Check | Severity | Reports |
| --- | --- | --- |
| `unreadable_page` | error | Source pages came back with no text, or only part of it; `reasons` gives each page's reason. |
| `page_yield` | error, or warning | Pages the first engine read empty: error when the document lacks their text, warning when the other engine supplied it. Needs `page_yield`. |
| `fabricated_formula` | error | An enacting formula that does not appear in the source text. Formulas the config supplied are skipped. |
| `formula_truncated` | error | An enacting formula ends in an ellipsis. |
| `formula_script_mismatch` | error | An enacting formula's script does not match the document's language. |

## What repair may do

The repair loop sends a finding to its agent only if its check has permitted operations ([`codify/repair/edit_ops.py`](../../codify/repair/edit_ops.py)); it marks every other finding `skipped_non_actionable`.

| Check | Permitted operations |
| --- | --- |
| `empty_article`, `ocr_garble` | `set_body`, `restore_from_source` |
| `body_artefact` | `set_body` |
| `swallowed_enumerator` | `split` |
| `orphan_articles`, `hierarchy_coherence` | `move` |
| `duplicate_number` | `set_num`, `renumber_sequence`, `merge`, `delete`, `set_body`, `move` |
| `money_words_mismatch` | `set_money_numeral` |
| `number_gap` | `annotate`, except a gap whose `likely` is `defect`, which is skipped |
| `displaced_terminal_material` | `move_to_conclusions` |

Each operation has a risk class ([`codify/repair/ops.py`](../../codify/repair/ops.py)); a plan takes its riskiest operation's:

| Risk | Operations | What the loop does |
| --- | --- | --- |
| low | `move`, `move_to_conclusions`, `annotate` | Applies the plan |
| medium | `set_body`, `set_money_numeral`, `set_num`, `renumber_sequence`, `split`, `merge`, `restore_from_source` | Applies the plan |
| high | `delete` | Queues the plan as `pending` and changes nothing |

A plan also becomes high when it writes a body with no source text to check against or for a `duplicate_number`, or when the source text changed after planning. A plan may shorten body text only if it deletes, splits or merges, or its check is `ocr_garble`, `body_artefact`, `money_words_mismatch` or `displaced_terminal_material`.

## Other lanes

The rule-based publisher-XML lanes also run the validator, with `provenance="native"`. The EU lane adds findings whose `check` is a provenance signal: `unmapped_blocks`, `annex_images`, `annex_included_docs`, `annex_unmapped_elements`, `body_discarded_text`, `html_images` and `deduped_eids` are warnings, any other is `info` ([`codify/pipeline/formats/eu_directive.py`](../../codify/pipeline/formats/eu_directive.py)).

## Status

| Capability | Status |
| --- | --- |
| Findings and a grade for each ingest | **Available**: `codify ingest-one` writes `validator.json` and the manifest's `grade` |
| Run the validator on any file | **Library only**: `codify.pipeline.enrich.validator.validate_akn` |
| Fold findings into a grade | **Library only**: `codify.quality.structural_quality_grade.structural_quality_grade` |
| Store a document's grade with its stored version | **Not in the core**: the `structural_quality_grade` column exists and nothing in the core writes it |
| Record per-check results of a corpus scan, per version | **Library only**: `record_findings` in `codify.storage.structural_findings`; no command calls it |
