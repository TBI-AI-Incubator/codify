---
title: Bluebell
description: The plain-text notation Codify structures law in before it becomes Akoma Ntoso, how to read it, and how to convert in both directions.
sidebar:
  order: 4
---

You end with the ability to read and write Bluebell, and to convert it to and from
[Akoma Ntoso](akoma-ntoso.md) (AKN) in Python.

Bluebell is a plain-text notation for legal documents and a parser that turns it into AKN.
Laws.Africa publishes it under the LGPL (package `bluebell-akn`); Codify uses it with Cobalt, a
companion library for AKN metadata and URIs. Codify never asks a model for XML: the parser
generates it, so nesting and eIds follow one rule ([Structure](../guide/structure.md)).

![Section 1 of the worked example, Short title and commencement, in four bundle files. In source.txt, line 17 is the marginal note Short title and commencement, line 18 the number 1 alone, and line 19 begins (1) This Act may be cited as the Legislation Act, 1992. Find anchors, by rule from the config, records it as line 2 of anchors.jsonl: kind section, keyword Short title and commencement, number 1, char_offset 959, line 17, parent_eid part_I, akn_eid part_I__sec_1, source_pass marginal_note_units; the eId is set here. Build scaffold writes a header line per anchor with no body text, and keeps the eId in memory beside each header rather than in the file: scaffold.bluebell lines 11 to 15 read PART I - PRELIMINARY, SECTION 1 - Short title and commencement and SECTION 2 - Interpretation, with blank lines where the bodies go; a note on the SECTION 1 line reads part_I__sec_1, held in memory with this header, not in the file. Fill text calls a model, which returns the lines for each eId, and the parse gives final.akn.xml from line 47: a section with eId part_I__sec_1, num 1, heading Short title and commencement, and a point with eId part_I__sec_1__point_1, num (1), whose paragraph part_I__sec_1__point_1__p_1 holds the text the model filled: This Act may be cited as the Legislation Act, 1992.](../figures/v4-four-panels.svg "Where Bluebell sits in a real run: Codify writes the scaffold from the anchors, the model adds only text, and the parser turns the result into the XML at the bottom.")

## The notation

Save this as `act.bluebell`:

```text
PREFACE

  No. 7 of 1992

  LONGTITLE An Act to consolidate and reform the law relating to the publication of the legislation of Atlantis.

BODY

PART I - PRELIMINARY

  SECTION 1 - Short title

    SUBSECTION (1)

      This Act may be cited as the Legislation Act, 1992.

    SUBSECTION (2)

      In this Act—

      PARAGRAPH (a)

        "the Gazette" means the official gazette of Atlantis;

      PARAGRAPH (b)

        "law" means any Act of the Assembly.

SCHEDULE Forms

  PARAGRAPH 1

    A request for access shall be made in the form set out below.
```

- **Containers open at the left margin**: `PREFACE`, `PREAMBLE`, `BODY`, `CONCLUSIONS`, or an
  attachment keyword and its heading. Provisions under `BODY` may start at the margin or one level
  in, as in `scaffold.bluebell`; both give the same tree and eIds.
- **A provision is a capitalised keyword, a number and a heading**, with space, hyphen, space
  between number and heading: `SECTION 1 - Short title`. Number and heading are optional.
- **Indentation is nesting**, two spaces per level in Codify's output.
- **Everything else is text**: a paragraph of the provision it is indented under. Only capitals
  are structure, so `Section 6 of the same Act` stays prose and an amending act can cite sections
  without creating them.

<details>
<summary>All keywords and inline markup</summary>

| Kind | Keywords |
| --- | --- |
| Containers | `PREFACE`, `PREAMBLE`, `BODY`, `CONCLUSIONS` |
| Attachments | `SCHEDULE`, `ANNEXURE`, `APPENDIX`, `ATTACHMENT` |
| Hierarchy | `BOOK`, `TOME`, `PART`, `SUBPART`, `TITLE`, `SUBTITLE`, `CHAPTER`, `SUBCHAPTER`, `DIVISION`, `SUBDIVISION`, `SECTION`, `SUBSECTION`, `ARTICLE`, `PARAGRAPH`, `SUBPARAGRAPH`, `CLAUSE`, `SUBCLAUSE`, `POINT`, `INDENT`, `ALINEA`, `RULE`, `SUBRULE`, `LIST`, `SUBLIST`, `LEVEL`, `PROVISO`, `TRANSITIONAL` |
| Headings and blocks | `LONGTITLE`, `CROSSHEADING`, `SUBHEADING`, `P` |
| Lists | `ITEMS` or `BLOCKLIST`, with `ITEM` lines; `BULLETS` |
| Tables | `TABLE`, `TR`, `TH`, `TC`; spans as `TC{colspan 2}` |
| Quoted structure | `QUOTE`, for the text an amending act inserts into another act |
| Footnotes | `FOOTNOTE` |

