---
title: Translate
description: Make a translation of a stored law as a new expression that keeps every eId, record what it was translated from, and know when it goes stale.
sidebar:
  order: 8
---

You end with a French expression of the worked example stored beside its English text, with the same eIds, a record of its English source, and a way to tell when that source changes.

## Before you start

- [Amendments and versions](amendments-and-versions.md) done, so `bundle/` and `consolidated-2012.akn.xml` exist and the 2012 text is current. Note its `version_id`. Translate the current version: a translation of a superseded text answers in its language with the old law.
- A migrated database, with `POSTGRES_URL` pointing at it. See [Database](../operate/database.md).
- A source checkout: every step is Python; no command translates yet.
- For the translate step only, a chat model endpoint ([Models and costs](../operate/models-and-costs.md)); no other step calls one.

## The idea in brief

A translation is a new [expression](../concepts/frbr.md#work-expression-manifestation) of the same work, with the source's date and every [eId](../concepts/akoma-ntoso.md#identifiers-eid-and-wid), marked `authoritative="false"`. The model sees text per eId, never structure, and its answers are written onto a copy of the source; [Translation](../concepts/translation.md) explains why. The only structural text rewritten is `<num>`, folded into the target's script (such as Arabic-Indic digits to Latin).

![One work, /akn/xa/act/1992/7, the Legislation Act, 1992, with one URI for its whole life, and its expressions on a date axis. From 1992-05-02, the English expression /akn/xa/act/1992/7/eng@1992-05-02, as enacted, its URI fixed and still citable, in the file final.akn.xml. From 2012-01-01, the English expression /akn/xa/act/1992/7/eng@2012-01-01, the current text, amended by /akn/xa/act/2011/5, in the file consolidated-2012.akn.xml. Translated from it and stored as its child, the French expression /akn/xa/act/1992/7/fra@2012-01-01, with the same date, not authoritative and never current, in the file translated-fra.akn.xml. Each box is one expression, one text in one language from one date; its shaded strip is its file, a manifestation.](../figures/v5-family-tree.svg "One law, three dated texts in two languages, each saved as a file; identifiers and file names are from the worked example and its amendment and translation steps.")

One model call over the whole act first returns a binding glossary. Terms you supply (*pinned terms*) replace its entry for the same source term.

## Steps

### See what the model is given

Save this as `walk.py` and run it with `uv run python walk.py`.

```python
from pathlib import Path

from codify.akn import parse_akn
from codify.translate import walk_source

akn = Path("bundle/final.akn.xml").read_text(encoding="utf-8")
units, summary = walk_source(parse_akn(akn), akn)
print(summary)
for unit in units[1:4]:
    print(unit.akn_eid, "|", unit.heading, "|", unit.body_text[:40])
```

```text
WalkSummary(units=84, skipped_without_eid=0, skipped_kinds=())
part_I__sec_1 | Short title and commencement | 
part_I__sec_1__point_1 | None | This Act may be cited as the Legislation
part_I__sec_1__point_2 | None | This Act shall come into operation on su
```

Schedules are walked too. An element with no eId cannot be written back, so it is skipped, counted and flagged `translate_skipped_without_eid`.

### Pin your own terminology

Save this as `pinned.py`.

```python
from pathlib import Path

from codify.translate import PinnedTerm, apply_pinned_terms, notes_to_prompt, select_pinned_terms
from codify.translate.translate import source_prose

source = Path("bundle/final.akn.xml").read_text(encoding="utf-8")
glossary = [
    PinnedTerm(source="official text", target="texte officiel", origin="Ministry glossary"),
    PinnedTerm(source="statutory instrument", target="texte réglementaire", origin="Ministry glossary"),
]
chosen = select_pinned_terms(source_prose(source), glossary)
notes = apply_pinned_terms({"target_language": "fra"}, chosen)
print(notes_to_prompt(notes))
```

```text
TRANSLATION NOTES (binding). Target language: fra

Defined terms (use the target verbatim, everywhere, exclusively):
  - official text → texte officiel  []
```

"statutory instrument" is not in the Act, so it is dropped. The brackets hold the term's `note`.

<details>
<summary>How terms are matched</summary>

Matching ignores case, Unicode width and, for Arabic, diacritics. In Thai, Lao, Khmer, Burmese, Chinese, Japanese and Tibetan, which do not separate words with spaces, a term matches anywhere in the text.

</details>

### Translate

This step calls a model, so no output is shown. It returns a `TranslationResult`: `akn_xml`, `notes` (the glossary), `audit` (the measurements) and `flags` (each `{"location", "issue", "code"}`).

```python
import asyncio
import os
from pathlib import Path

from codify.core.llm import OpenAICompatibleClient
from codify.translate import PinnedTerm, translate_document
from codify.translate.quality_grade import translation_quality_grade


async def main() -> None:
    llm = OpenAICompatibleClient(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=os.environ["LLM_MODEL"],
    )
    source = Path("consolidated-2012.akn.xml").read_text(encoding="utf-8")
    result = await translate_document(
        source,
        target_language="fra",
        llm=llm,
        source_language="eng",
        pinned_terms=[PinnedTerm(source="official text", target="texte officiel")],
    )
    Path("translated-fra.akn.xml").write_text(result.akn_xml, encoding="utf-8")
    print(translation_quality_grade(result.audit, result.flags))
    for flag in result.flags:
        print(flag["code"], flag["location"])


asyncio.run(main())
```

<!-- not run: needs model -->

Leave `expression_date` out to keep the source's date. Passing it replaces the date in the expression and its URI, which then claims the text shows the law on that date.

<details>
<summary>What translate_document does, phase by phase</summary>

| Phase | What it does |
| --- | --- |
| Notes | One call over the whole act returns the glossary of defined terms, terms of art and named entities. If it fails or is incomplete, the run continues, flagged `notes_failed` or `notes_partial`. |
| Front matter | Preface, preamble and closing attestation go as numbered lines. A line with no letters, such as a gazette number, is copied. A line that fails three retries keeps its source text. |
| Bodies | Units go in batches of eight, grouped by chapter. Chapter groups run in parallel, up to 25 batches at once; within a group batches run in order, so earlier answers serve as examples for later ones. Numbers, dates, money and references become named placeholders before the model sees them and are restored afterwards, so a lost number is caught. |
| Repair | A unit that lost placeholders or came back short is sent again with the defect described, up to three passes. A line that still fails keeps its source text. |
| Write | Answers are written onto a copy of the source, as in the next step. |
| Audit | Deterministic checks of content volume, placeholder recall, money amounts, clauses per provision, glossary terms, drafting register and gazette headers left in (flags under [What can go wrong](#what-can-go-wrong)). |
| Judge | Optional (`judge_enabled=True`): a model scores up to 20 sampled provisions for coverage, fidelity, terminology and register, and its verdicts reach the grade. Repairs it asks for are counted in `audit["judge_repairs"]` but not written into `result.akn_xml`. |

</details>

### Write a translation from your own text

The deterministic writer also takes a translation made by people, but records every translation as `by="#codify"`; no parameter names another translator. Save this as `write_fra.py`.

```python
from pathlib import Path

from codify.akn import validate_akn
from codify.translate import TranslatedBlock
from codify.translate.write import apply_translation_to_akn

source = Path("consolidated-2012.akn.xml").read_text(encoding="utf-8")
blocks = [
    TranslatedBlock(eid="part_I__sec_1", heading="Titre abrégé et entrée en vigueur"),
    TranslatedBlock(
        eid="part_I__sec_1__point_1",
        lines=["La présente loi peut être citée sous le titre : Legislation Act, 1992."],
    ),
]
translated = apply_translation_to_akn(source, blocks, preface_lines=[], target_language="fra")
validate_akn(translated)
Path("translated-fra.akn.xml").write_text(translated, encoding="utf-8")
```

```bash
uv run python write_fra.py
uv run python -m codify.akn.structure_diff consolidated-2012.akn.xml translated-fra.akn.xml
```

```text
part_I__sec_1: heading 'Short title and commencement' -> 'Titre abrégé et entrée en vigueur'
1 structural difference(s)
```

`structure_diff` compares eIds, tags, nesting, numbers and headings, not paragraph text. In the identification block, `FRBRthis`, `FRBRuri` and `FRBRlanguage` move to `fra`, the date and its name (`2012-01-01`, `Amendment`) stay the source's, and `<FRBRtranslation>` is added:

![Across languages, a translation: the eId part_I__sec_1 names section 1 in two expressions of the Legislation Act, 1992. In the English source, /akn/xa/act/1992/7/eng@2012-01-01, it is headed Short title and commencement, with (1) This Act may be cited as the Legislation Act, 1992. In the French text, /akn/xa/act/1992/7/fra@2012-01-01, which is not authoritative, it is headed Titre abrégé et entrée en vigueur, with (1) La présente loi peut être citée sous le titre : Legislation Act, 1992. The French was written by hand for the example in write_fra.py, not by a model. The French text records its source in FRBRtranslation, with fromLanguage eng, href /akn/xa/act/1992/7/eng@2012-01-01, by #codify and authoritative false.](../figures/v7-translate.svg "One eId names the same provision in a source text and its translation; texts are from the worked example and its translation step, with the French written by hand.")

<details>
<summary>How blocks map onto the source</summary>

Units with no block keep their source text. `lines` maps by position onto the unit's non-empty paragraphs. A patched paragraph becomes plain text: its inline `<term>`, `<ref>` and `<def>` mark-up is dropped and an attached `<authorialNote>` kept. `preface_lines`, `preamble_lines` and `conclusions_lines` are either empty (left in the source language) or one line per slot; the worked example's preface has seven.

</details>

### Store it as a child version

Save this as `store_fra.py` and pass the 2012 text's `version_id`; your ids will differ.

```python
import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from codify.akn import parse_akn
from codify.settings import database_url
from codify.storage import get_version, latest_version_for_law, save_document, stale_translation_ids


async def main(source_version_id: uuid.UUID) -> None:
    translated = Path("translated-fra.akn.xml").read_text(encoding="utf-8")
    engine = create_async_engine(database_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        source = await get_version(session, source_version_id)
        await save_document(
            session,
            parse_akn(translated),
            jurisdiction_code="xa",
            law_title="Legislation Act, 1992",
            year=1992,
            number="7",
            akn_xml=translated,
            parent_version_id=source.id,
            source_akn_sha256=hashlib.sha256(source.akn_xml.encode("utf-8")).hexdigest(),
        )
        await session.commit()
        french = await latest_version_for_law(session, source.law_id, language="fra")
        default = await latest_version_for_law(session, source.law_id)
        print("fra:    ", french.expression_uri)
        print("default:", default.expression_uri)
        print("stale:  ", await stale_translation_ids(session, source.law_id))
    await engine.dispose()


asyncio.run(main(uuid.UUID(sys.argv[1])))
```

```bash
uv run python store_fra.py 57b16c65-3e0c-4399-bc87-b2348e36fe4f
```

```text
fra:     /akn/xa/act/1992/7/fra@2012-01-01
default: /akn/xa/act/1992/7/eng@2012-01-01
stale:   set()
```

`parent_version_id` makes the French text a child of the English. A child never names the law and is never current, so search without a language answers from the English. `source_akn_sha256` records the English text translated.

### Know when a translation is stale

Correct the English source. Save this as `stale.py`.

```python
import asyncio
import sys
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from codify.settings import database_url
from codify.storage import get_version, stale_translation_ids, update_repaired_akn
from codify.storage.repository import rederive_version_rows


async def main(source_version_id: uuid.UUID) -> None:
    engine = create_async_engine(database_url())
    async with AsyncSession(engine, expire_on_commit=False) as session:
        source = await get_version(session, source_version_id)
        print("before:", await stale_translation_ids(session, source.law_id))
        corrected = source.akn_xml.replace(
            "free of charge, at all reasonable times", "free of charge at all reasonable times"
        )
        await update_repaired_akn(session, source.id, corrected, "editor: punctuation in s. 8(2)")
        await rederive_version_rows(session, source.id, corrected)
        await session.commit()
        print("after: ", await stale_translation_ids(session, source.law_id))
    await engine.dispose()


asyncio.run(main(uuid.UUID(sys.argv[1])))
```

```bash
uv run python stale.py 57b16c65-3e0c-4399-bc87-b2348e36fe4f
```

```text
before: set()
after:  {UUID('94e67c2c-284f-4ab4-b344-699e2421f1ee')}
```

The id is the French version: PostgreSQL compares a hash of each parent's current AKN with the stored `source_akn_sha256`. A translation stored without that hash is never reported stale.

## Check your result

- `structure_diff` exits 0 on a match, 1 on a difference; a translation should differ only in headings.
- `translation_quality_grade` returns `A` (no check fired), `B` (warnings only), `C` (a hard failure) or `ungraded`. A run that lost half its volume can still grade `A`, as `translate_low_coverage` does not affect the grade, so read the flags too.
- *Regression pins* are phrases a reviewer requires in every later translation of a provision. `python -m codify.translate.regression_pins <file> <work> <language>` exits 0 when every pin holds, 1 on a violation, 2 when no pins are registered or the file will not parse.

<details>
<summary>What each grade means</summary>

| Grade | Meaning |
| --- | --- |
| `A` | No check fired. |
| `B` | Warnings only: glossary phase partial, mixed drafting register, a clause gap, a gazette header left in, a judge `warn`, or the judge enabled for a source language with no example pool (see Status and limits). |
| `C` | A hard failure: more than 5% of units kept their source text, glossary phase failed, source-script text in the body or headings, a lost or doubled money amount, a placeholder left undecoded, placeholder recall below 99%, a judge `fail`, or the judge failing on more than a third of its sample. |
| `ungraded` | The audit is empty, or lacks `numeric_slot_recall`, `notes_status` or `units_total`. |

</details>

<details>
<summary>Regression pins on the worked example</summary>

A pin names a work, a target language, an eId and the phrase. The shipped synthetic pins are for English, so this example checks an English text:

```bash
uv run python -m codify.translate.regression_pins bundle/final.akn.xml /akn/xa/act/1992/7 eng
```

```text
... [info     ] regression_pins_loaded         count=5
VIOLATED   sec_15__subsec_2__p_1 'imprisonment'
  sec_15__subsec_2__p_1 lost 'imprisonment' (eId absent, so either the provision was dropped or the pin in xa.jsonl is stale; the penalty verb left and returned across successive releases; without it the sentence type is unstated)
```

The pin is stale: this ingest gives that text the eId `part_V__sec_15__point_2__p_1`. With `translated-fra.akn.xml` and `fra` the command prints `no pins registered for /akn/xa/act/1992/7 in fra`.

Pins are read only from the installed package, at `codify/translate/pins/<jurisdiction>.jsonl`; to add your own, edit that file in a source checkout and install from it.

</details>

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| Warning `frbr_meta_patch_skipped reason=unknown_language`; the source's expression URI is kept | Unknown `target_language` (`fra`, `fr` and `French` work; `français` does not). Storing writes nothing and returns the source's `version_id`. | Use a known code or name. |
| Warning `frbr_translation_not_declared reason=same_language` | Target equals source language. The URI is kept and no `<FRBRtranslation>` written, so storing writes nothing, or with `expression_date` adds a new English expression. | Pick another target. |
| `ValueError: zip() argument 2 is shorter than argument 1` | A front-matter line list is shorter than the document's slots. | Pass an empty list or one line per slot. |
| Flag `notes_failed` or `notes_partial` | The glossary call failed (grade `C`) or was incomplete (`B` at best). | Pin key terms; translate again. |
| Flag `translate_low_coverage` | The translation is under half the source's volume. | Look for a missing section. |
| Flag `translate_sentinel_unrepaired` | A number, date or reference still missing after three repair passes. | Fix the provision at `location`. |
| Flag `translate_clause_gap` | A provision has under 80% of the source's sentences. | Review the provision at `location`. |
| Flag `translate_preface_line_fallback_source` (or `preamble`, `conclusions`, `body`) | A line failed every attempt and kept its source text. | Supply it through the writer. |
| The French text answers a French request though the English has moved on | A language match outranks a later date. | Check `stale_translation_ids`; translate the current version. |

## Status and limits

| Capability or limit | Status |
| --- | --- |
| Translate a document with a model | **Library only**: `codify.translate.translate_document` |
| Write a translation from supplied text | **Library only**: `codify.translate.write.apply_translation_to_akn` |
| Pin terminology | **Library only**: `PinnedTerm`, `select_pinned_terms`, `apply_pinned_terms` |
| Grade a translation | **Library only**: `codify.translate.quality_grade.translation_quality_grade` |
| Per-provision findings for review | **Library only**: `codify.translate.delivery.delivery_findings` |
| Store a translation and detect staleness | **Library only**: `save_document` with `parent_version_id` and `source_akn_sha256`; `stale_translation_ids` |
| Check regression pins | **Available**: `python -m codify.translate.regression_pins` |
| Compare structure with the source | **Available**: `python -m codify.akn.structure_diff` |
| Read a stored translation | **Available**: `GET /versions/{version_id}`, MCP `get_version`, `?v=` in the web reader |
| Prefer a language in search | **Available**: `codify search --language` |
| Run a translation by command, route or screen | **Not in the core** |
| Record a translation run, stamp its grade on the version, refuse on a lost pin | **Not in the core**. The `translation_runs` table and the version's `translation_quality_grade` column exist; no core code fills them. |
| Untranslated-text check | Limit: Arabic or Hebrew letters only, English and Hebrew targets only, so Latin-script text left untranslated goes undetected. A test model returning English unchanged for a French target graded `B`. |
| Drafting-register check and *example pool* (a chapter's earlier answers fed to its later batches) | Limit: register check for English, Arabic and Hebrew targets; example pool for English, Arabic and Hebrew sources. |
| Version order | Limit: `GET /laws/{law_id}/versions` sorts newest expression date first, then latest ingest, so the French text, stored last with the same date, lists before the current English, and the web reader opens it when no `?v=` is given. |

## Next

[Search](search.md)
