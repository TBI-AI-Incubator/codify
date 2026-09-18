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
