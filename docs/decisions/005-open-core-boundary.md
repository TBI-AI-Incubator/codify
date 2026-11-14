# Core and plugin boundary

Codify contains legal-document acquisition, structuring, validation, storage,
retrieval, comparison, structure-preserving translation and generic plugin
interfaces. It runs without the hosted platform.

Translation is core by decision (18 September 2026): the batching, clause
parity and audit passes are part of producing a citable expression, not a
hosted service around one.

Commercial detector implementations, private catalogues and customer corpora
are not included. Applications may register their own lenses and supply their
own remediation templates. Generic storage does not load a private catalogue.

A jurisdiction configuration is included only when marked `synthetic` or
`public_reference`. That flag controls packaging, not the licence of documents
subsequently downloaded from a source.

## Status note, 14 November 2026

The boundary holds. The text above leaves out four details:

- The plugin mechanism is the `codify.lenses` entry-point group. A package that
  declares an entry point in that group is loaded at the first lookup
  (`codify/lenses/registry.py`), and `codify.lenses.register` adds a lens
  directly. An install of the core alone registers no lens.
- The capabilities list predates the HTTP server, the MCP server and the web
  app (`codify serve`, `codify mcp`, `apps/web`), which are now in this
  repository.
- The package carries more data than jurisdiction configurations: the registry
  cut to the shipped codes, `supranational_bodies.json` and `data/frameworks/`
  (`hatch_build.py`).
- No catalogue rows ship, but the core migration chain creates three empty
  catalogue tables, with the permitted remediation template names fixed in
  check constraints (`codify/migrations/versions/0001_core_baseline.sql`). The
  shape of that catalogue is in the core; its content is not.
