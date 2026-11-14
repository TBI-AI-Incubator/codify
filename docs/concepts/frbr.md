---
title: FRBR and document identity
description: How Codify names a law, each version of its text and each file, using FRBR URIs from the Akoma Ntoso naming convention.
sidebar:
  order: 3
---

You end with the rules Codify uses to name a law, each text of it and each file: FRBR (Functional Requirements for Bibliographic Records) URIs, as the [Akoma Ntoso](akoma-ntoso.md) naming convention adopts them.

## Work, expression, manifestation

A law amended, consolidated or translated is still the same law. FRBR separates the law from its texts so each can be cited alone.

| Level | What it is |
| --- | --- |
| Work | The law as an abstract thing, with no text of its own. |
| Expression | One text of the work: a language, as it stood on a date. |
| Manifestation | One file of an expression. |

This makes point-in-time law possible: "section 12 of the Legislation Act" names the work, and a court applying it needs the expression in force on the date that matters, and asking what section 12 said in 2012 names an expression. A translation is another expression of the same work, never a second law.

![One work, /akn/xa/act/1992/7, the Legislation Act, 1992, with one URI for its whole life, and its expressions on a date axis. From 1992-05-02, the English expression /akn/xa/act/1992/7/eng@1992-05-02, as enacted, its URI fixed and still citable, in the file final.akn.xml. From 2012-01-01, the English expression /akn/xa/act/1992/7/eng@2012-01-01, the current text, amended by /akn/xa/act/2011/5, in the file consolidated-2012.akn.xml. Translated from it and stored as its child, the French expression /akn/xa/act/1992/7/fra@2012-01-01, with the same date, not authoritative and never current, in the file translated-fra.akn.xml. Each box is one expression, one text in one language from one date; its shaded strip is its file, a manifestation.](../figures/v5-family-tree.svg "One law, three dated texts in two languages, each saved as a file; identifiers and file names are from the worked example and its amendment and translation steps.")

## The URI grammar

```text
Work           /akn/{country}/{doctype}/{year}/{number}
               /akn/{country}/{doctype}/{subtype}/{year}/{number}
Expression     {work}/{language}@{date}
Manifestation  {expression}.{format}
Component      {work or expression}/!{component}
```

