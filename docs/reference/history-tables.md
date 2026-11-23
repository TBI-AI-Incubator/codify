---
title: History tables
description: The tables that hold every dated text of a work and the spans derived from them, what the builder derives, and what keeps them out of search.
sidebar:
  order: 9
---

Six tables keep a work's history apart from current law. One holds each text as the publisher gave it. Three hold what the builder derives from those texts: dated expressions, provision intervals and provision links. A fifth logs the derived rows a rebuild removes, and a sixth names the work that groups the texts. The builder is [`codify.history.build.build_work`](../../codify/history/build.py), a pure function: it reads one work's texts and returns rows, and the caller writes them.

## The idea in brief

A publisher's text is a fact and stays as it was given. Which text was in force on a date, where one provision's wording changed, and which provision a renumbered one continues are rules applied to those facts. A rule that proves wrong is corrected by rebuilding one work, not by rewriting keys readers have already cited.

Laws, versions and provisions are unchanged and remain the only tables search, embeddings and the latest-version readers touch. No query outside the history package names a history table; a test fails if one does.

## Tables

| Table | Holds | Key |
| --- | --- | --- |
| `history_works` | One act as history groups its texts; optional links to a law and a register work; the revisions of its last rebuild | surrogate `id` |
| `publisher_texts` | Every text: hash, language, the publisher's own start, end and publication dates, an opaque group and metadata, where the bytes can be read again | jurisdiction, source reference, source hash |
| `dated_expressions` | Derived: one span per selected text, per gap, or per derived text | work, language, variant, start |
| `provision_intervals` | Derived: one span per provision for each period its text did not change | work, language, variant, eId, start |
| `provision_links` | Derived: a provision's earlier eId, the day the later one opens, the rule and its confidence | work, language, variant, day, later eId, earlier eId |
| `history_withdrawals` | A derived row a rebuild removed, so a poller learns of withdrawals as well as additions | none declared |

`publisher_texts` is immutable except for the work assignment (`work_id`, `work_rule`); a trigger refuses any other change to a row. Deleting a text stays possible, and the work is then rebuilt without it. The derived tables carry no such trigger: a rebuild deletes a work's rows and inserts the new ones in one transaction, and stamps the work with the converter and builder revisions.

A text with no start date is undated or prospective. It is kept and never gets a span, so no read for a date reaches it.

## What the builder derives

Spans end where the next one starts; the last ends the day after its text's own end date, or stays open.

**One text per start date.** When several texts start on one day, a text that has an end date wins, then the latest publication. The others stay as facts and make no span. Texts of different languages or variants do not compete.

**Gaps.** A text that did not convert becomes a span with a `gap_reason` and no document. It closes every open provision interval, and the next text opens its provisions as new.

**Provision identity.** A provision keeps its history while its eId is unchanged. When the eId changes, it joins the one departing provision with the same number path from the basic-unit level down (the caller names that level's eId prefixes; the default is `sec` and `art`, the deepest in an eId) and the same type, if that is unique on both sides; where several wordings share a number, identical normalised text pairs them one to one. A converter duplicate suffix is ignored. Labels and notes about other wordings never join another eId. Identical text under a new number is recorded as a `moved_text` link at low confidence and is never merged into one history. A same-number link whose texts share under 30% of their wording stores that similarity as its confidence.

**Change hashes.** An interval stores the exact hash of its text and a normalised hash, taken after Unicode compatibility folding, one dash, one quote of each kind, no whitespace and case folded. A span splits on any exact change; a reader that wants only changes of wording compares the normalised hash.

**How a span was made.** Every span and interval carries `derivation` (a publisher text, applied amendments, an unofficial consolidation), a nullable `confidence`, and `official`. A `variant` in the keys holds parallel texts of one provision, for example by territory; the whole-act expression keeps the empty variant.

**Recording time.** `recorded_at` is when a row was first written. A rebuild keeps it for a row whose key, hashes, end date and derivation are unchanged, so a reader polling by recording time sees a text reissued, retroactive or future-dated on the day it is recorded, and sees withdrawals in `history_withdrawals`.

`dated_expressions.version_id` may name a held version with the same source hash, so a reader can show the repaired document for the span.

## Run the builder

The builder is synchronous and CPU-bound. Run it on a worker pool, not on an event loop.

```python
from codify.history.build import SourceText, Unit, build_work

built = build_work(work_id, jurisdiction_id, texts, now=now, previous=previous)
```

`previous` is the last build of the work, if any. `built.expressions`, `built.intervals` and `built.links` replace the work's rows; `built.withdrawals` lists the rows `previous` held that no longer exist.

## Status

| Capability | Status |
| --- | --- |
| The six tables and the builder | **Available** |
| Loading publisher texts, reading a date or a change feed, a refresh step | **Not in the core** |
