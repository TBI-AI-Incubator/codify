---
title: Decision records
description: The design decisions behind Codify, what each one settled, and where the code has moved on since the record was written.
sidebar:
  order: 5
---

A decision record keeps the reasoning behind a choice that is expensive to change later,
such as which calendar a law's identifier uses. Read one to learn why Codify behaves as it
does, or before you propose changing that behaviour.

The records live under `docs/decisions/`. Each keeps its argument as written; where the
code has moved on, a dated status note near the top says what differs today. Where a
record and the source disagree, the source is right.

## The records

| Record | What it settled | State today |
| --- | --- | --- |
| [001: Gregorian calendar in FRBR URIs](../decisions/001-calendar-policy.md) | The year in a law's identifier is always a Gregorian year, whichever calendar the jurisdiction dates its law in. | In force. A test checks that no shipped config declares a non-Gregorian calendar for its URIs. The record says the local date is kept as an alias; no code writes one. |
| [002: § maps to `article`](../decisions/002-paragraph-symbol-mapping.md) | Where § marks the basic citable provision, it becomes the Akoma Ntoso `article` element in every jurisdiction. | In force. Every config the record names follows it. No test checks a new config. |
| [003: Supranational membership is config data](../decisions/003-supranational-membership.md) | A jurisdiction's membership of a body such as a union is data on its config, not a constant in code. | The data model is in the core. Nothing in the core's comparison code reads it. |
| [005: Core and plugin boundary](../decisions/005-open-core-boundary.md) | Which capabilities belong to the open core, and that structure-preserving translation is one of them. | In force. |
| [006: Repository topology](../decisions/006-repository-topology.md) | The core is a library that never imports the hosted platform's packages. | The dependency rule is in force. The record's statement that the core has no web interface is out of date. |

The series has no record 004.

## Two decisions to settle before you store law

**The year in an identifier (001).** A FRBR work URI names a law as a
[work](../concepts/frbr.md), whatever its version or language: `/akn/xa/act/1992/7` is
Act 7 of 1992 of the fictional jurisdiction `xa`. An expression URI adds the language and
names one version, such as `/akn/xa/act/1992/7/eng@1992-05-02`. Other documents cite the work URI, so
it must not depend on how one source prints a date. Codify converts a year printed in a
local calendar to a Gregorian year before it builds the URI.

Ingest uses the `frbr.calendar_conversion` rule in your
[jurisdiction config](../guide/model-your-legal-system.md) when one is declared;
that rule can take the month and day into account. Otherwise it converts from the
config's `calendar` field with a built-in rule that reads the year alone. Near a
local new year the two can give different Gregorian years, and so different URIs for the
same law: settle the rule before your first real ingest.

The built-in conversion needs no model, database or config:

```python
from codify.calendar import year_from_calendar

print(year_from_calendar("1256", "lunar_hijri"))
```

```text
1840
```

**The element behind § (002).** An eId identifies one provision inside a document: a path
with one component per level. Each component comes from the level's
[Akoma Ntoso](../concepts/akoma-ntoso.md) element and number, so an `article` numbered 5
gives `art_5` and a `section` numbered 5 gives `sec_5`.
Search results and comparison findings point at eIds. Record 002 gives § one mapping
everywhere, so provisions of the same kind carry identifiers of the same shape across
jurisdictions. If your legal system uses § for its basic unit, map it to `article`.

![Where each part of two identifiers in the worked example comes from. The work URI /akn/xa/act/1992/7: the part /akn/xa/act/ is the fixed text of the config's pattern frbr.uri_patterns.act, /akn/xa/act/{year}/{number}, the pattern of the default document class, act; the part 1992/7 fills the pattern's year and number slots with 1992 and 7, which a model read from the text as metadata. The eId part_I__sec_1__point_1, with levels joined by a double underscore: part comes from the hierarchy level whose akn_element is part, with the number I as printed in PART I, found by rules; sec is the prefix of the level whose akn_element is section, with the number 1 as printed, found by rules; point_1 comes from the text a model filled in, where each line starting (1) becomes a point, not the config's Subsection level.](../figures/v8-identifier-sources.svg "Each segment of the work URI and of one eId, labelled with the config field or the text it comes from, in the worked example's real run.")

Record 003 explains the `supranational_memberships` block you may see in a config.
Records 005 and 006 draw the line between the open core and anything built on it;
[What is open](../open-core/what-is-open.md) states that line today.

## Status

| Capability | Status |
| --- | --- |
| Gregorian year in the work URI, converted by the config's rule or the built-in one | **Available**: applied when `codify ingest-one` ingests a PDF or text source, and when `codify serve` runs an ingest (`POST /runs/ingest`) |
| Local-calendar date kept as an alias on the work | **Not in the core** |
| § levels mapped to `article` in the shipped configs | **Available** |
| Supranational body registry and memberships on a config | **Available** to read: `GET /jurisdictions/{code}` on `codify serve` returns them with the config |
| Comparison or lens scope derived from a membership | **Not in the core** |
| Lens plugins (analyses that record findings against stored provisions) through the `codify.lenses` entry-point group | **Library only**: the core defines the interface and registers no lens |
| HTTP server, MCP server and web app | **Available** |

## Design proposals

`docs/design/` holds proposals for a corpus ownership boundary in storage:
[Corpus ownership](../design/corpus-ownership.md), marked as a proposed contract, and its
[storage API inventory](../design/corpus-api-inventory.md), marked as a design inventory.
The storage code does not enforce either, so do not read them as a description of
behaviour.

To propose a change that touches one of these decisions, start from
[Contributing](contributing.md).
