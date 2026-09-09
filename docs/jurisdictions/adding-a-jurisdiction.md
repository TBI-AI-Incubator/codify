# Adding a jurisdiction

A jurisdiction is one required file and no code. This walks the whole of it
against a configuration that exists in the tree, so every command below runs and
every number is one the pipeline actually produced.

The worked example is **Quivira** (`xq`), a fictional civil-law republic added
as this guide's fixture. Nothing in it describes a real place. When you write
your own, copy the shape and replace the content.

## What you need first

One statute from the jurisdiction, in whatever form you have it, and the answers
to five questions about it:

1. What is the instrument called, in the local language? (`ley`, `act`, `qanun`)
2. What does the law divide into, from the outside in? (título → artículo)
3. Which of those levels carries the substance a citation points at?
4. How is a law cited? (`Ley 4 de 2011`)
5. What sentence enacts it?

If you cannot answer 3, read one law and find the level a court would cite. That
level is the **basic unit**, and most of the configuration hangs off it.

## The files

```
data/jurisdictions/<your code>/
  config.json    what the pipeline reads. Required
  profile.md     prose for a human, rendered in the product. Optional
```

Only `config.json` is required. `load_profile` returns `None` when `profile.md`
is absent, and six of the twelve configs that ship today have none.

`xq` below is this guide's worked example and already exists, so read it rather
than recreate it. For your own jurisdiction pick a code nothing else uses: every
directory under `data/jurisdictions/` is a code, and so is every entry in
`registry.json`. A collision loads the wrong config rather than failing.

`config.json` is validated against `JurisdictionConfig` in
`codify/jurisdictions.py`. That model is the specification; this guide is a path
through it. Four fields are required: `code`, `name`, `tradition`, `languages`.
Everything else has a default, and the defaults are chosen so a thin config
fails loudly rather than quietly doing the wrong thing.

## Step 1: the smallest config that ingests

```json
{
  "code": "xq",
  "name": "Quivira",
  "tradition": ["civil_law"],
  "languages": ["spa"],
  "authoritative_language": "spa",
  "default_document_class": "ley",
  "document_classes": {
    "ley": {
      "label": "Ley (Law)",
      "akn_element": "act",
      "basic_unit": "article",
      "bluebell_compatible": true,
      "hierarchy": [
        {
          "local_term": "Título (Title)",
          "akn_element": "part",
          "level": "higher",
          "bluebell_keyword": "PART",
          "numbering": "roman_upper"
        },
        {
          "local_term": "Artículo (Article)",
          "akn_element": "article",
          "level": "basic",
          "bluebell_keyword": "ARTICLE",
          "numbering": "arabic_continuous"
        }
      ]
    }
  }
}
```

**`document_classes`** maps a local instrument name to its shape. The key is the
doctype you will pass on the command line. `akn_element` is what it becomes in
Akoma Ntoso, and it is normally `act` whatever the local name.

**`hierarchy`** is ordered outside in. Each entry needs `local_term`,
`akn_element` and `level`; `level` is one of `higher`, `basic`, `subdivision`,
`grouping`, `presentational`, and exactly one entry should be `basic`.
`bluebell_keyword` must come from Bluebell's own vocabulary (`PART`, `CHAPTER`,
`ARTICLE`, `PARAGRAPH`, …); a plausible abbreviation is rejected.

**`languages`** are ISO 639-3, three letters. `authoritative_language` is the one
the pipeline reads a document in when nothing else says.

## Step 2: check it parses

A real jurisdiction needs a registry entry before this passes. The registry is
derived, so do not write one: run the script and let it catch up.

From the `codify` package's own directory:

```
uv run python scripts/build_registry.py
uv run pytest tests/test_jurisdictions.py
```

Skip the first line and `test_registry_matches_jurisdiction_directories` fails on
your new directory, which is the check working rather than your config being
wrong. A synthetic jurisdiction needs neither, because it is excluded from the
registry by design.

The suite discovers every directory under `data/jurisdictions/`, so yours is
picked up with no registration step. It checks the model, and it checks the
cross-cutting invariants: that your `default_document_class` is one you declared,
that a residual system names a jurisdiction that exists, that only known
directories carry working files.

A validation error names the field and the value. The two that catch most people
are the `level` and `bluebell_keyword` enumerations above.

## Step 3: give it a corpus and prove the round-trip

The pipeline's structuring output is Bluebell, a plain-text form that parses to
valid AKN by construction. Writing one law by hand is the fastest way to prove
your hierarchy is right, and it costs nothing at runtime.

Quivira's three laws live in `tests/fixtures/synthetic/xq/`. The
opening of one:

```
PREFACE

  Ley 9 de 2004

  Ley de Publicación de Normas

PREAMBLE

  LA ASAMBLEA NACIONAL DE QUIVIRA DECRETA:

BODY

PART I - Disposiciones generales

  ARTICLE 1 - Objeto

    La presente ley regula la publicación de las leyes.
```

Round-trip it:

```python
from pathlib import Path

from codify.akn.bluebell import parse_to_akn
from codify.pipeline.enrich.validator import validate_akn

text = Path("tests/fixtures/synthetic/xq/publicacion-2004.bluebell").read_text()
akn = parse_to_akn(text, "xq", doctype="ley", date="2004", number="9", language="spa")
print(validate_akn(akn))
```

For Quivira's three laws this reports no findings, and the parts, articles and
paragraphs come back at the counts the sources declare.

**This is a Bluebell and AKN check, not a check of your config.** `parse_to_akn`
consumes the keywords you authored and never reads `document_classes`, so the
same counts come back however wrong your hierarchy is. It tells you the text
parses and the AKN is schema-valid. That is worth knowing first, and it is not
what you came for.

