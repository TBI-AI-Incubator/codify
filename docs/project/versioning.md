---
title: Versioning
description: What a Codify release promises as a library, a command and a database schema, what counts as a breaking change, and how a release is published.
sidebar:
  order: 3
---

The policy is [`VERSIONING.md`](../../VERSIONING.md) at the repository root; this page
explains it and adds what it does not yet say.

The distribution is `codify-core`, imported as `codify`. It has no `codify.__version__`
attribute; check your version with:

```bash
uv run python -c "import importlib.metadata as m; print(m.version('codify-core'))"
```

## Why a law system needs a version policy

Stored law outlives the software that produced it. A ministry that has published
identifiers and built a reader on the HTTP API needs to know which upgrade changes the
Akoma Ntoso (AKN) it gets back, the columns it queries or the command it scripts. So a
change in output counts as a break even when no signature changes: structuring an act
differently disrupts a published corpus as much as renaming a function disrupts a program.

## What is promised

| Surface | What it covers | A break is |
| --- | --- | --- |
| Modules | everything under `codify.` whose name does not start with an underscore | removing or renaming a symbol; changing a parameter's name, order or meaning; inserting a parameter ahead of an existing one; widening a return type |
| The command | `codify` and every subcommand in the [CLI reference](../reference/cli.md), with the output each writes | removing a subcommand or a flag; changing a flag's meaning or the shape of the output |
| Jurisdiction config | `JurisdictionConfig`, `ResolvedConfig` and the `config.json` files the package carries | removing a field; making an optional field required; reinterpreting a field; removing a shipped jurisdiction |
| Events | `ProgressEvent` and the domain events written through `codify.storage` | removing a field; renaming an event; changing when one fires |
| Database schema | the core migration chain, its version table `alembic_version_core`, and every table, column, default, index, trigger and function it creates | dropping or renaming any of them; narrowing a type; dropping a default; adding a constraint |

Adding a constraint is a break because it can reject a write that used to succeed, even
when every existing row passes it. A purely additive change is not a break; a new
parameter is additive only when it is optional and goes last or is keyword-only.

## What the policy does not yet name

The policy predates the servers, so it neither includes nor excludes three public
surfaces. If you depend on one, read the changelog before you upgrade.

| Surface | What holds it steady today |
| --- | --- |
| The HTTP API of `codify serve` | [`contract/openapi.json`](../../contract/openapi.json) is committed, and a test fails when the server's schema differs from it, so a route change shows in the pull request. |
| The MCP tool names of `codify mcp` | `codify.mcp.TOOL_NAMES`, checked by `tests/test_mcp.py` against the tools the server lists. |
| The files `ingest-one` writes into a bundle | nothing beyond the tests of each file. See the [bundle reference](../reference/bundle.md). |

## The downstream contract

[`codify/contract/product_surface.txt`](../../codify/contract/product_surface.txt) lists,
one `module:symbol` per line, everything the hosted Platform imports.
`tests/contract/test_product_surface.py` imports each entry, so a rename or removal fails
here, not downstream after a release. The `contract` job in
[`.github/workflows/downstream-platform.yml`](../../.github/workflows/downstream-platform.yml)
also runs the Platform's own contract test against the change; without a read token for
the Platform repository it records that it did not run and passes.

The manifest lists what one consumer uses; the policy covers every public module, listed
or not. The manifest also names a few underscore names (a symbol in `codify.akn._schema`,
and underscore-prefixed functions in public modules). They cross the boundary, but the
policy promises nothing about them.

## Version numbers

| Part | Moves for |
| --- | --- |
| Major | any break in the table above, including output that changes while signatures do not |
| Minor | new capability or new data, such as a command or a jurisdiction config |
| Patch | everything else |

While the major version is 0, the minor acts as the major and can contain breaks. Pin one
minor range, the one you have tested against (the release on PyPI can be older than the
source tree):

```toml
dependencies = ["codify-core>=0.7,<0.8"]
```

The [changelog](../../CHANGELOG.md) lists every break, numbered, under its release, with
unreleased changes under `Unreleased` at the top. A version is released when its git tag
exists. The first tag, `v0.4.0`, is the launch tag; earlier versions were never tagged.
After it, a dated heading with no tag is a cut not yet published.

## How a release is published

Pushing a tag of the form `v<version>` starts
[`.github/workflows/release.yml`](../../.github/workflows/release.yml), which:

1. Runs every job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) against
   the tag: lint, types, the offline suite, the database suite and the build check.
2. Fails with `tag <tag> != version <version>` unless the tag, without its `v`, equals
   `version` in `pyproject.toml`, so a mistyped tag publishes nothing.
3. Builds the wheel and source distribution, rejects either if it contains generated or
   private files, and imports the wheel in isolation to prove it carries its configs and
   framework data.
4. Publishes to PyPI only when the repository variable `PUBLISH_TO_PYPI` is `true`, through
   trusted publishing, which mints a short-lived token for the run; no API key is stored.

The tag is the record of what a release contains.

## Status

| Capability | Status |
| --- | --- |
| Written compatibility policy for modules, command, config, events and schema | **Available**: [`VERSIONING.md`](../../VERSIONING.md) |
| Compatibility promise for the HTTP API, MCP tools and bundle files | **Not in the core**: the policy does not cover them yet; tests guard them as described above |
| Tag-driven release with checks and trusted publishing | **Available**: `release.yml` |

## Next

[Security](security.md): how to report a vulnerability, and what the software protects.
