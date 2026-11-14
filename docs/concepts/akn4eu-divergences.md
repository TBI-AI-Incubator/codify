---
title: AKN profiles and known differences
description: The choices Codify makes inside Akoma Ntoso 3.0, where they can differ from AKN4EU and other profiles, and what the tree does not test.
sidebar:
  order: 6
---

You end with a list of the mark-up choices to compare with what your receiving system expects.

[Akoma Ntoso](akoma-ntoso.md) 3.0 allows several schema-valid mark-ups of one law, so
institutions that exchange AKN agree a profile: narrower rules on elements, component names and
required metadata. AKN4EU is the EU institutions' profile; other publishers have their own. Codify targets the AKN 3.0 schema and
naming convention and claims conformance to no profile; the repository has no conformance suite
for AKN4EU, the legislation.gov.uk AKN format or Indigo. A schema-valid document can still be
refused, or read differently, by a system that expects a profile.
[Interoperate](../guide/interoperate.md) shows how to run the checks.

## Choices in every document

For documents structured from PDF or text:

| Area | What Codify writes | Check whether the receiving system |
| --- | --- | --- |
| Schedules and annexes | Inline: each is an `<attachment>` holding a nested `<doc>` with its own `<meta>` and `<mainBody>`; never `<documentRef>` or `<componentRef>` | Expects each annex as a separate referenced document (the schema allows both) |
| Component names | `/!main`, then `/!schedule_1`, `/!schedule_2` for attachments, in `FRBRthis` | Expects other names |
| Root `name` | `<act name="act">` for every act class; the class is in the URI subtype and stored `doctype` | Reads the type from `name` |
| Work date | Date of the law, `name="Generation"` | Expects other date names |
| Expression date | The work's date, `name="Original"`, as the source states no separate one | Needs a publication or in-force date |
| Manifestation | URI equals the expression URI, no format suffix; date is the day the file was produced | Requires `.xml` or `.akn` |
| Authors | `FRBRauthor href=""` at all three levels | Requires an author reference |
| eIds | ASCII only; a clash takes a `_2` suffix ([identifiers](akoma-ntoso.md#identifiers-eid-and-wid)) | Regenerates eIds on import, so stored citations to yours would not match |
| Enacting formula | `refersTo="#codify"` where Codify inserted one from the config. It is inserted unless the opening text contains one of the config's `enacting_formula_markers`, so a source worded differently gets a second one, as the worked example does | Wants inserted formulae kept or dropped |

<details>
<summary>Choices that rarely matter on import</summary>

| Area | What Codify writes | Check whether the receiving system |
| --- | --- | --- |
| Source of identification | `source="#cobalt"`, with a matching `TLCOrganization` | Usually nothing to check |
| Entity references | `TLCTerm` per defined term and `TLCOrganization` per tool, in `meta/references`. Terms and Codify's entry use an AKN `/ontology/...` path, such as `/ontology/term/the-gazette`, which nothing in Codify serves; Cobalt's entry is its project URL | Dereferences those `href` values |
| Derived references | `<ref class="derived">` for links inferred from wording | Wants inferred links imported |
| Local containers | `<hcontainer name="...">` where no standard element fits | Accepts those `name` values |
| Calendar | Gregorian years in URIs, converted from a local calendar where the config says how; ISO dates in metadata | Nothing, if it follows the naming convention |
| Document types | `act`, `doc` and `statement` roots with full metadata ([limit on other roots](akoma-ntoso.md#document-types)) | Needs `bill`, `judgment` or `debateReport` |

</details>

## AKN4EU

`codify/pipeline/formats/eu_directive.py` reads XML from the EU Publications Office, as AKN or as
FORMEX (the Office's XML format for the Official Journal); its source states it follows AKN4EU
3.0, the AKN core specification and FORMEX 5.55. An `akomaNtoso` root is kept as published; FORMEX
is converted to AKN. Both then pass through enrichment without a model, and the structural
validator runs last.

<details>
<summary>Enrichment passes, the converter's metadata and the AKN4EU choices it cites</summary>

Enrichment gives placeholder units a `status`, names local containers, adds `meta/references`
entries and marks up terms and references. The typed model drops elements outside the AKN
namespace; the stored XML keeps them.

This lane skips the text lane's metadata pass, so a converted FORMEX file carries its own
identification block:

| Field | FORMEX converter | Text lane |
| --- | --- | --- |
| `identification/@source` | `#codify` | `#cobalt` |
| `FRBRdate/@name` | `generation` at all three levels | `Generation`, `Original`, `Generation` |
| `FRBRauthor/@href` | `#eu` for work and expression, `#codify` for the manifestation | `""` |
| Manifestation URI | Ends in `.xml`, with `FRBRformat` `xml` | No format suffix |

Choices the converter cites from AKN4EU:

- the act title carries `docType`, `docNumber` and `docDate` inside `longTitle`;
- signature blocks in `conclusions` are grouped with `blockContainer`;
- a table sits in `hcontainer name="TAB"`, and a hierarchical element's `content` holds a single
  `p`, `table` or `foreign`;
- an annex is a `doc name="ANNEX"`, titled in a `container name="headerOfAnnex"`, and its work
  carries `FRBRprescriptive`;
- the root carries `contains="originalVersion"`, or `singleVersion` for a consolidated text.

</details>

Three points differ from what you might assume:

- **Annex content is inline**, as in every lane. A scanned page or nested document inside an annex
  is not transcribed: a marker paragraph names it, and the run reports `annex_images` or
  `annex_included_docs`.
- **Component names differ from the text lane**: `/main` (no exclamation mark) for the main document and `/!att_1` for an annex.
- **No schema check runs**. Run `codify.akn.validate_akn(xml, strict=True)` yourself.

The tests that check converted FORMEX against the strict schema are skipped, because their
fixtures do not ship; other tests convert short inline samples without it. Nothing tests AKN4EU
conformance.

The `eu` config ships, so the HTTP server reaches this lane two ways: an XML upload under `eu`,
or `POST /runs/ingest-url` with an EUR-Lex or Publications Office address, which needs
`ACQUISITION_USER_AGENT` ([Serve and publish](../guide/serve-and-publish.md)). The config's
patterns give work URIs the AKN4EU subtypes, for example `/akn/eu/act/reg/2016/679` for
Regulation (EU) 2016/679 and `/akn/eu/act/dir/2019/1024` for Directive (EU) 2019/1024.
`ingest-one` does not route XML here. From Python,
`formex_to_akn4eu(xml, frbr_work_uri=..., language=...)` runs the conversion alone, with no
model and no jurisdiction config.

## Other publishers and Indigo

AKN from legislation.gov.uk and similar publishers is stored as published, apart from the
identifier mapping and eId repair in
[Bring in AKN from another system](../guide/interoperate.md#bring-in-akn-from-another-system).
Codify builds AKN with Bluebell and Cobalt, Laws.Africa's open-source libraries, but has no
Indigo importer, exporter or test; try a few representative documents before planning a
migration either way.

## Status

| Capability | Status |
| --- | --- |
| AKN 3.0 output that passes the OASIS schema for the worked example | **Available**: `codify ingest-one`, `POST /runs/ingest` |
| OASIS schema check of a document | **Library only**: `codify.akn.validate_akn`; no command runs it |
| Schedules and annexes as separate referenced documents (`documentRef`) | **Not in the core** |
| FORMEX to AKN conversion | **Library only**: `codify.pipeline.formats.eu_directive.formex_to_akn4eu` |
| AKN4EU profile conformance checking | **Not in the core** |
| Reading publisher AKN with URI and eId normalisation | **Available**: `POST /runs/ingest` |
| Writing the legislation.gov.uk AKN format, its metadata extensions or URI scheme | **Not in the core** |
| Indigo import or export | **Not in the core** |

## Next

[Command reference](../reference/cli.md): every command and flag.
