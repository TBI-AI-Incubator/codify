---
title: Akoma Ntoso
description: What Akoma Ntoso is, which parts of it Codify writes and reads, how provisions are identified, and how to check a document against the schema.
sidebar:
  order: 2
---

You end with a reading knowledge of the Akoma Ntoso (AKN) 3.0 XML that Codify writes, in the
namespace `http://docs.oasis-open.org/legaldocml/ns/akn/3.0`, and stores as the record of the law.

AKN is the OASIS standard vocabulary for legal documents. It separates words from layout, names
every provision for citation, and carries the document's identity inside the file, so the law is
not tied to the tool that produced it ([Interoperate](../guide/interoperate.md)).

## The parts of a document

The start of the worked example, `final.akn.xml`, produced from
`data/fixtures/synthetic/xa/legislation-act-1992.pdf` ([First ingest](../start/first-ingest.md)):

```xml
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act name="act">
    <meta>
      <identification source="#cobalt">
        <FRBRWork>
          <FRBRthis value="/akn/xa/act/1992/7/!main"/>
          <FRBRuri value="/akn/xa/act/1992/7"/>
          <FRBRalias value="Legislation Act, 1992" name="title"/>
          <FRBRdate date="1992-05-02" name="Generation"/>
          <FRBRauthor href=""/>
          <FRBRcountry value="xa"/>
          <FRBRnumber value="7"/>
        </FRBRWork>
        ...
      </identification>
      <references source="#cobalt">
        ...
      </references>
    </meta>
    <preface>
      <p eId="preface__p_1">THE ATLANTIS GAZETTE</p>
      ...
    </preface>
    <preamble><formula name="enactingFormula" refersTo="#codify"><p>BE IT ENACTED by the Assembly of Atlantis, and by the authority of the same, as follows:</p></formula></preamble><body>
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

![Page 1 of the worked example's scan, pages/page-001.png, cropped from the masthead to subsection (1) of section 1, beside an abridged extract of final.akn.xml. Six numbered markers pair a printed line with its element. 1: the masthead, THE ATLANTIS GAZETTE, is a p in preface. 2: the line No. 7 of 1992 gives the work URI /akn/xa/act/1992/7, in FRBRuri under meta. 3: the recital ending in the enacting words, WHEREAS to AS FOLLOWS, is another p in preface. The formula in preamble, refersTo #codify, has no marker: Codify inserted it from the jurisdiction config. 4: the heading PART I, PRELIMINARY, is part with eId part_I and heading PRELIMINARY. 5: section 1 is section with eId part_I__sec_1 and num 1. 6: subsection (1) is point with eId part_I__sec_1__point_1.](../figures/v2-page-to-akn.svg "Each numbered marker pairs a line printed on page 1 with the element it became, from the worked example's real run.")

| Part | Element | What it holds |
| --- | --- | --- |
| Identity | `meta/identification` | The work, expression and manifestation, each with a URI and a date. See [FRBR](frbr.md). |
| Referenced entities | `meta/references` | A `TLCTerm` for each defined term and a `TLCOrganization` for each tool that wrote part of the document (Cobalt and Codify). |
| Front matter | `preface`, `preamble` | The title lines, the long title, recitals and the enacting formula. |
| The law | `body` | The hierarchy of provisions. A `doc` or `statement` uses `mainBody` instead. |
| Closing matter | `conclusions` | Promulgation and signature lines, where Codify finds them after the last provision. |
| Schedules and annexes | `attachments` | One `attachment` per schedule, each holding a nested document with its own `meta`. |

<details><summary>Why the worked example states its enacting formula twice</summary>

Codify inserts the `formula` in `preamble` (`refersTo="#codify"`) unless the opening text contains
one of the config's `enacting_formula_markers`, and the `xa` markers do not include "BE IT ENACTED".

</details>

## Document types

The element under `akomaNtoso` names the kind of document. For text it structures, Codify chooses
it from the document class in your jurisdiction config; AKN that arrives structured keeps its root.

| Document class | Root element written from text | Body element |
| --- | --- | --- |
| Declares `akn_element` `act`, or nothing | `act` | `body` |
| Declares `doc` | `doc` | `mainBody` |
| Declares `statement` | `statement` | `mainBody` |
| Declares any other element, such as `judgment` | `act` | `body` |
| Is itself named `bill`, `judgment` or `debateReport` | that element, but see the caution below | as the parser writes it |

:::caution
Do not key a class in `document_classes` as `bill`, `judgment` or `debateReport`. The metadata
pass handles only `act`, `doc` and `statement`, so the document keeps the parser's placeholder
identity.
:::

<details><summary>What happens with such a class</summary>

The Bluebell parser accepts those names as roots. The metadata pass then fails with
`Expected act as a child of root element`, is skipped and logged as `enrich_pass_failed`. The
document keeps the title `Untitled`, a work URI built without your `uri_patterns`, and an
expression URI with no date.

</details>

The class itself (a decree, a circular) is carried in the work URI's subtype segment and the
stored `doctype`; on the text lane the root's `name` stays `act`.

## Hierarchy elements

Your jurisdiction config maps each level of your hierarchy to one of AKN's fixed hierarchical
elements (`part`, `chapter`, `section`, `article`, `paragraph`, `point` and others), or to
`<hcontainer name="...">` where none fits. Each carries a `num`, an optional `heading`, and either
a `content` block or child elements, with optional `intro` and `wrapUp` text around them. See
[Model your legal system](../guide/model-your-legal-system.md) and the
[jurisdiction config reference](../reference/jurisdiction-config.md).

## Identifiers: eId and wId

Every provision has an `eId`, unique within the document. Under the AKN naming convention it is an element prefix, an underscore and the
provision's number, with a double underscore between levels. `part_I__sec_2__point_1__point_a` is
point (a) inside point (1), in section 2 of Part I; the worked example writes lettered paragraphs
as `point` elements too.

![Where each part of two identifiers in the worked example comes from. The work URI /akn/xa/act/1992/7: the part /akn/xa/act/ is the fixed text of the config's pattern frbr.uri_patterns.act, /akn/xa/act/{year}/{number}, the pattern of the default document class, act; the part 1992/7 fills the pattern's year and number slots with 1992 and 7, which a model read from the text as metadata. The eId part_I__sec_1__point_1, with levels joined by a double underscore: part comes from the hierarchy level whose akn_element is part, with the number I as printed in PART I, found by rules; sec is the prefix of the level whose akn_element is section, with the number 1 as printed, found by rules; point_1 comes from the text a model filled in, where each line starting (1) becomes a point, not the config's Subsection level.](../figures/v8-identifier-sources.svg "Each segment of the work URI and of one eId, labelled with the config field or the text it comes from, in the worked example's real run.")

Citations, cross-references, search results and translations all point at the eId, so it must not
change when a document is re-read. A numbered provision's eId therefore comes from its own number;
only unnumbered blocks, such as paragraphs (`p_1`, `p_2`), are counted by position within their parent.

```python
from codify.akn import ancestor_chain, parent_eid, parse_eid

