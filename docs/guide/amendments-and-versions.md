---
title: Amendments and versions
description: Store each dated text of a law as its own version, record what amending acts changed, and ask what state a provision was in on a given date.
sidebar:
  order: 7
---

You end with two stored versions of the worked example, as enacted and as amended from 1 January 2012, with the amendments recorded as dated effects you can query by date.

## Before you start

- The bundle from [First ingest](../start/first-ingest.md), in `bundle/`.
- A migrated database, with `POSTGRES_URL` pointing at it. See [Database](../operate/database.md).
- A source checkout: every step after the first is Python, with no command yet. No step calls a model.

## The idea in brief

Under [FRBR](../concepts/frbr.md) the *work* is the law, with one URI for its whole life, and each *expression* is one dated text in one language. Codify stores a work as one law and each expression as one version. The database refuses to change a version's URI, language, date or parent, or to delete it on its own, so a reference to a dated text always finds the same row; its text can still be corrected in place, with an attribution (see [What can go wrong](#what-can-go-wrong)).

![One work, /akn/xa/act/1992/7, the Legislation Act, 1992, with one URI for its whole life, and its expressions on a date axis. From 1992-05-02, the English expression /akn/xa/act/1992/7/eng@1992-05-02, as enacted, its URI fixed and still citable, in the file final.akn.xml. From 2012-01-01, the English expression /akn/xa/act/1992/7/eng@2012-01-01, the current text, amended by /akn/xa/act/2011/5, in the file consolidated-2012.akn.xml. Translated from it and stored as its child, the French expression /akn/xa/act/1992/7/fra@2012-01-01, with the same date, not authoritative and never current, in the file translated-fra.akn.xml. Each box is one expression, one text in one language from one date; its shaded strip is its file, a manifestation.](../figures/v5-family-tree.svg "One law, three dated texts in two languages, each saved as a file; identifiers and file names are from the worked example and its amendment and translation steps.")

Codify records amendments; it does not apply them. For PDF and Bluebell sources you supply each amended text, from your own or a publisher's consolidation, and declare its effects. One structured-source import, for a publisher XML format with amendment notes, declares them for you.

<details><summary>How an effect finds its provision</summary>