It needs no model and no network, so it is the one to run first and the one CI
can hold you to.

## Step 4: check the config, with source-form text

The anchor scanner is the part that reads your configuration. It builds its
alternation from every `local_term` you declared, which is why this step uses
the words a real document uses rather than Bluebell keywords:

```python
from codify.jurisdictions import load_config
from codify.pipeline.enrich.anchors import build_anchor_regex, scan_anchors

source = """TÍTULO I - Disposiciones generales

Artículo 1 - Objeto

La presente ley regula la publicación de las leyes.

Artículo 2 - Ámbito

Esta ley se aplica a toda ley aprobada por la Asamblea Nacional."""

anchors = scan_anchors(
    source, build_anchor_regex(load_config("xq"), "ley"), country="xq", doctype="ley"
)
print([(a.kind, a.number) for a in anchors])
```

For Quivira: one `part` and two `article` anchors, from `TÍTULO` and `Artículo`
alone. **Rename the basic unit's `local_term` to a word the text does not use and
every article disappears**, while the Bluebell round-trip in step 3 goes on
passing unchanged. That difference is the whole reason both steps exist.

If your own scan finds nothing, the `local_term` in your config is not the word
your documents use. If it finds the wrong level, check `akn_element` and which
entry you marked `basic`.

## Step 5: ingest a real document

```
uv run codify ingest-one <file.pdf> --jurisdiction xq --out bundle/
```

This is the full path: transcribe, scan for anchors, scaffold, fill bodies,
parse, validate. It needs a model, so it is the step an outside contributor
cannot run without a gateway. What it writes into `bundle/` is the point: the
page images, every anchor with the pass that produced it, the coverage
measurement with both number sets it compared, the scaffold before body-fill,
the final AKN and the validator findings.

Read that bundle before believing any summary of what an ingest did. The run
stream carries counts, and counts are what mislead.

## The flags

**`synthetic: true`** marks a fictional jurisdiction authored as test material.
It is discovered and validated like any other, and kept out of the derived
registry and the world atlas, which are real-only. Set it, or your invented
country appears on a map.

**`public_reference: true`** ships the config in the open core. It defaults to
false, so a jurisdiction joins the open set by a deliberate edit rather than by
being forgotten about. The reason for excluding rather than simplifying: a
simplified config is still a statement about what was studied.

**`tier`** is 1 to 5 and describes the source, not the work: 1 the jurisdiction
publishes AKN, 2 structured XML, 3 HTML with usable anchors, 4 PDF only, 5 no
online statute base. It is promoted from the derived registry, so set it from
what you found rather than from ambition.

**`validation.status`** is the ladder, and it is about evidence rather than
effort:

| status       | what it claims                                           |
| ------------ | -------------------------------------------------------- |
| `stub`       | the config parses and the shape is a hypothesis          |
| `draft`      | real documents were read and the hierarchy survived them |
| `production` | ingested at volume, ambiguities recorded and closed      |

`documents_examined` is the count behind the claim and `unresolved_ambiguities`
the count against it. A `production` status with nothing examined is the one
combination reviewers look for.

## The registry, and where the data lives

**Do not edit `registry.json` by hand.** It is derived: `scripts/build_registry.py`
rewrites it from the configs, which is why Step 2 runs it.

Synthetic jurisdictions have no registry entry at all, and that is asserted rather
than accidental: `test_registry_matches_jurisdiction_directories` excludes them on
purpose. So the registry lists five of the twelve configs that ship, and a count
that disagrees is the invariant working, not a gap.

**Where the loader looks.** It prefers the nearest data. In a checkout it walks up
from `codify/jurisdictions.py` to the tree's `data/jurisdictions`; an sdist reads the
`data/` at its own root, even when unpacked inside a checkout. In an installed wheel
the shipping subset travels inside the package at `codify/data/`, which wins outright
when it is there, and is absent in a checkout, so a checkout reads its working tree
and an edit there is live.

## What a thin config leaves empty

The minimum in Step 1 loads, and it is genuinely minimal. Six fields are set by
every one of the twelve configs that ship and by none of the example above:
`calendar`, `enacting_formulae`, `amendments`, `core_tlcs`,
`supranational_memberships` and `display`. None is required, and a config without
them parses; `enacting_formulae` and `core_tlcs` come back as empty lists and
`amendments` as unset. Read a shipped config before deciding you do not need them.

## Traps

**The doctype normalises before the URI is minted.** A `ley` becomes an `act` on
the way into AKN, so `frbr.uri_patterns` is keyed on `act` and a pattern keyed on
the local name is never consulted.

**A relation keyed on the local name dangles.** The same normalisation bites
manifests: `cites: /akn/xq/ley/2004/9` names nothing, because the law was minted
at `/akn/xq/act/2004/9`. This guide's own fixture had that wrong until review
caught it.

**Pass a year, not a date.** `parse_to_akn(date="2004-03-11")` mints
`/akn/xq/act/2004-03-11/9`, which the validator reports as an uncitable work URI.
`date="2004"` gives `/akn/xq/act/2004/9`.

**A missing config is now an error rather than a default.** Naming a jurisdiction
that has no config raises rather than falling back, because the fallback used to
scan non-Latin documents with English keywords and return a document that looked
thin rather than a run that had failed.

**One `basic` level.** Two, or none, and anchor scanning has no level to count
coverage against.

## Where to look next

- `codify/jurisdictions.py`: the model, and the specification.
- `data/jurisdictions/xq/`: this guide's fixture, minimal on purpose.
- `data/jurisdictions/xa/`, `xz/`: a common-law hierarchy and a right-to-left
  Arabic one, both synthetic, both fuller than Quivira.