| Segment | Meaning | Rule in Codify |
| --- | --- | --- |
| `country` | Jurisdiction code, usually two letters, optionally with `-{locality}` for a sub-national issuer | From `frbr.country_code` in the config, which can differ from the jurisdiction code. A locality is lower-case letters and digits. |
| `doctype` | The AKN document type | `act`, or `doc` or `statement` for a class that declares one; a configured pattern can write another. |
| `subtype` | Your own document class, where it is not a plain act | The class's URI pattern, or by default the class name. |
| `year` | Date the work was made | A four-digit Gregorian year only (see below). |
| `number` | The number the work is cited by | See [where the number comes from](#where-the-year-and-number-come-from). |
| `language` | Three-letter ISO 639-3 code | `eng`, `fra`, `ara`. |
| `date` | Date the expression's text applies from | `YYYY-MM-DD`. |
| `component` | A part of the document with its own identity | `main` for the body, `schedule_1` for the first schedule. |

<details><summary>The year segment</summary>

The convention allows a year or a full date; Codify writes only the four-digit Gregorian year, and `is_citable_work_uri` treats any other shape, a full date included, as uncitable. The config's `frbr.calendar_conversion` converts other calendars first; a year it cannot convert is logged as `year_calendar_unconverted`.

</details>

The worked example's identification block carries all three levels:

```xml
<FRBRWork>
  <FRBRthis value="/akn/xa/act/1992/7/!main"/>
  <FRBRuri value="/akn/xa/act/1992/7"/>
  <FRBRalias value="Legislation Act, 1992" name="title"/>
  <FRBRdate date="1992-05-02" name="Generation"/>
  <FRBRauthor href=""/>
  <FRBRcountry value="xa"/>
  <FRBRnumber value="7"/>
</FRBRWork>
<FRBRExpression>
  <FRBRthis value="/akn/xa/act/1992/7/eng@1992-05-02/!main"/>
  <FRBRuri value="/akn/xa/act/1992/7/eng@1992-05-02"/>
  <FRBRdate date="1992-05-02" name="Original"/>
  <FRBRauthor href=""/>
  <FRBRlanguage language="eng"/>
</FRBRExpression>
<FRBRManifestation>
  <FRBRthis value="/akn/xa/act/1992/7/eng@1992-05-02/!main"/>
  <FRBRuri value="/akn/xa/act/1992/7/eng@1992-05-02"/>
  <FRBRdate date="2026-11-14" name="Generation"/>
  <FRBRauthor href=""/>
</FRBRManifestation>
```

`FRBRuri` names the whole document, `FRBRthis` this component. The manifestation date is the day the file was produced; on this lane its URI has no format suffix.

## How a work URI is built

Ingest calls `codify.frbr.build_frbr_work_uri(country, doctype, year, number, locality=None)` with the document class it resolved; so can you.

```python
from codify.frbr import build_frbr_expression_uri, build_frbr_work_uri

work = build_frbr_work_uri("xa", "act", 1992, "7")
print(work)
print(build_frbr_expression_uri(work, "eng", "1992-05-02"))
print(build_frbr_work_uri("xa", "mint_circular", 2004, "12"))
print(build_frbr_work_uri("xa", "code", 1998, "3"))
print(build_frbr_work_uri("xa", "guidance", 2015, "2"))
print(build_frbr_work_uri("xa", "act", 1992, "7", locality="c01"))
print(build_frbr_work_uri("xa", "act", "", "7"))
```

```text
/akn/xa/act/1992/7
/akn/xa/act/1992/7/eng@1992-05-02
/akn/xa/act/mint-circular/2004/12
/akn/xa/act/code/1998/3
/akn/xa/doc/guidance/2015/2
/akn/xa-c01/act/1992/7
/akn/xa/act/0001/7
```

A class with a `frbr.uri_patterns` entry takes that shape; otherwise the class name becomes the subtype.

![Where each part of two identifiers in the worked example comes from. The work URI /akn/xa/act/1992/7: the part /akn/xa/act/ is the fixed text of the config's pattern frbr.uri_patterns.act, /akn/xa/act/{year}/{number}, the pattern of the default document class, act; the part 1992/7 fills the pattern's year and number slots with 1992 and 7, which a model read from the text as metadata. The eId part_I__sec_1__point_1, with levels joined by a double underscore: part comes from the hierarchy level whose akn_element is part, with the number I as printed in PART I, found by rules; sec is the prefix of the level whose akn_element is section, with the number 1 as printed, found by rules; point_1 comes from the text a model filled in, where each line starting (1) becomes a point, not the config's Subsection level.](../figures/v8-identifier-sources.svg "Each segment of the work URI and of one eId, labelled with the config field or the text it comes from, in the worked example's real run.")

<details><summary>The builder's rules, in order</summary>

1. **Country.** `frbr.country_code` from the config, or the jurisdiction code when there is no
   config.
2. **Configured pattern.** It looks up `frbr.uri_patterns` by the document class name.
   `mint_circular` has a pattern in the Atlantis config, so its URI takes the configured shape. A
   pattern may use only `{year}` and `{number}`; one that asks for another field is skipped and
   logged as `frbr_pattern_unsupported_field`.
3. **Default.** With no pattern, the class name becomes the subtype (`code` gives
   `/akn/xa/act/code/1998/3`). A class named `act` has no subtype. A class that declares
   `akn_element` `doc` or `statement` goes under that element, as `guidance` does.
4. **Locality.** Joined to the country segment.
5. **Unknown year.** Written as `0001`: AKN requires a date segment, and the XML schema has no
   year zero.

A class with no pattern of its own takes the default shape even when the config has one under `act`.

</details>

Decide the URI for every class before your first production ingest: [Model your legal system](../guide/model-your-legal-system.md) covers the design, [Write a jurisdiction config](../guide/write-a-jurisdiction-config.md) the fields.

:::caution
A work URI is a law's permanent name. Storage keeps one law per work URI, so changing a pattern after ingest creates new laws beside the old ones.
:::

## Where the year and number come from

Ingest uses the number the document states if it is shaped like a law number. Failing that it uses a number in the title, a title digest or a hash of the source file; a config can choose the digest first.

<details><summary>How the number segment is chosen</summary>

| Situation | Number segment |
| --- | --- |
| The document states a number such as `7`, `7A`, `12-3` or `12/2004` | That number. A slash becomes a hyphen, so `12/2004` is `12-2004`. |
| The value is not shaped like a law number (an office routing code, say) | Discarded and logged as `law_number_discarded`; the next rows apply. |
| No usable number, and the title contains one | The number in the title, except for an amending act, whose title states the number of the act it amends. |
| The config declares `frbr.title_identity`, for instruments that carry no number | `t-` and 12 hexadecimal characters, a digest of the normalised title, edition and year; the same title always gives the same segment. When it resolves, it turns off the number-in-the-title row. |
| The class sets `number_source` to `title_identity` | The title digest even when a number is stated, for classes whose serials repeat across issuers. |
| Nothing else resolved | `draft-` and 12 hexadecimal characters of the source file's SHA-256. |

With `number_has_year_prefix`, a class has a leading year removed from its number: `1992-7` in a
1992 document becomes `7`. Calendars are covered in
[Model your legal system](../guide/model-your-legal-system.md).

</details>

A `draft-` number or the year `0001` lets the ingest finish without a citable identity; the structural validator reports it as `uncitable_work_uri`:

```python
from codify.frbr import build_frbr_work_uri, is_citable_work_uri

print(is_citable_work_uri("/akn/xa/act/1992/7"))
print(is_citable_work_uri("/akn/xa/act/0001/draft-0d22cdcc10e6"))
build_frbr_work_uri("xa", "act", 1992, "")
```

```text
True
False
Traceback (most recent call last):
  ...
codify.frbr.UncitableFrbrUri: '/akn/xa/act/1992/' is not citable (empty path segment); country='xa' doctype='act' number=''
```

An empty segment raises `UncitableFrbrUri` and stops the ingest: a stored identity nobody can write down is worse than a failed run.

## Expression URIs

`build_frbr_expression_uri(work_uri, language, date)` appends the language and date.

- **Language** is the one the model detected, then the config's `authoritative_language`, then the config's only language.
- **Date** on the PDF and text lane is the work's own date (`FRBRdate` named `Original`); this lane reads no separate expression date. Published AKN keeps its publisher's.

<details><summary>Fallbacks and other suffixes</summary>

- A multi-language config with no detection falls back to `eng` and logs `language_unresolved_defaulting_eng`.
- A single-provision patch made with `amend_provision` appends `.1`, `.2` and so on to the parent's
  expression URI. Under the naming convention a dot introduces the manifestation format, so another
  system reads `eng@1992-05-02.1` as format `1` of the parent. Rename these versions before export.
- `include_time=True` with a timezone-aware timestamp writes a full UTC timestamp after the `@` in
  place of the date.

</details>

## What identity does in storage

| Rule | Effect |
| --- | --- |
| `laws.frbr_work_uri` is unique | One law per work across the whole database. |
| `versions.expression_uri` is unique | Loading the same expression twice returns the version already stored. |
| A work URI already held by another jurisdiction | The write is refused with `ForeignJurisdictionCollision`. |
| Provisions are unique on version and eId | A document that repeats an eId cannot be stored until the repeat is resolved. |

Loading the worked example twice gives the same `version_id`. To store a corrected text of the same expression, see [Amendments and versions](../guide/amendments-and-versions.md).

## Cite a provision

Cite at the level the claim needs. On the worked example, section 12 has the eId `part_IV__sec_12`:

| To cite | Form | Example |
| --- | --- | --- |
| The law, in every version | Work URI | `/akn/xa/act/1992/7` |
| Its text as it stood on a date | Expression URI | `/akn/xa/act/1992/7/eng@1992-05-02` |
| One provision, in whichever text applies | Work URI, `#`, eId | `/akn/xa/act/1992/7#part_IV__sec_12` |
| One provision's wording on a date | Expression URI, `#`, eId | `/akn/xa/act/1992/7/eng@1992-05-02#part_IV__sec_12` |

An eId names the same provision across expressions until the law is renumbered, and a repair can rewrite a stored version, eIds included, in place ([Amendments and versions](../guide/amendments-and-versions.md)). Where the exact words matter, keep the text you cite alongside the URI.

To choose a text for a date, take the stored version with the latest expression date not after it, in the law's authoritative language and leaving out `.1`, `.2` patch versions. That is the latest text Codify holds, not proof it was in force: a consolidation may be missing, and commencement or repeal is not checked. Codify's reference resolver applies the same date cutoff only to links to recognised publisher addresses, and takes the latest version for `/akn/...` links ([`codify/storage/resolve_refs.py`](../../codify/storage/resolve_refs.py)). `GET /laws/{law_id}/versions` lists each version's `expression_uri`, one page at a time (`cursor`); take the date from the URI.

A search match gives `work_uri`, `eid` and `version_id`, and that version's `expression_uri` completes the citation. A match `eid` ending `__intro`, `__content` or `__wrapup` may name a stored slice of an element rather than an eId in the XML, though a `wrapUp` element can carry such an eId itself. Check the version's XML: cite the `eid` if an element has it, otherwise the element it belongs to.

`parse_source_ref` below reads all four forms. Codify does not resolve them: the server looks laws up by id ([Status](#status)). It neither writes nor parses the naming convention's portion form, `/akn/xa/act/1992/7/eng@1992-05-02/!main~part_IV__sec_12`.

## Reading URIs

```python
from codify.akn.frbr import work_parts
from codify.frbr import expression_uri_date, parse_source_ref

print(work_parts("/akn/xa/act/mint-circular/2004/12"))
print(expression_uri_date("/akn/xa/act/1992/7/eng@1992-05-02/!main"))
print(parse_source_ref("/akn/xa/act/1992/7#sec_12"))
```

```text
('xa', 'act', 'mint-circular', '2004', '12')
1992-05-02
('/akn/xa/act/1992/7', 'sec_12')
```

`parse_source_ref` splits a link into a work URI and a target eId, and returns `None` for a form it does not know.

<details><summary>What <code>parse_source_ref</code> recognises</summary>

It recognises Codify's `/akn/...` form, legislation.gov.uk's `/id/{type}/{year}/{number}` and
`/european/{kind}/{year}/{number}` links to EU instruments.
From `/akn/...` links it removes only a bare `/main`: `/akn/xa/act/1992/7/!main#sec_12` returns
`/akn/xa/act/1992/7/!main`, and an expression URI comes back whole. Pass work-level links, or
strip the component and expression yourself.

</details>

## Status

| Capability | Status |
| --- | --- |
| Work and expression URIs minted during ingest | **Available**: `codify ingest-one`, `POST /runs/ingest` |
| URI patterns per document class in the config | **Available**: `frbr.uri_patterns` |
| Builders and parsers (`codify.frbr`, `codify.akn.frbr`) | **Library only** |
| Title-derived and content-addressed numbers | **Available**: applied by ingest when no number is stated |
| Changing the work URI of a stored law | **Library only**: `codify.pipeline.enrich.cobalt.set_work_uri` restates a document's identification block; no command or route applies it to stored laws |
| Resolving a FRBR URI over HTTP (`GET /akn/...`) | **Not in the core**: the server looks laws up by id; the law list carries `frbr_work_uri` and the law detail `work_uri` |
| Minting ELI identifiers for your own documents | **Not in the core** |

## Next

[Bluebell](bluebell.md): the plain-text notation Codify structures law in.
