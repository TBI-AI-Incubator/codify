---
title: What is open
description: What the open-source Codify core contains, what it leaves out and why, which data the package carries, and how to extend it without forking.
sidebar:
  order: 1
---

This page shows what you can run, change and redistribute under the core's licence, what
you would have to build or buy, and how to add your own analysis without forking. A
decision record sets the line:
[Core and plugin boundary](../decisions/005-open-core-boundary.md).

## Why the line is where it is

A country that digitises its law needs to own the result: the structured text, the
identifiers and the means to produce them again. So everything from a published document
to a citable, machine-readable law is open and runs on machines you control, calling an
OpenAI-compatible model endpoint you choose wherever it reads scans, structures text,
compares or embeds. Translation is on that path by decision: keeping each clause aligned
with its source is part of producing a citable language version.

What stays out: the commercial detectors, the data behind them, and the services for
running Codify as an organisation (sign-in, tenancy, audit and support).

![The open-core line. Inside, the Codify core (under the Apache License 2.0; the Python package is codify-core on PyPI): available as commands and apps are reading, structuring and validating a PDF (codify ingest-one), validation and the quality grade in every bundle, bulk publisher indexes (codify index-* and crawl-ocs), storage in PostgreSQL (codify load), hybrid search (codify search), directive-to-law comparison (codify compare), the read API (codify serve), the MCP server for agents (codify mcp), the web app (apps/web, run from a source checkout, not in the PyPI package) and the jurisdiction configs flagged synthetic or public_reference; library only are the repair agent (codify.repair), acquisition adapters (codify.acquisition), translation (codify.translate) and the lens interface (codify.lenses). The core calls your own OpenAI-compatible model endpoint to read scans, structure, compare, embed and translate. Your lens, a package with an entry point in codify.lenses, plugs in through an opening in the line beside the lens interface. Not in the core: the commercial lens detectors and their catalogues, customer corpora and real documents, other jurisdiction configs, sign-in, roles, tenancy and an audit trail, durable job orchestration, and hosting, backup, upgrade and support.](../figures/v6-open-core.svg "Schematic of the line: the heavy outline is the open core, grouped by status, with your model endpoint and your lens outside it and the dashed box for what the core leaves out.")

## What the core contains

| Capability | Status | Where to read more |
| --- | --- | --- |
| Reading a scanned or digital PDF, structuring it and validating the result | **Available**: `codify ingest-one` | [OCR](../guide/ocr.md), [Structure](../guide/structure.md) |
| Validation and the structural quality grade | **Available**: written into every bundle | [Validate and repair](../guide/validate-and-repair.md) |
| The repair agent for validator findings | **Library only** | [Validate and repair](../guide/validate-and-repair.md) |
| Bulk indexes for some publishers | **Available**: the `index-*` and `crawl-ocs` commands | [Acquire sources](../guide/acquire-sources.md) |
| Acquisition adapters for other sources | **Library only** | [Acquire sources](../guide/acquire-sources.md) |
| Storage of laws, versions and provisions in PostgreSQL | **Available**: `codify load`, and `codify migrate` for the schema | [Database](../operate/database.md) |
| Hybrid search | **Available**: `codify search` and `GET /search` | [Search](../guide/search.md) |
| Comparison of a directive with a domestic law that may transpose it, provision by provision, through a model | **Available**: `codify compare` and the MCP tool `compare_versions` | [Architecture](../concepts/architecture.md) |
| Structure-preserving translation | **Library only** | [Translate](../guide/translate.md) |
| The read API | **Available**: `codify serve` | [Serve and publish](../guide/serve-and-publish.md) |
| Model Context Protocol (MCP) server, which lets AI agents call the read tools | **Available**: `codify mcp` | [Serve and publish](../guide/serve-and-publish.md) |
| Web app for browsing, reading and searching law, and uploading documents to ingest | **Available**: `apps/web`, run from a source checkout | [Serve and publish](../guide/serve-and-publish.md) |
| The lens interface for analytical plugins (a lens scans stored law and reports findings) | **Library only**: `codify.lenses` | below |
| The jurisdiction config schema and the configs flagged `synthetic` or `public_reference` | **Available**: the `--jurisdiction` option of `ingest-one`, `GET /jurisdictions` and the MCP tool `list_jurisdictions` | [Write a jurisdiction config](../guide/write-a-jurisdiction-config.md) |

## What the core leaves out

| Not in the core | What the core has instead |
| --- | --- |
| Lens detectors: the analyses that scan a law for a particular kind of risk | the `Lens` interface, and tables for lens runs, findings and scheme matches |
| Private catalogues behind those detectors | three empty catalogue tables from the core migrations, with the permitted remediation template names fixed in check constraints: the shape, not the content |
| Customer corpora and real source documents | invented documents for the synthetic jurisdictions |
| Jurisdiction configs not flagged `synthetic` or `public_reference` | none; every config here carries one of the two flags |
| Sign-in, roles, tenancy and an audit trail | nothing: the servers have no authentication ([Security](../project/security.md)) |
| Durable job orchestration | an in-memory run queue in `codify serve` |
| Hosting, backup, upgrade and support | [Deployment](../operate/deployment.md) |