By [wId](../concepts/akoma-ntoso.md#identifiers-eid-and-wid), meant to persist across expressions, or failing that by eId, which is positional and can change on renumbering. An effect matching neither counts as `no_target`. The worked example has no wIds and the 2011 Act renumbers nothing, so every effect matches by eId.

</details>

## Steps

### Load the text as enacted

```bash
uv run codify load bundle/
```

```json
{"version_id": "20cb9d70-142f-47fc-993b-98d884a79ad8", "work_uri": "/akn/xa/act/1992/7", "embedded": 0}
```

A second run prints the same `version_id` and writes nothing.

### See how an amending act is marked up

An amending act is a work of its own; the text it quotes belongs to the law it amends. Ingest wraps each quoted block holding structure in `<mod>` (the amending instruction) around `<quotedStructure>` (the quoted provisions). Run this from the root of the source checkout.

<details><summary>How the anchor scan spots quoted articles</summary>

The *anchor scan* (the rule-based pass that finds structural markers before a model fills in text; see [Structure](structure.md)) treats an article number as quoted when it follows one of the config's `amendments.trigger_phrases` plus a colon and breaks the act's own sequence. A quoted article takes no eId and does not count in the act's numbering. `amending_body_structure` and `amendment_item_kind` cover two other drafting patterns ([jurisdiction config reference](../reference/jurisdiction-config.md)).

</details>

```python
from pathlib import Path

from lxml import etree

from codify.akn.bluebell import bluebell_to_akn
from codify.pipeline.enrich.amendments import lift_amendment_markup

text = Path("tests/fixtures/synthetic/xq/archivos-reforma-2018.bluebell").read_text(encoding="utf-8")
akn, _, errors = bluebell_to_akn(text, country="xq", date="2018", number="12", language="spa")
assert not errors
lifted = etree.fromstring(lift_amendment_markup(akn).encode("utf-8"))
ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
mod = lifted.find(".//a:mod", ns)
print(etree.tostring(mod, pretty_print=True, encoding="unicode"))
```

```text
... [info     ] amendment_lift_complete        mods=2
<mod xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" eId="part_I__art_1__mod_1"><quotedStructure eId="part_I__art_1__mod_1__qstr_1">
                <article eId="part_I__art_1__mod_1__qstr_1__art_1">
                  <num>4</num>
                  <heading>Solicitud</heading>
                  ...
                </article>
              </quotedStructure>
            </mod>
```

Ingest stops here: it records no effect from a `<mod>` and does not find the law it amends.

### Store the amended text as a new expression

From 1 January 2012 the fictional Legislation (Amendment) Act, 2011 (`tests/fixtures/synthetic/xa/publication-amendment-2011.bluebell`) substitutes "seven days" for "thirty days" in section 5, repeals section 11, and repeals the words "other than a decision to defer publication under section 11," in section 13(1). Save this script, which makes the 2012 text, as `consolidate.py`; check its `<destination>` eIds against your `bundle/final.akn.xml`.

```python
from pathlib import Path

from codify.akn import validate_akn

akn = Path("bundle/final.akn.xml").read_text(encoding="utf-8")

# 1. The amended words of section 5(1).
assert akn.count("within thirty days") == 1
akn = akn.replace("within thirty days", "within seven days")

# 2. The words repealed from section 13(1).
words = ' other than a decision to defer publication under <ref href="#part_II__sec_11" class="derived">section 11</ref>,'
assert akn.count(words) == 1
akn = akn.replace(words, "")

# 3. A new expression of the same work, dated to the day the amendment took effect.
akn = akn.replace("eng@1992-05-02", "eng@2012-01-01")
akn = akn.replace(
    '<FRBRdate date="1992-05-02" name="Original"/>',
    '<FRBRdate date="2012-01-01" name="Amendment"/>',
)

# 4. What changed, by which act, and from when.
effects = """
      <lifecycle source="#codify">
        <eventRef eId="evt_enacted" date="1992-05-02" type="generation" source="/akn/xa/act/1992/7"/>
        <eventRef eId="evt_2012" date="2012-01-01" type="amendment" source="/akn/xa/act/2011/5"/>
      </lifecycle>
      <analysis source="#codify">
        <passiveModifications>
          <textualMod eId="pmod_1" type="substitution">
            <source href="/akn/xa/act/2011/5"/>
            <destination href="/akn/xa/act/1992/7/!main#part_I__sec_5__point_1"/>
            <force period="#period_2012"/>
          </textualMod>
          <textualMod eId="pmod_2" type="substitution">
            <source href="/akn/xa/act/2011/5"/>
            <destination href="/akn/xa/act/1992/7/!main#part_IV__sec_13__point_1"/>
            <force period="#period_2012"/>
          </textualMod>
          <textualMod eId="pmod_3" type="repeal">
            <source href="/akn/xa/act/2011/5"/>
            <destination href="/akn/xa/act/1992/7/!main#part_II__sec_11"/>
            <force period="#period_2012"/>
          </textualMod>
        </passiveModifications>
      </analysis>
      <temporalData source="#codify">
        <temporalGroup eId="period_2012">
          <timeInterval start="#evt_2012" refersTo="#inForce"/>
        </temporalGroup>
      </temporalData>"""
akn = akn.replace("</identification>", "</identification>" + effects, 1)

validate_akn(akn)  # raises DocumentInvalid if the edits broke the schema
Path("consolidated-2012.akn.xml").write_text(akn, encoding="utf-8")
```

<details><summary>How the fourth edit states an amendment</summary>

`<passiveModifications>` lists changes to this document. Each `<textualMod>` names the amending act in `<source>`, the amended provision in `<destination>`, and in `<force>` a period in `<temporalData>`, which points to the `<lifecycle>` event carrying the date. See [Akoma Ntoso](../concepts/akoma-ntoso.md).

</details>

Two script choices have a cost:

- Section 11 stays in the text so citations to its eId resolve, but the stored text, web reader and search show it in force; only `state_at` (next step) reports the repeal.
- The section 13(1) words are recorded as a `substitution`, because Codify reads `repeal` as ending the whole provision, so the document and its exports state a substitution where the Act repealed words.

```bash
uv run python consolidate.py
uv run codify load consolidated-2012.akn.xml --jurisdiction xa --title "Legislation Act, 1992"
```

```json
{"version_id": "57b16c65-3e0c-4399-bc87-b2348e36fe4f", "work_uri": "/akn/xa/act/1992/7", "embedded": 0}
```

Same work URI, new `version_id`. Each `<textualMod>` became an `amendment_effects` row and each `<eventRef>` a `lifecycle_events` row.

![Over time, an amendment: the eId part_I__sec_5__point_1 names subsection 5(1) in two expressions of the Legislation Act, 1992. As enacted, /akn/xa/act/1992/7/eng@1992-05-02, it reads: (1) The Publishing Authority shall maintain the official text of every law in a state that is current to within thirty days of the coming into force of any amending instrument affecting the law. As amended from 1 January 2012, /akn/xa/act/1992/7/eng@2012-01-01, it reads the same with seven days in place of thirty days, a change consolidate.py made by replacing the words in the file. The 2012 text records the change as textualMod pmod_1, type substitution, with source /akn/xa/act/2011/5, destination /akn/xa/act/1992/7/!main#part_I__sec_5__point_1 and force period #period_2012.](../figures/v7-amend.svg "One eId names the same provision before and after an amendment; texts are from the worked example and its amendment step, with the 2012 wording replaced by consolidate.py.")

### Ask what state a provision was in on a date

Save this as `timeline.py`. `effects_for_version` reads the work's effects and locates each in the version; `state_at` is a pure function over them.

```python
import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from codify.settings import database_url
from codify.storage import effects_for_version
from codify.timeline import state_at


async def main(version_id: uuid.UUID) -> None:
    engine = create_async_engine(database_url())
    async with AsyncSession(engine) as session:
        effects = await effects_for_version(session, version_id)
    await engine.dispose()

    for day in (date(2011, 12, 31), date(2012, 1, 1)):
        timeline = state_at(effects, day)
        print(day, timeline.holes)
        for eid, state in timeline.states.items():
            print(f"  {eid}: {state.kind} since {state.date}")


asyncio.run(main(uuid.UUID(sys.argv[1])))
```

```bash
uv run python timeline.py 57b16c65-3e0c-4399-bc87-b2348e36fe4f
```

```text
2011-12-31 HoleCounts(undated=0, unapplied_by_publisher=0, needs_text=0, no_target=0, out_of_scope=Counter())
2012-01-01 HoleCounts(undated=0, unapplied_by_publisher=0, needs_text=2, no_target=0, out_of_scope=Counter())
  part_II__sec_11: repealed since 2012-01-01
  part_IV__sec_13__point_1: text_unapplied since 2012-01-01
  part_I__sec_5__point_1: text_unapplied since 2012-01-01
```

The 1992 version id gives the same answer, as effects belong to the work; same-day effects come in row-id order, so your line order may differ. `state_at` reports status, never text: a provision absent from `states` is untouched, and `holes` counts, by cause, effects it could not place.

<details><summary>What each <code>kind</code> means</summary>

| `kind` | Meaning |
| --- | --- |
| `repealed` | A `repeal` took effect by the date. Outranks every other state. |
| `text_unapplied` | A `substitution` or `insertion` took effect. Codify cannot tell whether your text carries the new words, so it also counts under `needs_text`. |
| `not_yet_in_force` | An `entryIntoForce` effect exists, none dated by the date. |
| `in_force` | An `entryIntoForce` effect is dated by the date; nothing else applies. |
| `affected` | Any other action took effect; `action` names it. |

</details>

## Check your result

Untouched provisions must keep their eIds, or effects and citations will not line up; `structure_diff` shows any insertion or removal.

```bash
uv run python -m codify.akn.structure_diff bundle/final.akn.xml consolidated-2012.akn.xml
```

```text
0 structural difference(s)
```

With `codify serve` running, `GET /laws/{law_id}/versions` lists both, newest expression date first:

```json
{
    "items": [
        {
            "id": "57b16c65-3e0c-4399-bc87-b2348e36fe4f",
            "law_id": "21278932-9b16-4320-b50a-c832f3c61806",
            "expression_uri": "/akn/xa/act/1992/7/eng@2012-01-01",
            "language": "eng",
            "expression_date": "2012-01-01",
            ...
        },
        {
            "id": "20cb9d70-142f-47fc-993b-98d884a79ad8",
            "law_id": "21278932-9b16-4320-b50a-c832f3c61806",
            "expression_uri": "/akn/xa/act/1992/7/eng@1992-05-02",
            ...
        }
    ],
    "next_cursor": null
}
```

The 2012 text is now the law's *current* version, so [Search](search.md) without a language answers from it once embedded (`codify load --embed`, which calls an embedding model).

<details><summary>Which version search reads</summary>

Each law has one current version: among versions with no parent, the latest expression date, then the latest ingest. A database trigger keeps the flag. Search without a language reads current versions only. Search with a language (`codify search --language eng`) takes each law's latest version in that language, children included, falling back to the current one.

</details>

## What can go wrong

| Symptom | Cause | What to do |
| --- | --- | --- |
| `load` prints an existing `version_id` after you corrected the file | The expression URI is stored, so `load` returns it unchanged and exits 0. | Use a correction call below. |
| `ForeignJurisdictionCollision: /akn/xa/act/1992/7 already names a law outside xq; refusing to write` | Another jurisdiction code holds the work URI; work URIs are unique across the database. | Check the jurisdiction code. |
| `ValueError: Document is missing a parseable <FRBRExpression><FRBRdate date=...> entry` | No expression date, so the file cannot be a version. | Add the `FRBRdate`. |
| `DocumentInvalid: ... Element '...old': Character content other than whitespace is not allowed because the content type is 'element-only'.` | Words inside `<old>` or `<new>` in `<analysis>`, which accept only an `href`. | Leave the wording in the amending act. |
| An effect is missing from `amendment_effects` | A `<textualMod>` with no source, no destination, or a `type` that is not an Akoma Ntoso modification type is skipped silently. | Fix the `<textualMod>`. |
| `holes.undated` or `holes.no_target` above zero | An effect has no date, or names no provision of this text, so it is left off the timeline. | Date it, or correct its `<destination>`. |
| `CheckViolationError: versions row is immutable (op=UPDATE, id=...)` (or `op=DELETE`) | Something changed or deleted a version in SQL. | Use a correction call below. |
| `LookupError: no amendment-effects adapter for 'xa'; supported: eu, gb` | `adapter_for` has no feed for that jurisdiction. | Declare effects in the document. |
| Finding `undetected_amendment_shape`: `article numbering went non-monotonic at ...; possible undetected amendment quoted-structure or lost anchor range` | An amending act's quoted articles were read as its own. The check reads `<article>` numbers only, so never fires on acts numbered in sections. | Add the lead-in wording to `amendments.trigger_phrases`. |

<details><summary>Three library calls that correct a stored text, and their costs</summary>

| Call | Effect |
| --- | --- |
| `supersede_document` in `codify.storage` | Replaces a wrong ingest: deletes the law and everything under it, then stores the new document. Superseding the worked example with the 2012 text left only that version. |
| `update_repaired_akn` in `codify.storage`, then `rederive_version_rows` in `codify.storage.repository` | Rewrites one version in place, keeping its id and stamping `repaired_at` and `repair_attribution`. Its translations become stale. Rederiving deletes its search embeddings (it leaves search until embedded again) and other versions' references into it. See [Validate and repair](validate-and-repair.md). |
| `amend_provision` in `codify.storage` | Corrects one provision in answer to a validator finding (`finding_id`), storing a child version and logging a `version.amended_via_finding` event. The child's URI is the parent's plus `.n`, n counting all the parent's children, translations included (`/akn/xa/act/1992/7/eng@2012-01-01.1`). It refuses a container (`provision_eid 'part_I__sec_5' is a structural element (section); cannot patch text`) and rewrites the whole body: on the worked example the child kept its `<ref>` links and lost all 70 `<term>` elements. A child is never current, but search in its language finds it: on the worked example `latest_version_for_law(..., language="eng")` returned `eng@2012-01-01.1`, not its parent. |

</details>

## Status and limits

| Capability | Status |
| --- | --- |
| Store a further dated expression of a law | **Available**: `codify load` |
| Record effects declared in a document's `<analysis>` | **Available**: on every `codify load` and every upload to `codify serve` |
| List a law's versions | **Available**: `GET /laws/{law_id}`, `GET /laws/{law_id}/versions`, MCP `get_law`; responses do not mark current or parent |
| Read one version | **Available**: `GET /versions/{version_id}`, and `?v=` in the web reader |
| Mark quoted amendment text during ingest | **Available**: part of `codify ingest-one` |
| State of each provision on a date | **Library only**: `effects_for_version` and `codify.timeline.state_at` |
| Amendment history of a law or provision | **Library only**: `law_amendment_history`, `amendment_history` in `codify.storage` |
| Fetch a publisher's amendment effects | **Library only**: `codify.acquisition.effects` reads the `gb` and `eu` public feeds as `FeedEffect` records; nothing in the core stores them |
| Correct or replace a stored text | **Library only**: the three correction calls under [What can go wrong](#what-can-go-wrong) |
| Apply an amending act to produce the amended text | **Not in the core** |
| Work out from an amending act's wording which provisions it changes | **Not in the core** |
| Compare the structure of two versions | **Available**: `python -m codify.akn.structure_diff` |
| Show a law at a date, or two versions' textual differences, in the web app | **Not in the core** |

:::note[On the hosted Platform]
The hosted Platform adds a tamper-evident trail of who read and changed what.
:::

## Next

[Translate](translate.md)
