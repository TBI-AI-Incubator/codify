---
title: Model your legal system
description: Decide how your instruments, their divisions, their identifiers, calendars, languages and numbering map onto Akoma Ntoso before you write a config.
sidebar:
  order: 1
---

You end with a decision on each thing a jurisdiction config records: document classes, hierarchies, work URI shapes, calendars, languages and numbering. [Write a jurisdiction config](write-a-jurisdiction-config.md) turns the decisions into a file.

## Before you start

- A synced source checkout ([Install](../start/install.md)); run commands from its root.
- Real instruments of every kind you will load, from every period whose drafting differs.
- How a court or a drafter in your system cites a law and a provision.

## The idea in brief

Patterns built from your config find a law's structure; a language model writes the text under each provision but does not decide the divisions ([Architecture](../concepts/architecture.md)).

Two outputs are permanent once cited: the work URI names a law in every version and language ([FRBR identifiers](../concepts/frbr.md)), and the eId names one provision inside it ([Akoma Ntoso](../concepts/akoma-ntoso.md)). Settle both before your first real ingest; changing either later renames law already cited.

![Where each part of two identifiers in the worked example comes from. The work URI /akn/xa/act/1992/7: the part /akn/xa/act/ is the fixed text of the config's pattern frbr.uri_patterns.act, /akn/xa/act/{year}/{number}, the pattern of the default document class, act; the part 1992/7 fills the pattern's year and number slots with 1992 and 7, which a model read from the text as metadata. The eId part_I__sec_1__point_1, with levels joined by a double underscore: part comes from the hierarchy level whose akn_element is part, with the number I as printed in PART I, found by rules; sec is the prefix of the level whose akn_element is section, with the number 1 as printed, found by rules; point_1 comes from the text a model filled in, where each line starting (1) becomes a point, not the config's Subsection level.](../figures/v8-identifier-sources.svg "Each segment of the work URI and of one eId, labelled with the config field or the text it comes from, in the worked example's real run.")

Examples use Atlantis (`xa`), a fictional common-law jurisdiction shipped as test material.

## Steps

### List your kinds of instrument

A document class is one kind of instrument with its own shape, keyed by a name you choose in `document_classes`:

```python
from codify.jurisdictions import load_config

cfg = load_config("xa")
for name, dc in cfg.document_classes.items():
    print(f"{name:22} {dc.akn_element:9} basic_unit={dc.basic_unit}")
print(cfg.default_document_class)
```

```text
act                    act       basic_unit=section
code                   act       basic_unit=article
constitution           act       basic_unit=section
mint_circular          act       basic_unit=section
harbour_treasury_jmc   act       basic_unit=section
guidance               doc       basic_unit=section
resolution             statement basic_unit=section
determination          statement basic_unit=section
act
```

Make a separate class wherever division or numbering differs: an Atlantis code numbers articles inside sections; structured as an act, its provisions number against the wrong units and its chapter numbers can collide. `akn_element` is the Akoma Ntoso type: `act` for legislation, `doc` for a non-legislative document, `statement` for a public body's decision; any other value is written as an `act`.

`structuring.classification_rules` choose the class at ingest:

```python
print(cfg.classify_document_class(title="Mint Circular No. 4"))
print(cfg.classify_document_class(title="Legislation Act, 1992"))
```

```text
mint_circular
None
```

`None` means no rule matched, so the default class applies. Write a rule for every class a document must reach on its own.

<details>
<summary>How ingest chooses the class</summary>

Ingest takes the class the caller names, else the first rule to match by descending `priority`, else `default_document_class`. `codify ingest-one` names no class, so the rules decide. At ingest a rule tests the title or opening text against a regular expression, or tests only the date; `issuer_role` and `gazette_series` rules never match, because ingest supplies no issuer or series. A class with no rule (Atlantis's `guidance`, `resolution`, `determination`) needs the caller to name it, which only the library can do.

</details>

### Map each class's hierarchy

List each class's divisions from the outside in, each with a `level`; a config that breaks this order is refused at load:

| Level | Use it for |
| --- | --- |
| `higher` | Containers above the citable unit, such as part or chapter. None may follow `basic` or `subdivision` |
| `basic` | The unit a citation names, such as the section or article. At most one |
| `subdivision` | Divisions inside the basic unit: subsection, paragraph |
| `grouping`, `presentational` | Allowed anywhere, such as the Atlantis schedule (`grouping`). No code treats the two differently |

Each level maps a printed word (`local_term`) to a standard element (`akn_element`) by role, never by name: the Atlantis constitution prints "Article" above its sections, so that level is a `chapter`. Each level gives the eId one segment, so changing a mapping later changes every eId below it.

<details>
<summary>Section signs, schedules and the coverage check</summary>

Map a basic unit printed with the § sign to `article` ([decision record 002](../decisions/002-paragraph-symbol-mapping.md)). A division with no standard element is an `hcontainer`: Atlantis schedules are `hcontainer` with `akn_name` `schedule` and the eId prefix `sch` (`eid_abbrev`).

`level: basic` places the citable unit in the order; `basic_unit` on the class tells the structuring prompt what to expect. The coverage check on each ingest compares the provisions the scan found with the markers printed in the text. It counts the `basic_unit` element if its level declares a `marker_form` (provisions printed without a keyword), else the first of `article`, `section` and `rule` the hierarchy has (`basic_unit_kind` in `codify/pipeline/enrich/structure.py`). A class with none is never checked, so a missed provision never halts it.

</details>

### Design the work URI

A work URI has the form `/akn/{country}/{document type}/{subtype}/{year}/{number}`, built from the class name and the config:

```python
from codify.frbr import build_frbr_work_uri

for doctype in ("act", "code", "mint_circular", "guidance", "resolution"):
    print(f"{doctype:14} {build_frbr_work_uri('xa', doctype, 1992, '7')}")
print(build_frbr_work_uri("xa", "resolution", 2024, "3", locality="c01"))
```

```text
act            /akn/xa/act/1992/7
code           /akn/xa/act/code/1992/7
mint_circular  /akn/xa/act/mint-circular/1992/7
guidance       /akn/xa/doc/guidance/1992/7
resolution     /akn/xa/statement/resolution/1992/7
/akn/xa-c01/statement/resolution/2024/3
```

The class name is the subtype, underscores included; a class named `act` has none. A pattern in `frbr.uri_patterns` replaces that shape for one class, using only `{year}` and `{number}`: Atlantis's writes `mint-circular` where the class name gives `/akn/xa/act/mint_circular/1992/7`. Patterns are keyed by the `document_classes` key, so one keyed on `act` does not cover a class called `ley` or `loi`.

A sub-national instrument takes a locality in the country segment (`xa-c01`). Decide one URI shape per class, with the year and number your citations use; [FRBR identifiers](../concepts/frbr.md) covers missing values.

### Record your calendars

The URI year is always Gregorian, so all jurisdictions share one date space ([decision record 001](../decisions/001-calendar-policy.md)). `calendar` records the native calendar and `legal_eras` each period dated differently; the metadata step tells the model:

```python
from codify.jurisdictions import load_config
from codify.pipeline.enrich.metadata import calendar_hint

print(calendar_hint(load_config("xa")))
```

```text
This jurisdiction dates its instruments in the gregorian calendar. Thalassocracy (to 1847-03-31): dated in hijri.
```

The model labels the calendar of the date it reads; Codify converts the year and drops the local day and month:

```python
from codify.calendar import year_from_calendar
from codify.pipeline.dating import resolve_dating

print(year_from_calendar("1256", "lunar_hijri"))
print(year_from_calendar("5", "japanese_era"))
print(resolve_dating({"year": "1256", "calendar": "hijri"}, country="xa"))
```

```text
1840
None
Dating(uri_year='1840', raw_date='', stored_year=1840)
```

Where the calendar's name is not enough, declare a rule in `frbr.calendar_conversion` ([jurisdiction config reference](../reference/jurisdiction-config.md)): an era-named calendar needs an `era_table`, or a bare era year such as the `5` above is not converted.

<details>
<summary>Other conversion settings</summary>

- `month_day_is_gregorian` marks a calendar where only the year differs.
- `date_cues` with `month_names` let a date be read off the text. An `era_table` rule cannot carry them, because it reads a bare number as a year of its latest era.

</details>

### Declare languages and scripts

`languages` lists the languages your law is published in, as ISO 639-3 codes (`eng`, `ara`); `authoritative_language` names the one whose text prevails. Leave it unset where two languages have equal authority. Only an Arabic script pack ships; Zerzura (`xz`) is a synthetic Arabic config.

<details>
<summary>Language detection and script settings</summary>

A level printed differently per language or script lists its words in `local_terms`, keyed by language code or by `script:` plus an ISO 15924 code. A document's language at ingest is the one the model detected, else `authoritative_language`, else the only language listed, else `eng` with a warning. Ingest ignores `authoritative_language` on a document class. `display.script` selects script-specific handling; without it, the document's letters decide. Scripts other than Arabic use the general rules. No code reads `display.rtl`.

</details>

### Describe the numbering

The scanner recognises common number forms (Roman numerals, several digit systems, letter suffixes, insertions such as `13-1`); a `numbering` value on a level is for human readers only. Langkasuka (`xl`), a synthetic Indonesian config, uses ordinal words:

```python
from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors

text = "BAB I\nKETENTUAN UMUM\n\nBagian Kesatu\nUmum\n\nPasal 1\n\nIsi pasal.\n\nPasal 1A\n\nIsi pasal sisipan.\n"
cfg = load_config("xl")
for a in scan_anchors(text, build_anchor_regex(cfg, "act"), country="xl", doctype="act"):
    print(a.kind, a.number, a.akn_eid)
```

```text
... [info     ] container_heading_captured     heading='KETENTUAN UMUM' kind=chapter line=1 number=I
...
chapter I chp_I
part Kesatu chp_I__part_1
article 1 chp_I__part_1__art_1
article 1A chp_I__part_1__art_1A
```

<details>
<summary>Number forms and the settings that change them</summary>

Recognised forms: Roman numerals; Western, Arabic-Indic, Extended Arabic-Indic, Bengali, Devanagari and Thai digits; a single Latin letter; Arabic ordinal words; a number with a letter suffix (`5A`); hyphenated or slashed insertions (`13-1`, `7/1`).

| Case | Setting |
| --- | --- |
| Numbers written as words | `structuring.ordinal_words` maps each word to its value, in the source's own capitalisation |
| Inserted provisions | `1A` keeps its suffix in the eId. A suffix written as a separate word ("5 bis") goes in `structuring.insertion_suffixes` |
| Serials that carry the year | `number_has_year_prefix` on a class reads `2024-15` as number 15 of 2024 |
| Instruments without a number | `frbr.title_identity` derives a stable identity from the title. Otherwise the number is `draft-` plus the start of the source file's SHA-256 digest: stable across re-ingests, but the validator reports it uncitable |
| Citations by number alone | `numbering.series_citations` lists series cited with no year ("Act No. 386"); `numbering.numbers_unique_across_years` declares that a number alone identifies a law |

</details>

## Check your result

Rerun this page's code with your code in place of `xa`, using real titles from every period: each class prints its element, each title classifies correctly, and each URI matches your citations. Check calendars, languages and number forms against the same instruments.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `document class 'act': 2 hierarchy entries with level=basic (expected at most 1): ['Section', 'Article']` | Two `basic` levels | Keep the one a citation names |
| `document class 'act': higher-level entry 'Part' appears after a basic/subdivision entry` | Hierarchy not listed outside in | Reorder it |
| `an era_table conversion cannot carry a date grammar (month_names, date_cues)` | An era table takes no date grammar | Remove the date grammar |
| A law filed under `/akn/{country}/act/{class}/...` despite your pattern | Pattern keyed on `act`, not the class name | Key it on the class name |
| The URI year is `0001`; the validator reports `uncitable_work_uri` | No year read or converted, such as an era year without an era table | Add the conversion rule |
| Instruments of one kind structured with another's hierarchy | No classification rule matched; the default class applied | Add a rule |

## Status and limits

| Capability | Status |
| --- | --- |
| Several document classes, chosen by classification rules at ingest | **Available** |
| Naming the class for one document | **Library only**: the `doctype` argument of `ingest` in `codify.pipeline.formats.pdf`. `ingest-one` has no option for it |
| Sub-national issuers (`locality`) | **Library only**: the `locality` argument of `ingest` |
| Converting a local-calendar year to the Gregorian URI year | **Available** |
| Keeping the local-calendar date as an alias in the document | **Not in the core** |
| Script-specific handling for Arabic | **Available** |
| Script-specific handling for other scripts | **Not in the core**: the general rules apply |

## Next

[Write a jurisdiction config](write-a-jurisdiction-config.md)
