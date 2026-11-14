---
title: Write a jurisdiction config
description: Write the config.json that describes your legal system to Codify, check it without a model, and get it ready for a first ingest.
sidebar:
  order: 2
---

You end with a `config.json` that loads, passes its checks and finds the structure of your instruments without a model, ready for a first ingest.

## Before you start

- Decisions from [Model your legal system](model-your-legal-system.md).
- A synced checkout ([Install](../start/install.md)); run commands from its root.
- One real instrument as a `.txt` file or a PDF with a text layer. A scanned PDF needs OCR first; the `source.txt` in a bundle from [First ingest](../start/first-ingest.md) is that text.

## The idea in brief

A jurisdiction is one file and no code: `data/jurisdictions/<code>/config.json`. Every command that names a jurisdiction validates it against `JurisdictionConfig`, the Pydantic schema in [`codify/jurisdictions.py`](../../codify/jurisdictions.py); the [reference](../reference/jurisdiction-config.md) lists every field. Most blocks keep an unknown key and ignore it, so a misspelt field loads without error:

```python
from codify.jurisdictions import JurisdictionConfig

raw = {"code": "xa", "name": "Atlantis", "tradition": ["common_law"], "languages": ["eng"],
       "document_classes": {"act": {"label": "Act", "basic_unti": "section"}}}
print(JurisdictionConfig.model_validate(raw).document_classes["act"].model_extra)
```

```text
{'basic_unti': 'section'}
```

<details>
<summary>Blocks that reject unknown keys</summary>

The schema is strict where the pipeline makes decisions: `frbr.calendar_conversion`, `frbr.title_identity`, `numbering.series_citations`, the entries of `core_tlcs`, `placeholders`, `source_adapters` (with their `discovery` block) and `akn_sources`, `segmentation`, and `structuring.classification_rules`. Everything else, including the top level, document classes, hierarchy levels and the `frbr`, `structuring` and `display` blocks, keeps unknown keys.

</details>

The tests and the scan below catch what loading misses.

## Steps

### Choose the code and the location

The code is the directory name, in lower case, and every command takes it as `--jurisdiction`. It is also the country segment of your work URIs unless `frbr.country_code` says otherwise. Pick one no directory under `data/jurisdictions/` already uses.

In a checkout, create `data/jurisdictions/<code>/config.json`; an edit is live at once. With an installed package, keep your configs in a directory of your own and point `CODIFY_DATA_ROOT` at it; `jurisdictions/<code>/config.json` alone is enough to load the config and scan with it.

<details>
<summary>Where Codify looks for jurisdiction data</summary>

In this order (`data_dir` in [`codify/data_paths.py`](../../codify/data_paths.py)):

1. `CODIFY_DATA_ROOT`, if set: an absolute path to an existing directory holding `jurisdictions/`. It replaces the bundled data entirely, so only the configs under it exist. The comparison against EU law also needs a `frameworks/` directory there, and fails with `CODIFY_DATA_ROOT is missing the 'frameworks' directory` without one; copy it from the package's `data/`.
2. A `data/` directory inside the installed package. A wheel carries the configs flagged `synthetic` or `public_reference` there.
3. The checkout's own `data/`.

</details>

### Write the smallest config that ingests

`code`, `name`, `tradition` and `languages` are required; to ingest, a config also needs a document class with its hierarchy. This is the Atlantis act class cut down to that:

```json
{
  "code": "xa",
  "name": "Atlantis",
  "tradition": ["common_law"],
  "languages": ["eng"],
  "authoritative_language": "eng",
  "synthetic": true,
  "default_document_class": "act",
  "document_classes": {
    "act": {
      "label": "Act of the Assembly",
      "akn_element": "act",
      "basic_unit": "section",
      "hierarchy": [
        { "local_term": "Part", "akn_element": "part", "level": "higher", "bluebell_keyword": "PART" },
        { "local_term": "Section (Sec.)", "akn_element": "section", "level": "basic", "bluebell_keyword": "SECTION" },
        { "local_term": "Subsection", "akn_element": "subsection", "level": "subdivision", "bluebell_keyword": "SUBSECTION" }
      ]
    }
  },
  "display": { "heading_type": "marginal_note", "script": "latin" }
}
```