eid = "part_I__sec_2__point_1__point_a"
print([(c.prefix, c.number, c.kind) for c in parse_eid(eid)])
print(parent_eid(eid))
print(ancestor_chain("part_I__sec_2__point_1"))
```

```text
[('part', 'I', 'part'), ('sec', '2', 'section'), ('point', '1', 'point'), ('point', 'a', 'point')]
part_I__sec_2__point_1
['part_I', 'part_I__sec_2', 'part_I__sec_2__point_1']
```

<details><summary>Prefixes</summary>

| Element | Prefix | Element | Prefix |
| --- | --- | --- | --- |
| `section` | `sec` | `chapter` | `chp` |
| `subsection` | `subsec` | `article` | `art` |
| `paragraph` | `para` | `subparagraph` | `subpara` |
| `clause` | `cl` | `attachment` | `att` |

Other elements use their own name (`part`, `point`, `hcontainer`). Bluebell serialises with the
same table, and a test fails if the two drift. A hierarchy entry can override the prefix with
`eid_abbrev`.

</details>

Codify keeps eIds ASCII, unique and present.

<details><summary>How Codify enforces each rule</summary>

- **ASCII only** (text Codify structures). After the Bluebell parse, digits in other scripts,
  declared ordinal words and marker punctuation are folded to ASCII, so `(1)` becomes `1`; `num`
  keeps the number as printed. An eId already in ASCII keeps its value, and a folded eId that would
  collide takes a `_2` suffix, whatever the document order. One that cannot be folded is left and
  logged as `eid_non_ascii_residual`.
- **Unique** (published AKN). The first occurrence of a repeated eId keeps it; later ones become
  `<eId>_dup2`, `_dup3` and so on.
- **Present** (published AKN). A provision with no eId gets `codify-synth-1` in the body, or the
  parent's eId plus the element type inside an attachment.

</details>

`wId` is the identifier a provision had in the work's original version, and matters only after a
renumbering. Codify reads it where the source has one and otherwise treats it as the eId.

## Inline markup

```xml
<p eId="part_I__sec_2__point_1__point_c__p_1"><def refersTo="#term-the-gazette">"the Gazette"</def> means the official gazette of Atlantis published under <ref href="#part_I__sec_6" class="derived">section 6</ref>;</p>
```

| Element | Meaning |
| --- | --- |
| `def` | The place where a term is defined. |
| `term` | A later use of a defined term, pointing at its entry in `meta/references`. |
| `ref` | A reference. `href="#..."` points at an eId in the same document; any other value is the URI of another work. |
| `authorialNote` | A note or marginal aside. |
| `mod`, `quotedStructure` | An amending instruction and the text it quotes. |

`class="derived"` marks a link Codify inferred from the wording; a `ref` without it was published
in the source. Keep the distinction when you export: only a published link is part of the record.

## The typed model

`parse_akn` reads AKN into a typed `Document`, for code that needs the hierarchy without XPath.

```python
from pathlib import Path