Each hierarchy keyword produces the lower-case AKN element of the same name; the parser also
accepts `ART`, `CHAP`, `PARA`, `SEC`, `SUBCHAP`, `SUBPARA` and `SUBSEC`. A jurisdiction config
records each level's keyword in `bluebell_keyword`, beside `akn_element`.

Inline markup: `**bold**`, `//italic//`, `__underline__`, and double braces for the rest, such as
`{{>#sec_6 section 6}}` for a reference and `{{^2}}` for superscript. A backslash escapes the next
character.

</details>

## From Bluebell to AKN

```python
from pathlib import Path

from codify.akn.bluebell import bluebell_to_akn

text = Path("act.bluebell").read_text(encoding="utf-8")
akn_xml, source_map, errors = bluebell_to_akn(
    text, country="xa", doctype="act", date="1992", number="7", language="eng"
)
print(errors)
for anchor in source_map:
    print(anchor.line, anchor.kind, anchor.eid)
print(akn_xml[akn_xml.index("<body>") : akn_xml.index("</body>") + 7])
```

```text
[]
9 part part_I
11 section part_I__sec_1
13 subsection part_I__sec_1__subsec_1
17 subsection part_I__sec_1__subsec_2
21 paragraph part_I__sec_1__subsec_2__para_a
25 paragraph part_I__sec_1__subsec_2__para_b
31 paragraph att_1__para_1
<body>
      <part eId="part_I">
        <num>I</num>
        <heading>PRELIMINARY</heading>
        <section eId="part_I__sec_1">
          <num>1</num>
          <heading>Short title</heading>
          <subsection eId="part_I__sec_1__subsec_1">
            <num>(1)</num>
            <content>
              <p eId="part_I__sec_1__subsec_1__p_1">This Act may be cited as the Legislation Act, 1992.</p>
            </content>
          </subsection>
          <subsection eId="part_I__sec_1__subsec_2">
            <num>(2)</num>
            <intro>
              <p eId="part_I__sec_1__subsec_2__intro__p_1">In this Act—</p>
            </intro>
            <paragraph eId="part_I__sec_1__subsec_2__para_a">
              <num>(a)</num>
              ...
```

| Returned | Content |
| --- | --- |
| `akn_xml` | The AKN document as a string, or `""` if parsing failed. |
| `source_map` | One entry per structural marker: its line, the AKN element and the eId it became. An editor can use it to keep text and preview in step. |
| `errors` | Empty on success. On failure, one entry with `line`, `column` and `message`. |

The parser added `intro` (the text before the first lettered paragraph) and `content` itself. The
schedule became attachment `att_1`, a nested `doc` with its own identification block, whose eIds
restart under `att_1`. Identity is raw: work URI `/akn/xa/act/1992/7` from the
`date` and `number` arguments (a full date appears whole), title `Untitled`, no expression date.
Ingest sets the real identity ([FRBR and document identity](frbr.md)), so use raw output for
structure only.

### Indentation decides the tree

Unindent the section line and it leaves Part I:

```python
from pathlib import Path

from codify.akn.bluebell import bluebell_to_akn

text = Path("act.bluebell").read_text(encoding="utf-8")
flat = text.replace("\n  SECTION 1", "\nSECTION 1")
_, source_map, _ = bluebell_to_akn(flat, country="xa", date="1992", number="7")
print([anchor.eid for anchor in source_map][:3])
```

```text
['part_I', 'sec_1', 'sec_1__subsec_1']
```

Both are valid AKN, but the eIds differ and an eId is how a provision is cited, so keep one form
per document class.

### The parser accepts almost anything

A line the parser does not recognise becomes a paragraph, with no error:

```python
from codify.akn.bluebell import bluebell_to_akn

text = "SECTION1 Short title\nThis Act may be cited as the Legislation Act, 1992.\n"
akn_xml, source_map, errors = bluebell_to_akn(text, country="xa", date="1992", number="7")
print(errors, source_map)
print(akn_xml[akn_xml.index("<body>") : akn_xml.index("</body>") + 7])
```