| Field | What it does |
| --- | --- |
| no `frbr` block | The URI country segment is the code: an `act` gets `/akn/xa/act/{year}/{number}`. Add `frbr.country_code` or `frbr.uri_patterns` when that differs ([Model your legal system](model-your-legal-system.md#design-the-work-uri)) |
| `local_term` | The printed word. The scanner matches it as written, in title case and in capitals, and matches the `bluebell_keyword` too |
| `bluebell_keyword` | The level's name in [Bluebell](../concepts/bluebell.md), the text form the structurer writes (`PART`, `CHAPTER`, `SECTION`, `ARTICLE` and so on). Any other value is refused at load |
| `display.heading_type: marginal_note` | Sections print as a heading line then a bare number (`1.`). The scanner reads that layout only when declared |

<details>
<summary>Parentheses in <code>local_term</code></summary>

A form in parentheses in the same script, starting with a capital, also matches: `Section (Sec.)` matches `Sec.`. So an English gloss such as `Título (Title)` makes `Title` a heading word too; put only printed forms in parentheses.

</details>

<details>
<summary>What the full Atlantis config adds</summary>

The full config (`data/jurisdictions/xa/config.json`) adds what a real corpus needs:

| Block | What it changes |
| --- | --- |
| `structuring.classification_rules` | Which class each document is given at ingest |
| `structuring.long_title_lead_ins`, `citation_lead_ins`, `citation_subjects` | How the long title and the short title are found |
| `enacting_formulae`, `enacting_formula_markers`, `opening_material_terminators` | How the enacting formula and the opening material before the body are read |
| `numbering.series_citations` | Citations by series number alone |
| `core_tlcs` | The top-level classes ([TLCs](../concepts/akoma-ntoso.md)): the people, bodies, roles and publications documents refer to, such as the Atlantis Gazette |
| `legal_eras` | Periods with their own authority and calendar |
| `furniture_line_patterns`, `furniture_inline_patterns` | Gazette mastheads and stamps to remove from the text |

`min_anchor_coverage` sets the share of printed markers the scan must capture before an ingest may continue. Atlantis leaves it at the default, 0.8.

</details>

### Load it and run the config tests

```bash
uv run python -c "from codify.jurisdictions import load_config; print(load_config('xa').name)"
```

```text
Atlantis
```

A config that breaks a rule fails here and in every command that reads it.

<details>
<summary>Example: two basic levels</summary>

With the Chapter level of the full Atlantis config also marked `basic`, `scan-corpus` stops with:

```text
pydantic_core._pydantic_core.ValidationError: 1 validation error for JurisdictionConfig
  Value error, document class 'act': 2 hierarchy entries with level=basic (expected at most 1): ['Chapter', 'Section (Sec.)'] [type=value_error, ...]
```

</details>

Run the tests over every config in the tree (about a minute); `-k <code>` would skip the checks that span configs:

```bash
uv run pytest tests/test_jurisdictions.py -q
```

```text
...
... passed, ... skipped in ...
```

| Flag | Meaning | What the suite does |
| --- | --- | --- |
| `synthetic: true` | Fictional, like Atlantis | Parses it, checks it against its directory name, keeps it out of `registry.json` |
| `public_reference: true` | Real, published in the open core | Lists it in `registry.json`, checks further invariants (below) |
| neither | Private | `test_corpus_is_present` fails: every config in the tree needs a flag |

The suite expects the shipped reference configs, so check a private config under `CODIFY_DATA_ROOT` with `load_config` and the scan below instead.

<details>
<summary>Publishing a <code>public_reference</code> config</summary>

The further invariants include: URI pattern keys name declared classes, patterns use only `{year}` and `{number}`, every key is one the schema declares, and every enacting formula can be selected.

After adding or changing one, regenerate the derived registry and check it; never edit `registry.json` by hand. A newly flagged config also fails `test_the_flagged_set_is_deliberate`, which pins the size of the flagged set; update the pinned count in the same change ([Testing](../project/testing.md)):

```bash
uv run python scripts/build_registry.py
uv run python scripts/build_registry.py --check
```

```text
registry.json matches derived (... entries).
```

When the file is out of date, `--check` exits with code 2 and prints `DRIFT: registry.json does not match derived version.`

</details>

### Find the structure without a model

`scan-corpus` runs the anchor scan, the rule-based pass that finds every part and section, over each `.txt` and `.pdf` file in a directory, with no model or database:

```bash
mkdir -p ~/codify-texts
cp bundle/source.txt ~/codify-texts/legislation-act-1992.txt
uv run codify scan-corpus ~/codify-texts --jurisdiction xa
```

Logs go to standard error and a JSON summary to standard output. Run with the smallest config above, and again with its `display` block removed:

![The first part of the worked example's source.txt: the line PART I — PRELIMINARY, then the heading Short title and commencement on its own line followed by a bare 1. and the subsection (1) This Act may be cited as ..., then the heading Interpretation followed by 2. and (1) In this Act, unless the .... With the marginal-note layout declared, the scanner finds PART I as part_I through the regex pass, and finds Short title and commencement as part_I__sec_1 and Interpretation as part_I__sec_2 through the marginal_note_units pass. Over the whole act that is 26 anchors: 6 parts and 20 sections. With the display block removed, the config still loads and part_I is still found, but neither heading gives an anchor: 6 anchors, 6 parts and 0 sections.](../figures/v14-marginal-notes.svg "From scan-corpus run twice on the worked example's text: declaring the marginal-note layout is what lets the scanner find the sections.")

The text has Parts I to VI and sections 1 to 20, as with the full Atlantis config. In the summary, `anchors_by_kind` and `anchors_by_pass` give the counts above, `documents` is 1 and `basic_units` is 20. Without the `display` block, `basic_units` is 0 and `documents_without_basic_unit` is 1.

Compare `anchors_by_kind` with a count made by hand from one printed instrument of every class and period. [Acquire sources](acquire-sources.md) explains the rest of the summary.

### Ingest one document

With the scan counts right, run the full pipeline on one document with a model endpoint ([First ingest](../start/first-ingest.md)). The bundle's `manifest.json` records the config and its SHA-256 digest, which tells runs on different config versions apart:

```json
"jurisdiction_config": {
  "code": "xa",
  "path": "data/jurisdictions/xa/config.json",
  "sha256": "f6941da7c52c53cfac95fdf17f62d151bb0e1297bedda46720ca947ff9ab76de"
},
```

## Check your result

- `load_config("<code>")` returns your config.
- `pytest tests/test_jurisdictions.py` passes, and for a `public_reference` config so does `scripts/build_registry.py --check`.
- `scan-corpus` finds every container and basic unit you counted by hand, for each class and period.
- After an ingest, the bundle's `validator.json` carries no `structure_halted` finding ([Validate and repair](validate-and-repair.md)).

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `no config for jurisdiction 'zz': looked in .../data/jurisdictions/zz/config.json`, exit code 2, from `ingest-one` | No file at that path | Check the code, the directory and `CODIFY_DATA_ROOT` |
| A traceback ending `codify.jurisdictions.JurisdictionDataMissing: no jurisdiction config for 'zz' in .../data/jurisdictions; write .../data/jurisdictions/zz/config.json: ...`, exit code 1 | The same, from `scan-corpus` or the library | As above |
| `CODIFY_DATA_ROOT must name an existing absolute directory` | A relative path or a missing directory | Use an absolute, existing path |
| `CODIFY_DATA_ROOT is missing the 'jurisdictions' directory` | No `jurisdictions/` inside it | Create it |
| `Input should be 'BOOK', 'PART', ...` | A `bluebell_keyword` Bluebell does not have | Use a Bluebell keyword |
| `jurisdiction 'xa' declares no hierarchy for doctype 'act'; declared: [...]` | The class has an empty `hierarchy` | Add its levels |
| `scan-corpus` counts no basic units | No `local_term` or `bluebell_keyword` matches the printed word, or the layout needs `display.heading_type` | Fix the term or declare the layout |
| A `structure_halted` finding with gate `coverage_below_floor`: `captured N of M section markers, P% against a 80% floor` | The scan captured fewer markers than `min_anchor_coverage` | Fix the hierarchy until the scan counts match |
| `AnchorCoverageError: anchor coverage 0% below floor 80%: ...`; the ingest stops at the structure stage | Markers were found and none was recognised | As above |
| A field you set has no effect | A misspelt key in a block that keeps unknown keys | Check it against the [reference](../reference/jurisdiction-config.md) |

## Status and limits

| Capability | Status |
| --- | --- |
| Validating a config on every load | **Available** |
| Checking a config against the project's invariants (`pytest tests/test_jurisdictions.py`) | **Available** in a source checkout, for configs flagged `synthetic` or `public_reference` |
| A command that checks one config on its own | **Not in the core**: call `load_config` |
| Scanning structure without a model (`scan-corpus`) | **Available** |
| Configs kept outside the package (`CODIFY_DATA_ROOT`) | **Available** |
| Reading a config over HTTP (`GET /jurisdictions/{code}` on `codify serve`) | **Available** |
| Editing a config through the HTTP server or the web app | **Not in the core** |

:::note[On the hosted Platform]
The hosted Platform offers calibration: adapting Codify to your jurisdiction's documents and legal conventions.
:::

## Next

[Acquire sources](acquire-sources.md)
