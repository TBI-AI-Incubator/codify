---
title: Validate and repair
description: Read a document's findings and grade, check it against the Akoma Ntoso schema, and correct a defect with a typed edit that is refused if it loses text.
sidebar:
  order: 6
---

You end with every finding on the worked example read, its file checked against the schema, and a correction applied that the guards kept because it cleared its finding.

## Before you start

- The bundle from [Structure a document](structure.md), in `bundle/`.
- Steps 1 to 4 need no model and no database. Step 5 needs a chat model that can call tools.

## The idea in brief

Schema validation asks whether the file is valid [Akoma Ntoso](../concepts/akoma-ntoso.md) 3.0. Structural validation asks whether the document agrees with its source and itself. A file can pass the schema with half its sections missing, so the structural validator sets the grade.

Many structural checks need evidence only the ingest run holds, so ingest writes the findings to the bundle. Each finding is a JSON object naming its check and severity; [Severity and grade](../reference/validation-checks.md#severity-and-grade) gives the rule that folds them into `clean`, `warning`, `blocking` or `ungraded`.

A correction to legal text must be attributable and must not lose words, so a repair is a plan of typed operations, each naming an eId (set a body, move a unit, renumber a run, annotate a gap), never a free edit of the XML. The plan is applied to a copy and kept only if every guard passes.

![The repair loop, top to bottom. Repair permitted: the check must allow an operation, or the finding is marked skipped_non_actionable, which is where the worked example's missing_container_title finding stops, to be fixed in the config and ingested again. Plan: typed operations on named eIds, from a model or a person. Six guards, on a copy: the operation is permitted and its eIds are in scope; the result passes the lenient schema; the finding is cleared and no new finding appears; body text does not shrink; meta and all other text are unchanged; a written body shares enough words with the source page. If a guard fails the plan is rejected and the reasons go back to the model, which revises up to 3 times; after the third the finding is settled revision_budget_exhausted with the document unchanged. Risk: the plan takes its riskiest operation's class, raised to high when it writes a body with no page evidence, when the source has changed, or when it rewrites a duplicate_number body; a high risk plan is queued_for_approval, its proposal pending and the document unchanged; a low or medium risk plan is applied, its finding cleared and the edit kept in the document.](../figures/v10b-repair-loop.svg "Follow one finding down the main path: the document changes only at Applied (the logic is the repair loop's source; the worked example's real finding stops at Skipped).")

The schema guard is Cobalt's lenient form; step 3 checks the strict OASIS form. Body text may shrink only when the plan deletes, splits or merges, or the check removes noise.

## Steps

1. Read the grade in `bundle/manifest.json`. The reason is the first finding at the deciding severity.

   ```json
   "validator_findings": 1,
   "grade": {
     "grade": "warning",
     "reason": "missing_container_title: part II has no descriptive heading; the reader shows a bare marker",
     "rubric_version": 1
   },
   ```

2. Read the findings in `bundle/validator.json`: check, severity and eId where there is one.

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

   [Structure a document](structure.md#check-your-result) traces this finding to the config. [Validation checks](../reference/validation-checks.md) describes every check, and marks those that apply only to `article` elements or read only Arabic-script conventions.

3. Validate a file after any change. Two functions share the name `validate_akn` ([two kinds of validation](../concepts/akoma-ntoso.md#two-kinds-of-validation)): the structural one returns findings, the schema one raises. Save this as `validate_bundle.py` beside `bundle/`:

   ```python
   from pathlib import Path

   from lxml import etree

   from codify.akn import validate_akn as validate_schema
   from codify.pipeline.enrich.validator import validate_akn
   from codify.quality.structural_quality_grade import structural_quality_grade

   akn = Path("bundle/final.akn.xml").read_text(encoding="utf-8")

   findings = validate_akn(akn)
   for finding in findings:
       print(finding["severity"], finding["check"], finding.get("eid", ""))
   print("grade:", structural_quality_grade(findings).grade)

   try:
       validate_schema(akn, strict=True)
       print("schema: valid")
   except etree.DocumentInvalid as exc:
       print("schema:", exc)
   ```

   ```bash
   uv run python validate_bundle.py
   ```

   Output, with one log line omitted:

   ```text
   warning missing_container_title part_II
   grade: warning
   schema: valid
   ```

   Given only the XML, the validator runs the checks it can derive from the document; checks against the scan, source text or pages need that evidence as keyword arguments, as ingest passes it.

4. Correct a defect by hand. The repair loop accepts any function that returns a plan, so a person can supply one. This draft Act lacks section 3 because it was repealed, so the correction records that in a note and neither invents nor deletes a section. Save this as `repair_by_hand.py`:

   ```python
   import asyncio

   from codify.akn.bluebell import bluebell_to_akn
   from codify.pipeline.enrich.validator import validate_akn
   from codify.repair.edit_ops import apply_plan
   from codify.repair.loop import repair_document
   from codify.repair.ops import Annotate, Delete, EditPlan

   draft = """\
   BODY

   SECTION 1 - Short title

     This Act may be cited as the Harbour Dues Act, 1997.

   SECTION 2 - Dues payable

     Every vessel entering the harbour shall pay the dues set by the Authority.

   SECTION 4 - Offences

     A master who fails to pay the dues commits an offence.
   """
   akn, _, _ = bluebell_to_akn(draft, country="xa", date="1997", number="2")
   findings = validate_akn(akn)
   gap = next(f for f in findings if f["check"] == "number_gap")
   print(gap["severity"], gap["eid"], gap["message"])

   # A deletion cannot answer a numbering gap: the policy refuses it.
   refused = apply_plan(akn, [Delete(eid="sec_4")], country="xa", target_finding=gap)
   print(refused.ok, refused.error)


   async def by_hand(deps):
       note = "Section 3 was repealed by the Harbour Dues (Amendment) Act, 2001."
       return EditPlan(ops=[Annotate(eid=deps.eid, note=note)], reasoning="Checked against the 2001 Act.")


   outcome = asyncio.run(
       repair_document(akn, findings, agent_run=by_hand, country="xa", object_key="")
   )
   print(outcome.before, "->", outcome.after, outcome.counts())
   for proposal in outcome.proposals:
       print(proposal["check"], proposal["risk"], proposal["status"], proposal["ops"])
   ```

   ```bash
   uv run python repair_by_hand.py
   ```

   Output, with log lines omitted:

   ```text
   info sec_2 section numbers [3] are absent between 2 and 4 (likely legitimate repeals).
   False delete not permitted for finding 'number_gap'
   1 -> 0 {'cleared': 1}
   number_gap low applied [{'op': 'annotate', 'eid': 'sec_2', 'note': 'Section 3 was repealed by the Harbour Dues (Amendment) Act, 2001.'}]
   ```

   `outcome.akn` holds the note as an `authorialNote` marked `editorial-gap` on the heading of section 2, before the gap, and the gap finding is gone:

   ```xml
   <section eId="sec_2">
     <num>2</num>
     <heading>Dues payable<authorialNote eId="edn_1" placement="bottom" marker="editorial-gap"><p>Section 3 was repealed by the Harbour Dues (Amendment) Act, 2001.</p></authorialNote></heading>
   ```

5. Let a model propose the plan with `build_agent(model, store)` from `codify.repair.agent`. This block calls a model and was not run for this guide, so no output is shown.

   <details><summary>How the agent works, and an evidence store for a bundle</summary>

   The agent is a Pydantic AI agent that investigates one finding with read-only tools (document outline, any element, text search, page text, page image), tests its plan in the same sandbox and submits it. A rejected plan returns to the model with the reasons, up to three retries. `model` is a Pydantic AI model object for your endpoint. `store` follows the `EvidenceStore` protocol in `codify.repair.evidence`: three async methods returning a page image, a cropped layout block and the stored read of a page. A bundle keeps only page images:

   ```python
   from pathlib import Path

   from codify.repair.agent import build_agent


   class BundleEvidence:
       def __init__(self, bundle: Path) -> None:
           self.bundle = bundle

       async def render_page(self, object_key: str, page_no: int) -> bytes:
           return (self.bundle / "pages" / f"page-{page_no:03d}.png").read_bytes()

       async def render_region(self, version_id, object_key, page_no, block_index) -> bytes:
           raise ValueError("no layout blocks are kept in a bundle")

       async def page_read(self, version_id, page_no):
           return None


   agent = build_agent(model, BundleEvidence(Path("bundle")))


   async def agent_run(deps):
       return (await agent.run("Repair the flagged finding.", deps=deps)).output
   ```

   Pass `agent_run` to `repair_document` as in step 4, with `page_text` set to the contents of `source.txt` and `raw_spans` to the `pages` list of `page_spans.json`, so each finding is matched to its source page.

   </details>

## Check your result

`repair_document` returns a `RepairOutcome`: `before` and `after` count findings, `findings` gives one disposition per finding, and `proposals` records each plan with its operations, reasoning, risk and a before-and-after text audit. Write `outcome.akn` to a file and run step 3 on it. Step 4's draft fails the strict schema on `FRBRdate` before and after repair, because a bare year (`date="1997"`) passes only the guard's lenient schema; the worked example has a full date and passes.

<details><summary>What counts as high risk, and how to see it</summary>

High risk covers every deletion, a body written with no source text to check it against, a plan made against source text that has since changed, and a rewritten body for a `duplicate_number`. To see it, edit step 4's script: use a draft with section 2 twice, delete the lines that find and use `gap`, and pass an `agent_run` that returns `EditPlan(ops=[Delete(eid=deps.finding["eids"][1])])`. The validator reports `duplicate_number` on `['sec_2', 'sec_2_2']` and the deletion clears it in the sandbox, yet the loop applies nothing: `outcome.counts()` is `{'queued_for_approval': 1}`, `outcome.akn == akn` is `True`, and the proposal is `pending` for a person to decide.

</details>

## What can go wrong

| Symptom | Cause | What to do |
| --- | --- | --- |
| Disposition `skipped_non_actionable` | No operation is permitted for the check, or the finding names no element. A `number_gap` whose `likely` is `defect` is skipped too: annotating a structuring drop as a repeal would be a false record. | For the worked example's `missing_container_title`, fix the config and ingest again. |
| `delete not permitted for finding 'number_gap'` | The policy forbids that operation for that check. | Use an operation [Validation checks](../reference/validation-checks.md#what-repair-may-do) allows. |
| `out of scope: annotate targets 'sec_4', which the finding does not name (permitted: sec_2)` | The plan reaches an element the finding does not name. | Target only the eIds the finding names. |
| Disposition `queued_for_approval` | The plan is high risk; nothing was applied. | A person decides the `pending` proposal. |

<details><summary>Other guard refusals and dispositions</summary>

| Symptom | Cause |
| --- | --- |
| `schema-invalid: ...` | The edit fails the lenient schema. |
| `introduced findings [...], rejected` | The edit created a new finding. |
| `targeted finding not cleared, rejected` | The edit applied and the finding remains. |
| `body text shrank, rejected` | The edit lost text, and neither the plan nor the check may shorten it. |
| `insufficient_grounding: <eid> scored <score>` | A body the plan writes shares too few words with the nearby source pages (floor 0.3). |
| `meta_modified` or `unchanged_span_modified: <eid>` | The plan changed the `meta` block, or text outside the elements it names. |
| Disposition `revision_budget_exhausted`, `investigation_budget_exhausted` or `errored` | The agent produced no accepted plan. Earlier accepted edits are kept. |
| Disposition `empty_plan` | The agent's plan had no operations; nothing was applied. |
| Disposition `ops_rejected` | The loop's own sandbox pass refused a plan the agent's check accepted; `reasons` says why. |
| Disposition `eid_gone` | An earlier edit in the same run removed the finding's element. |

</details>

`codify load` stores the document but not its findings or grade, so the stored version's `structural_quality_grade` is null; the findings stay in the bundle ([Bundle](../reference/bundle.md)).

## Status and limits

| Capability | Status |
| --- | --- |
| Structural findings and a grade for each ingest | **Available**: `codify ingest-one` writes `validator.json` and the manifest's `grade` |
| Structural validation of any Akoma Ntoso file | **Library only**: `codify.pipeline.enrich.validator.validate_akn` |
| Schema validation | **Library only**: `codify.akn.validate_akn`. The PDF and text lanes skip it; the repair guards run its lenient form. |
| Corpus-level scan checks against a pinned baseline | **Available**: `codify scan-corpus --check` |
| Store a document's grade with its stored version | **Not in the core**: the `structural_quality_grade` column exists and nothing in the core writes it. |
| Record per-check results of a corpus scan, per version | **Library only**: `record_findings` in `codify.storage.structural_findings`. No command calls it. |
| Apply a typed edit plan with guards | **Library only**: `apply_plan` in `codify.repair.edit_ops`, and `preview_plan` in `codify.repair.sandbox` for the full report |
| Run the repair loop over a document's findings | **Library only**: `repair_document` in `codify.repair.loop`. There is no command or route for it yet; call the function directly. |
| Model-proposed repairs | **Library only**: `build_agent` in `codify.repair.agent`. You supply the model and the evidence store. |
| Review and approve queued proposals | **Not in the core**: there is no review screen or route. `codify.storage.repair_proposals` stores and decides proposals for an application built on it. |

:::note[On the hosted Platform]
The hosted Platform keeps a tamper-evident trail of who read and changed what.
:::

## Next

[Amendments and versions](amendments-and-versions.md)
