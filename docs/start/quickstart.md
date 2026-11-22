---
title: Quickstart
description: Take a short act from plain text to Akoma Ntoso, check it, store it and read it over HTTP, with no model and no API key.
sidebar:
  order: 2
---

You end with a short act in [Akoma Ntoso](../concepts/akoma-ntoso.md) XML, checked, stored in PostgreSQL and served over HTTP, with no model and no API key.

## Before you start

- A source checkout installed with the `serve` extra, as in [Install](install.md).
- The Compose PostgreSQL service, migrated ([Install](install.md#start-the-database)).
- A terminal at the repository root.

The example is an invented act of Atlantis (`xa`), a fictional jurisdiction that ships for testing and teaching.

## The idea in brief

Rules find a law's structure; a model only fills the text. A jurisdiction config declares the words that open a Part or a Section, a scan finds those markers (anchors), and each anchor becomes a slot with a stable element identifier (`eId`) such as `part_I__sec_2`. The identifier comes from a fixed rule because a citation must resolve to the same element on every run, and model output varies. The full pipeline also uses a model to read scans, read the title, date and number, and arrange each provision's text; clean text with a year and number you supply needs none of that.

## Steps

### Write the source text

Create a folder (delete it when you finish) holding a plain-text act.

```bash
mkdir quickstart
cat > quickstart/coastal-lights.txt <<'EOF'
No. 9 of 2015

Coastal Lights Act, 2015

An Act to provide for the maintenance of coastal lights and for connected purposes.

[Assented to 3rd March, 2015]

PART I
PRELIMINARY

Section 1
This Act may be cited as the Coastal Lights Act.

Section 2
In this Act, unless the context otherwise requires, "light" means a lighthouse,
beacon or buoy maintained under this Act, and "Keeper" means the Keeper of Lights
appointed under section 3.

PART II
THE KEEPER OF LIGHTS

Section 3
The Minister shall appoint a Keeper of Lights, who shall hold office for a term of
five years and may be reappointed.

Section 4
The Keeper shall maintain a register of every light, and shall publish the register
in the Gazette not later than the thirty-first day of March in each year.

Section 5
A person who extinguishes, obscures or removes a light without the written consent
of the Keeper commits an offence and is liable on conviction to a fine.
EOF
```

### Scan it

`scan-corpus` scans every `.txt` and `.pdf` file under a folder and prints a JSON summary to standard output (logs to standard error). It writes nothing.

```bash
uv run codify scan-corpus quickstart --jurisdiction xa
```

```json
{
  "corpus": {
    "jurisdiction": "xa",
    "root": "quickstart",
    "include_derived": false
  },
  "documents": 1,
  ...
  "anchors": 7,
  "basic_units": 5,
  ...
  "marker_lines": 5,
  "marker_lines_claimed": 5,
  "capture_rate": 1.0,
  "mangled_marker_lines": 0,
  "honest_marker_lines": 5,
  "honest_capture_rate": 1.0,
  ...
  "container_recall": {
    "part": [
      2,
      2
    ]
  },
  "undeclared_grouping_lines": {
    "section": 5
  },
  ...
  "anchors_by_kind": {
    "part": 2,
    "section": 5
  },
  ...
  "coverage_states": {
    "measured": 1
  }
}
```

Judge the rules by `honest_capture_rate`. Below 1, they missed lines that look like markers: fix them before any model call.

<details>
<summary>What each field means</summary>

| Field | Meaning here |
| --- | --- |
| `anchors` | Two Parts and five Sections. |
| `basic_units` | Anchors of the basic unit, for an `xa` act the section. |
| `marker_lines`, `marker_lines_claimed` | Lines that look like a section marker, and how many the scan claimed. |
| `capture_rate` | Claimed lines divided by marker lines; `null` when there are none. It counts only lines the config recognises, so it can read 1.0 while garbled markers go unclaimed. |
| `mangled_marker_lines`, `honest_capture_rate` | Marker-shaped lines whose keyword matches no alias in the config, such as a misspelling. The honest rate counts them as lines the rules should have claimed. |
| `container_recall` | Per grouping level, headings present and headings found. |
| `undeclared_grouping_lines` | Lines opening a grouping level another document type declares and this one does not. An `xa` code groups Articles into Sections, so each `Section` line counts; in an act a Section is the basic unit, so ignore it. |

</details>

### Structure it

`text_to_bluebell_verbatim` copies the text between one anchor and the next into each slot, giving [Bluebell](../concepts/bluebell.md), a plain-text notation for legal structure; `parse_to_akn` turns Bluebell into XML. Neither calls a model. Save this as `quickstart/structure.py`:

```python title="quickstart/structure.py"
from pathlib import Path

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.verbatim import text_to_bluebell_verbatim

text = Path("quickstart/coastal-lights.txt").read_text(encoding="utf-8")

bluebell, summary, anchors = text_to_bluebell_verbatim(text, country="xa", doctype="act")
print(summary)
print(bluebell)

akn_xml = parse_to_akn(bluebell, "xa", doctype="act", date="2015", number="9", language="eng")
Path("quickstart/coastal-lights.akn.xml").write_text(akn_xml, encoding="utf-8")
```

```bash
uv run python quickstart/structure.py
```

```text
2026-11-14 00:50:00 [info     ] container_heading_captured     heading=PRELIMINARY kind=part line=7 number=I
2026-11-14 00:50:00 [info     ] container_heading_captured     heading='THE KEEPER OF LIGHTS' kind=part line=18 number=II
2026-11-14 00:50:00 [info     ] toc_check_skipped              reason=no_cover_listing
{'part': 2, 'section': 5}
PREFACE
  No. 9 of 2015
  Coastal Lights Act, 2015
  LONGTITLE An Act to provide for the maintenance of coastal lights and for connected purposes.
  [Assented to 3rd March, 2015]

BODY
  PART I - PRELIMINARY

    SECTION 1
      This Act may be cited as the Coastal Lights Act.

    SECTION 2
      In this Act, unless the context otherwise requires, "light" means a lighthouse, beacon or buoy maintained under this Act, and "Keeper" means the Keeper of Lights appointed under section 3.
...
```

The dictionary is the anchor count by kind; wrapped lines are joined. The XML file's identity is a [FRBR](../concepts/frbr.md) URI built from the country, document type, year and number you passed, and each `eId` is the element's path:

```xml
<FRBRWork>
  <FRBRthis value="/akn/xa/act/2015/9"/>
  <FRBRuri value="/akn/xa/act/2015/9"/>
  ...
</FRBRWork>
...
<part eId="part_I">
  <num>I</num>
  <heading>PRELIMINARY</heading>
  <section eId="part_I__sec_1">
    <num>1</num>
    <content>
      <p eId="part_I__sec_1__p_1">This Act may be cited as the Coastal Lights Act.</p>
    </content>
  </section>
  ...
```

This route skips the pipeline's enrichment passes, so the expression is undated.

<details>
<summary>What the route leaves out</summary>

The document has no title metadata (`FRBRalias` reads `Untitled`), no enacting formula or reference mark-up, and no expression date: the expression URI is `/akn/xa/act/2015/9/eng` and its `FRBRdate` is the day you ran the script. The full pipeline dates the expression from the document (`/akn/xa/act/1992/7/eng@1992-05-02` in [First ingest](first-ingest.md)); [FRBR](../concepts/frbr.md) explains works and expressions.

</details>

### Validate it

`codify.akn.validate_akn` checks the XML against the OASIS schema and raises on failure; `codify.pipeline.enrich.validator.validate_akn` runs the [structural checks](../reference/validation-checks.md) and returns findings ([two kinds of validation](../concepts/akoma-ntoso.md#two-kinds-of-validation)); `structural_quality_grade` folds them into `clean`, `warning`, `blocking` or `ungraded`. Save this as `quickstart/validate.py`:

```python title="quickstart/validate.py"
from pathlib import Path

from codify.akn import validate_akn as validate_schema
from codify.pipeline.enrich.validator import validate_akn as validate_structure
from codify.quality.structural_quality_grade import structural_quality_grade

akn_xml = Path("quickstart/coastal-lights.akn.xml").read_text(encoding="utf-8")

validate_schema(akn_xml)
print("schema: valid")

findings = validate_structure(akn_xml, expected_anchor_summary={"part": 2, "section": 5})
for finding in findings:
    print(finding)
print(structural_quality_grade(findings))
```

```bash
uv run python quickstart/validate.py
```

```text
schema: valid
StructuralGradeResult(grade='clean', reason='', rubric_version=1)
```

`expected_anchor_summary` is the count `structure.py` printed, so a unit lost on the way becomes a finding.

<details>
<summary>A failing check, and how findings set the grade</summary>

With `{"part": 2, "section": 6}`:

```text
schema: valid
... [info     ] validation_issues_found        count=1
{'check': 'anchor_count_mismatch', 'severity': 'error', 'kind': 'section', 'expected': 6, 'got': 5, 'message': "Pre-flight scan found 6 'section' anchors in the raw text but the assembled AKN contains only 5. ..."}
StructuralGradeResult(grade='blocking', reason="anchor_count_mismatch: Pre-flight scan found 6 'section' anchors ...", rubric_version=1)
```

An `error` finding makes the grade `blocking`, a `warning` makes it `warning`, and `info` findings leave it `clean`.

</details>

### Load it

`load` writes the law, version and provisions into PostgreSQL. A bare XML file has no manifest, so you name the jurisdiction and a title.

```bash
uv run codify load quickstart/coastal-lights.akn.xml --jurisdiction xa --title "Coastal Lights Act, 2015"
```

```json
{"version_id": "d149f6c8-c598-4511-98b5-251c5b06de87", "work_uri": "/akn/xa/act/2015/9", "embedded": 0}
```

Your `version_id` differs. `embedded` is 0: embeddings need a model endpoint and were not asked for. `load` runs no validator and stores no grade.

:::caution
A version is keyed on its expression URI. Loading a corrected file under a stored URI returns the old `version_id`, exits 0 and keeps the old text without a warning. No command replaces a stored version yet; use the library's repair path in [Validate and repair](../guide/validate-and-repair.md).
:::

<details>
<summary>See it happen</summary>

This URI has no date, so every corrected load hits it. Change "five years" to "seven years" in the XML and load it again: the same `version_id` comes back and the database still holds "five years".

</details>

### Serve it

Start the HTTP server and leave it running.

```bash background
uv run codify serve
```

In a second terminal, list the laws of `xa`.

```bash
curl -s "localhost:8000/laws?jurisdiction=xa"
```

```json
{"items":[{"id":"313d70ac-8a71-4d64-a249-c4f7b0b1730a","jurisdiction_code":"xa","title":"An Act to provide for the maintenance of coastal lights and for connected purposes.","short_title":"Coastal Lights Act","doctype":"act","status":"enacted","year":2015,"number":"9","frbr_work_uri":"/akn/xa/act/2015/9","latest_expression_date":"2026-11-14", ...}],"limit":50,"offset":0}
```

The stored `title` is the preface's long title; `--title` is only a fallback. `latest_expression_date` is the script's run day, because the expression is undated.

`/versions/{id}` returns the metadata and XML; `/versions/{id}/document` returns the tree of sections and provisions a reader needs. Use the `version_id` that `load` printed.

```bash
curl -s localhost:8000/versions/d149f6c8-c598-4511-98b5-251c5b06de87/document | python3 -m json.tool
```

```json
{
  "version_id": "d149f6c8-c598-4511-98b5-251c5b06de87",
  "law_id": "313d70ac-8a71-4d64-a249-c4f7b0b1730a",
  ...
  "frbr_work_uri": "/akn/xa/act/2015/9",
  "frbr_expression_uri": "/akn/xa/act/2015/9/eng",
  "language": "eng",
  "expression_date": "2026-11-14",
  ...
  "sections": [
    {
      "id": "19282a79-7188-4767-9faf-92bacc408b18",
      "akn_eid": "part_I",
      "akn_type": "part",
      "title": "PRELIMINARY",
      "position": 0,
      "num": "I",
      "sections": [
        {
          ...
          "akn_eid": "part_I__sec_1",
          "akn_type": "section",
          ...
```

The interactive API description is at `http://localhost:8000/docs`.

:::caution
The server has no authentication and listens on `127.0.0.1` by default. Keep it there, or put it behind a proxy that authenticates.
:::

## Check your result

- `scan-corpus` reported `honest_capture_rate` 1.0 and the anchor counts the text has.
- `validate.py` printed `schema: valid` and a `clean` grade.
- `GET /laws?jurisdiction=xa` lists work URI `/akn/xa/act/2015/9`.
- The document response's `eId`s (`part_I`, `part_I__sec_1`) match the XML.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `not a directory: <folder>`, exit 2 | `scan-corpus` takes a folder. | Pass the folder. |
| `no scannable sources under data/fixtures/synthetic/xa (unreadable 0, no text layer 3, derived exports 0)`, exit 1 | Only scanned PDFs; `scan-corpus` reads text and PDF text layers, no OCR. | Ingest scans with a model ([First ingest](first-ingest.md)). |
| Traceback ending `JurisdictionDataMissing: no jurisdiction config for 'zz' in .../data/jurisdictions; write .../data/jurisdictions/zz/config.json: ...` | No config for that code. | Check the code, or [write the config](../guide/write-a-jurisdiction-config.md). |
| `ValueError: no structural anchors found — use the LLM structuring lane` | No declared marker in the text. The "lane" is the model route `ingest-one` runs. | Check markers with `scan-corpus`; if none, use [First ingest](first-ingest.md). |
| `anchor_count_mismatch` | The XML holds fewer units than the scan counted. | Compare counts unit by unit. |
| `no jurisdiction in the bundle's manifest; pass --jurisdiction` or `no title in the bundle's manifest; pass --title` | Bare XML file without the flags. | Pass both flags. |
| Traceback from `load` ending `ConnectionRefusedError: [Errno 61] Connect call failed ...` | PostgreSQL unreachable at `POSTGRES_URL`; the error number varies by system. | Start the database and check the URL ([Install](install.md#start-the-database)). |
| `{"detail":"set EMBEDDING_BASE_URL (or LLM_BASE_URL) to an embeddings endpoint"}`, status 503 from `/search` | No embeddings endpoint. With `.env.example` copied to `.env`, `LLM_BASE_URL` is set and search calls it. | Expected here; see [Search](../guide/search.md). |

## Status and limits

| Capability | Status |
| --- | --- |
| Anchor scan over a folder (`scan-corpus`) | **Available** |
| Structuring clean text without a model (`text_to_bluebell_verbatim`, `parse_to_akn`); copies source unchanged, no enrichment; scans need the model route | **Library only** |
| Schema and structural check of an XML file | **Library only**; `ingest-one` runs the structural check within a run |
| Storing a document (`load`) | **Available** |
| Reading stored laws over HTTP (`serve`) | **Available** |
| Search | **Available**, with an embeddings endpoint |
| Authentication on the server | **Not in the core** |

## Next

[Install](install.md): the install options and database setup in full.