An install of the core alone registers no lens:

```bash
uv run python -c "from codify.lenses import list_names; print(list_names())"
```

```text
[]
```

## What the package carries

The wheel holds the `codify` package and part of the repository's `data/` folder. A build
hook, [`hatch_build.py`](../../hatch_build.py), selects by the configs' own flags, so no
separate list can fall out of step with them:

- every jurisdiction directory whose `config.json` sets `synthetic` or `public_reference`
  (`ships_in_open_wheel` in [`codify/open_wheel.py`](../../codify/open_wheel.py));
- the jurisdiction registry, cut to the codes that ship;
- `supranational_bodies.json`;
- everything under `data/frameworks/`.

The build refuses to produce a package when no config is flagged. The wheel leaves out the
sample documents, tests, web app, documentation and `contract/openapi.json`; the source
distribution carries every file git does not ignore.

The flags control packaging only. A `public_reference` config describes how a real
jurisdiction publishes its law; it grants no right to redistribute documents you download
from that publisher. Check each source's terms.

The synthetic jurisdictions, whose configs describe invented countries:

```bash
uv run python -c "
import json, pathlib
print([p.parent.name for p in sorted(pathlib.Path('data/jurisdictions').glob('*/config.json')) if json.loads(p.read_text()).get('synthetic')])
"
```

```text
['xa', 'xe', 'xl', 'xq', 'xu', 'xy', 'xz']
```

## Extend the core with a lens

A lens reads a version of a law stored as [Akoma Ntoso](../concepts/akoma-ntoso.md) (AKN),
the XML standard Codify writes law in, and yields findings, each tied to a provision by its
[eId](../concepts/akoma-ntoso.md#identifiers-eid-and-wid) (the provision's stable element
identifier). The core defines the contract and the storage; you write the analysis.

The contract is the `Lens` protocol in [`codify/lenses/types.py`](../../codify/lenses/types.py):

| Member | Purpose |
| --- | --- |
| `name` | the lens's unique name |
| `taxonomy_models` | the Pydantic models of its own vocabulary |
| `scan(version_id, *, llm, embedding_client, session_factory, lens_run_id, **params)` | an async iterator of `Finding` |
| `match_schemes(findings, *, session_factory, lens_run_id, **params)` | groups findings into `SchemeMatch` results |

Register one in either of two ways ([`codify/lenses/registry.py`](../../codify/lenses/registry.py)):

- Call `codify.lenses.register(lens)` from your application. A second lens with the same
  name raises `ValueError: lens '<name>' already registered`.
- Ship a package with an entry point in the `codify.lenses` group, naming a class or
  factory the registry calls with no arguments.

A packaged lens declares its entry point in its `pyproject.toml`:

```toml
[project]
name = "defined-terms-lens"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["codify-core"]

[project.entry-points."codify.lenses"]
defined-terms = "defined_terms_lens:DefinedTermsLens"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

The smallest lens that satisfies the contract, in `defined_terms_lens/__init__.py`, finds
nothing:

```python
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from codify.lenses import Finding, SchemeMatch


class DefinedTermsLens:
    name = "defined-terms"
    taxonomy_models: list[type[BaseModel]] = []

    async def scan(self, version_id: UUID, **params: Any) -> AsyncIterator[Finding]:
        return
        yield

    async def match_schemes(self, findings: list[Finding], **params: Any) -> list[SchemeMatch]:
        return []
```

Installed beside the core, the lens appears at the first lookup. From the root of a source
checkout, with the package folder in the current directory:

```bash
uv run --with ./defined-terms-lens --with-editable . python -c "from codify.lenses import list_names; print(list_names())"
```

```text
...
['defined-terms']
```

Keep `--with-editable .`: without it, uv resolves the lens's `codify-core` dependency from
PyPI and runs against the released core, not your checkout.

Discovery runs once per process, at the first call to `get` or `list_names`. A plugin that
fails to load, or has no `name` or `scan`, is logged and skipped without stopping the
others. A lens registered directly before that first lookup wins over an entry point of the
same name. An unregistered name raises `KeyError: "lens '<name>' not registered"`.

No command or route runs a lens. Call `scan` from your own code and store findings with the
helpers in `codify.storage`.

## Licences

The core's own code is under the [Apache License 2.0](../../LICENSE). Its dependencies keep
their own licences, and some are copyleft: Cobalt and Bluebell, the AKN libraries Codify
builds on, declare LGPL 3 terms. [`THIRD-PARTY-NOTICES.md`](../../THIRD-PARTY-NOTICES.md)
lists every locked dependency with its declared licence, and `licenses/` holds the texts.
Review them before you redistribute a build.

:::note[On the hosted Platform]
Codify Platform adds what this page lists as not in the core: authentication and access
control, a tamper-evident audit trail, lens detectors including comparison against the EU
acquis, cross-corpus analytics and a research agent, with hosting and support. The core's
`codify compare` assesses one directive against one domestic law that you name. See
[The hosted Platform](platform.md).
:::

## Next

[The hosted Platform](platform.md): what it adds, and how it depends on this core.