from codify.akn import parse_akn

doc = parse_akn(Path("bundle/final.akn.xml").read_text(encoding="utf-8"))
print(doc.frbr_work_uri, doc.frbr_expression_uri, doc.language, doc.expression_date)
part = doc.body[0]
print(part.kind, part.akn_type, part.akn_eid, part.number, part.heading)
section = part.children[1]
print(section.kind, section.akn_type, section.akn_eid, section.number, section.heading)
```

```text
/akn/xa/act/1992/7 /akn/xa/act/1992/7/eng@1992-05-02 eng 1992-05-02
title part part_I I PRELIMINARY
section section part_I__sec_2 2 Interpretation
```

<details><summary>Kinds, and what the model leaves out</summary>

The model has seven kinds: `title`, `chapter`, `section`, `article`, `paragraph`, `subparagraph`
and `point`. Most AKN elements fold into the closest kind and keep their name in `akn_type`, so the
part above reads `title part`. Elements outside the mapping in `codify/akn/vocabulary.py`, such as
`list`, `sublist` and `transitional`, are not read in.

`to_akn(doc)` writes only the identification block and the body, always under an `act` root. It
drops the preface, preamble, conclusions, attachments, `meta/references` and `def` and `term`
markup; `ref` elements survive. On the worked example the output still passes the strict schema.

</details>

:::caution
`to_akn(doc)` does not round-trip a document. To export, use the stored XML, as
[Interoperate](../guide/interoperate.md) shows.
:::

## Two kinds of validation

| Function | Question | Result |
| --- | --- | --- |
| `codify.pipeline.enrich.validator.validate_akn` | Does the structure look like the source? Numbering gaps, empty provisions, unreadable pages. | A list of findings. See [Validate and repair](../guide/validate-and-repair.md). |
| `codify.akn.validate_akn` | Is this XML valid against the OASIS schema? | Nothing on success; raises `lxml.etree.DocumentInvalid` on failure. |

The schema check confirms the grammar, not that every provision is there, so run both. By default
it uses Cobalt's lenient schema, which allows duplicate eIds and year-only dates; `strict=True`
uses the official OASIS schema.

```python
from pathlib import Path

from lxml import etree

from codify.akn import validate_akn

xml = Path("bundle/final.akn.xml").read_text(encoding="utf-8")
validate_akn(xml, strict=True)
print("valid")

broken = xml.replace('eId="part_I__sec_1__point_2"', 'eId="part_I__sec_1__point_1"', 1)
validate_akn(broken)
print("valid under the lenient schema")
try:
    validate_akn(broken, strict=True)
except etree.DocumentInvalid as exc:
    print(exc)
```

```text
valid
valid under the lenient schema
<string>:56:0:ERROR:SCHEMASV:SCHEMAV_CVC_IDC: Element '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}point': Duplicate key-sequence ['part_I__sec_1__point_1'] in unique identity-constraint '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}eId-act'.
```

<details><summary>Which paths run the schema check</summary>

| Path | Schema check |
| --- | --- |
| PDF or text through `ingest-one` or the server | Not run; only the structural validator |
| Published AKN through the server or `ingest_document` | Not run; only the structural validator |
| `codify load` | Not run |
| The EU converters (`codify/pipeline/formats/eu_directive.py` for FORMEX, `eu_html.py` for HTML) | Not run; only the structural validator |
| The other publisher-format converters in `codify/pipeline/formats/` | Lenient check. A failure stops the ingest at stage `schema_validation`. |
| A repair edit | Lenient check. An edit that fails it is rejected. |
| `amend_provision` (a single-provision patch) | Strict check. A failure raises `AknSchemaError` and nothing is stored. |

</details>

## Reading untrusted XML

`parse_akn` and the lanes that read published XML switch off entity resolution, DTD loading and
network access, and refuse a `DOCTYPE` with `XML declares a DOCTYPE; refusing to parse it`. Remove
the declaration before loading.

## Status

| Capability | Status |
| --- | --- |
| Write AKN 3.0 from PDF or text (`act`, `doc`, `statement` roots) | **Available**: `codify ingest-one`, `POST /runs/ingest` |
| Read published AKN, with eIds made unique and present | **Available**: `POST /runs/ingest` with an `.xml` or `.akn` file |
| Typed model: `parse_akn`, eId helpers | **Library only** |
| `to_akn` for a complete document (front matter, attachments) | **Not in the core**: it writes identification and body only |
| Schema validation, lenient and strict | **Library only**: `codify.akn.validate_akn`; no command runs it |
| Root elements other than `act`, `doc`, `statement` from text (`judgment`, `bill`, `debateReport`) with full metadata | **Not in the core** |

## Next

[FRBR and document identity](frbr.md): how a law, its versions and its files are named.