```text
[] []
<body>
      <hcontainer name="hcontainer" eId="hcontainer_1">
        <content>
          <p eId="hcontainer_1__p_1">SECTION1 Short title</p>
          <p eId="hcontainer_1__p_2">This Act may be cited as the Legislation Act, 1992.</p>
        </content>
      </hcontainer>
    </body>
```

A missing space after the keyword turned a section into two paragraphs. An empty `errors` list
means the text parsed, not that the structure is right: check the eIds in `source_map`,
or run the [structural validator](../guide/validate-and-repair.md), which reports a body with no
provisions.

## What Codify adds around the parser

`bluebell_to_akn` calls Codify's wrapper, `codify.pipeline.enrich.bluebell.parse_to_akn`, which
maps the document class to a standard root, cleans the text and fixes common OCR and model slips
before parsing.

<details>
<summary>Every step the wrapper adds</summary>

| Step | Why |
| --- | --- |
| Maps the document class to a root element | Bluebell accepts standard roots only. A class that declares `doc` or `statement` uses that root; any other local class is parsed as `act`. See [document types](akoma-ntoso.md#document-types) for classes named after a standard root. |
| Substitutes year `0001` for an empty date | An empty date segment makes a URI that Cobalt refuses to parse. |
| Rejoins a citation that was split into a subdivision | Where the config lists `structuring.reference_nouns`, "…under section 41 subsection" followed by a `POINT (3)` line is one sentence, not a new point. |
| Repairs the indentation of `QUOTE` blocks | Quoted text must sit one level deeper than its `QUOTE` line. |
| Removes control characters | They are not allowed in XML and can arrive from OCR. |
| Merges a duplicate point that is a misread citation | Two sibling points with the same number, where the second continues the first. |
| Folds eIds to ASCII | See [identifiers](akoma-ntoso.md#identifiers-eid-and-wid). |

A local container with no Bluebell keyword is written with the stand-in its `hcontainers` entry
names in `bluebell_proxy`; after parsing, an element whose heading starts with the container's
local term is renamed to `<hcontainer name="...">`.

</details>

## From AKN back to Bluebell

```python
from pathlib import Path

from codify.akn.bluebell import akn_xml_to_bluebell, bluebell_to_akn

text = Path("act.bluebell").read_text(encoding="utf-8")
akn_xml, _, _ = bluebell_to_akn(text, country="xa", date="1992", number="7")
print(akn_xml_to_bluebell(akn_xml))
```

```text
PREFACE

  No. 7 of 1992

  An Act to consolidate and reform the law relating to the publication of the legislation of Atlantis.

BODY

PART I - PRELIMINARY

  SECTION 1 - Short title

    SUBSECTION (1)

      This Act may be cited as the Legislation Act, 1992.
...

SCHEDULE 1 - Forms

  PARAGRAPH 1

    A request for access shall be made in the form set out below.
```

Parsing the output reproduces the hierarchy, but the round trip loses inline markup, metadata and
some keywords, so use it to read or edit structure only.

<details>
<summary>What the round trip loses</summary>

- Inline markup is flattened to text; references, terms and definitions are not written back.
- No `LONGTITLE` keyword; the long title returns as a plain line.
- Each attachment is written as `SCHEDULE {n} - {heading}`, so parsed again its heading reads
  `1 - Forms`.
- A local container's heading becomes a `CROSSHEADING` line.
- `conclusions` are dropped.
- Metadata is not part of Bluebell text; you supply identity again when you parse.

</details>

:::caution
`akn_xml_to_bluebell` uses lxml's default parser, not the
[hardened one](akoma-ntoso.md#reading-untrusted-xml), so it accepts a `DOCTYPE`. Pass it only XML
Codify wrote, or check the file first with `codify.akn.parse_xml`, which refuses one.
:::

## Status

| Capability | Status |
| --- | --- |
| Bluebell scaffold of every ingest, as `scaffold.bluebell` in the bundle | **Available**: `codify ingest-one` |
| Parse Bluebell text to AKN: `bluebell_to_akn`, `parse_to_akn` | **Library only**: no command takes a `.bluebell` file |
| Render AKN as Bluebell text: `akn_xml_to_bluebell` | **Library only** |
| Lossless AKN to Bluebell to AKN round trip, with inline markup | **Not in the core** |
| A text editor for Bluebell with live preview | **Not in the core**: the source map exists for one; the open web app has no editor |

## Next

[Translation](translation.md): how Codify's translation compares with published practice.
