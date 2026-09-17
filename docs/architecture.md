# Architecture

The CLI and Python API share the same processing modules:

1. Acquisition adapters fetch documents, or a caller supplies PDF/text bytes.
2. Jurisdiction configuration identifies document structure and anchors.
3. The pipeline constructs a skeleton and fills content, using configured model
   providers where required.
4. AKN validation and quality checks produce findings for review.
5. Storage, embedding and retrieval are optional application integrations. Embeddings
   are partitioned by jurisdiction, one vector index each, so a scoped search reads
   one partition; `docs/runbooks/vector-index-build.md` builds them on a populated
   database.

Model-assisted processing can introduce errors. Retain source material and
inspect validation findings before relying on structured output. Schema validity
alone does not establish legal accuracy or complete transcription.

`codify/lenses` exposes generic extension interfaces. Detector implementations
and private catalogues are supplied by applications. Core has no hosted service,
tenancy system or web interface in this tree.
