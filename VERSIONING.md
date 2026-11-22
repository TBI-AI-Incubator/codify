# Versioning

What this package promises as a library, a command and a schema, and what breaks
each promise. It publishes as `codify-core` and imports as `codify`; the version
is the distribution's. [Versioning](docs/project/versioning.md) in the
documentation explains the policy and how a release is published.

## The public surface

- **Modules.** Everything under `codify.` not starting with an underscore.
  `codify/contract/product_surface.txt` lists the symbols downstream
  applications import, and a test checks it. The list also names a few
  underscore names (the module `codify.akn._schema` and some underscore
  functions); they cross the boundary but are not surface. Breaks: removing or
  renaming a symbol, changing a parameter's name, order or meaning, inserting
  one ahead of an existing parameter, widening a return type.
- **The command.** `codify`, every subcommand `codify --help` lists, and the
  output each writes. Breaks: removing a subcommand or a flag, changing a flag's
  meaning or the shape of the output.
- **Jurisdiction config.** `JurisdictionConfig`, `ResolvedConfig` and the
  `config.json` files the package carries. Breaks: removing a field, making an
  optional field required, reinterpreting a field, removing a shipped
  jurisdiction.
- **Events.** `ProgressEvent` and the domain events written through
  `codify.storage`. Breaks: removing a field, renaming an event, changing when
  one fires.
- **Database schema.** The core migration chain, `alembic_version_core`, and
  every object it owns: tables, columns, defaults, indexes, triggers and
  functions. Breaks: dropping or renaming any of them, narrowing a type,
  dropping a default, or adding a constraint, which can reject writes that were
  allowed even when every existing row passes.

A purely additive change is not a break. A new parameter is additive only when
it is optional and goes at the end or is keyword-only.

This policy does not yet name the HTTP API of `codify serve`, the MCP tool
names of `codify mcp` or the files `ingest-one` writes. Tests guard the first
two (`contract/openapi.json`, `codify.mcp.TOOL_NAMES`); if you depend on any of
them, read the changelog before upgrading.

## Version numbers

Major for any break above, including output that changes while signatures do
not. Minor for new capability or data. Patch for the rest.

While the major is zero the minor acts as the major: pin one minor range, such
as `>=0.7,<0.8`. The [changelog](CHANGELOG.md) lists every break under its
release.
