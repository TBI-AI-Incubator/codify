# Corpus ownership

Status: proposed storage contract. The current storage API does not enforce these
boundaries. This document adds no schema changes or access controls.

## Boundary

A corpus is an independently owned collection of documents. Its identifier is an
opaque UUID. A jurisdiction describes a document; it does not identify the owner.
Two corpora may contain the same legal URI or identical source bytes independently.

Core owns corpus records and relationship integrity. A calling application decides
which corpus an authenticated caller may access. Core must not import an application's
users, organisations, roles or authentication system. Possessing a corpus UUID is not
proof of permission. Standalone applications also select a corpus explicitly.

This first design has no shared-content exception, corpus inheritance or implicit
cross-corpus access. An explicit copy creates independent records and provenance.
Public jurisdiction configuration and parsing rules may remain shared.

## Existing storage contracts

Paths below are relative to the repository root. These are source observations,
not claims about any running installation.

| Area                                                       | Current behaviour                                                                                         | Required change                                                                       |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `codify/storage/models.py`                                 | Work and expression URIs are globally unique. Source hashes identify source rows.                         | Make URI and hash uniqueness local to a corpus.                                       |
| `codify/storage/sources.py`                                | Upload deduplication uses a hash; inherited-source lookup walks version ancestry.                         | Scope deduplication and every lineage hop; return only the owned source.              |
| `codify/storage/repository.py`                             | Save, reuse and replacement select laws by work URI; repeat detection also considers language and source. | Apply scope before selecting, locking, reusing or replacing a row.                    |
| `codify/storage/versions.py`                               | Version lookup, listing, lineage and amendment persistence use document identifiers.                      | Require scope on entry; preserve it through derived versions and cursor lookups.      |
| `codify/storage/documents.py`                              | Document projection loads body rows for a version.                                                        | Resolve the scoped version before loading its body.                                   |
| `codify/storage/repair.py`                                 | Source retrieval can select a source directly by hash.                                                    | Use the owned source identity, including repair evidence reads.                       |
| `codify/storage/lexicon.py`                                | Expansion terms and counts come from a shared lexicon.                                                    | Scope terms, counts, writes, rebuilds and pruning.                                    |
| `codify/storage/retrieval.py`, `codify/retrieve/hybrid.py` | Search combines filters and candidate limits.                                                             | Apply corpus constraints before candidate selection, ranking and pagination.          |
| `codify/storage/partitions.py`                             | Embeddings are partitioned by jurisdiction.                                                               | Keep partitioning a performance choice, never an ownership boundary.                  |
| `codify/cli_store.py`, `codify/serve/app.py`               | Standalone entry points persist without corpus selection.                                                 | Require explicit configured corpus selection.                                         |
| `codify/storage/laws.py`                                   | Lists, URI/UUID reads, existence checks, cursors, counts and facets select corpus content.                | Require corpus scope for all readers, including cursor resolution and aggregation.    |
| `codify/storage/acquisitions.py`, `codify/storage/runs.py` | Acquisition and job records can exist without a document.                                                 | Store required corpus identity at creation; scope deduplication, reads and mutations. |
| `codify/storage/page_reads.py`                             | Page evidence can outlive its run without being attached to a version.                                    | Preserve independent corpus ownership when either optional parent disappears.         |
| `codify/storage/events.py`                                 | Audit events have no ownership foreign key; feeds and action counts can span the table.                   | Persist corpus ownership; scope writes, feeds, cursors, counts and entity enrichment. |

This inventory identifies the initial path and its dependencies. It is not an
exhaustive audit of every storage function. Each subsequent surface needs its own
read, write and relationship checks before it is enabled for multiple corpora.

## Identity and constraints

The intended end state is:

| Table              | Identity and uniqueness                                                   | Relationships                                           |
| ------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------- |
| `corpora`          | UUID primary key                                                          | No application-owned foreign keys                       |
| `laws`             | Existing UUID; unique `(corpus_id, frbr_work_uri)` and `(id, corpus_id)`  | Non-null corpus                                         |
| `versions`         | Existing UUID; unique `(corpus_id, expression_uri)` and `(id, corpus_id)` | Non-null corpus; law and optional parent in that corpus |
| `source_documents` | New UUID; unique `(corpus_id, sha256)` and `(id, corpus_id)`              | Non-null corpus                                         |

A version references its source UUID rather than using the source hash as identity.
Hashes remain integrity and deduplication values. Original filenames, source metadata
and object references belong to the corpus, even when file bytes match another corpus.
Do not rewrite legal URIs or embed corpus identifiers into AKN to obtain uniqueness.

Composite foreign keys pair the referenced UUID with the owning row's non-null
`corpus_id`. Their targets need the composite unique keys listed above. Optional
parent/source UUIDs may be null; corpus ownership may not. Do not use an unqualified
composite `ON DELETE SET NULL` that would also clear `corpus_id`. Prefer restricting
source/parent deletion and explicitly detaching the optional UUID in the same scoped
transaction; retain existing law-to-version cascade semantics only after testing the
complete dependent graph. Corpus deletion is outside this first change.

Child data with one ownership path can inherit it through its parent. Multi-parent
rows need constraints proving that every parent belongs to the same corpus. Examples
include provision-to-section links, resolved references, findings tied to runs and
versions, and feedback tied to provisions and findings. A single valid foreign key
is insufficient when another field can point outside the corpus.

Inheritance requires a non-null ownership path that survives for the row's lifetime.
Use these explicit rules for roots and records whose parents can disappear:

- `acquisitions`: non-null `corpus_id` referencing `corpora`; preserve the UUID primary
  key and add unique `(id, corpus_id)`. Deduplicate on
  `(corpus_id, jurisdiction_code, source_url, content_sha256)`; hash and URL lookups
  require corpus scope even before a version exists.
- `runs`: non-null `corpus_id` referencing `corpora`, assigned before any payload or
  artifact is written. Preserve run UUIDs and add unique `(id, corpus_id)`; an optional
  parent run uses a same-corpus composite foreign key. Actor and application identifiers
  are not substitutes for corpus identity. Retries retain the original corpus.
- `events`: non-null `corpus_id` referencing `corpora`, recorded at emission and
  retained after the referenced entity is deleted. Preserve the UUID primary key;
  actor, entity and payload identifiers do not establish ownership. Corpus event
  producers validate referenced domain entities in the same corpus and transaction;
  the free-form entity/payload fields cannot enforce that with a generic foreign key.
  Reads and enrichment joins must independently constrain corpus, including actor/entity
  feeds, recent feeds, cursor anchors and action counts. `reserve_actor_action` must
  use the same corpus in its lock key, count and insert. Application-wide audit events
  and account-wide rate limits belong in separate application storage, not a null-corpus
  exception or an accidental weakening of existing global limits. Audit ownership must
  not be cleared by entity deletion; use a restrictive corpus foreign key.
- `run_artifacts`: inherit ownership through the mandatory run foreign key, which
  cascades deletion. Every artifact read or mutation joins to the scoped run. Any future
  additional parent must agree with that corpus; a stored corpus column, if added, must
  have a composite foreign key to the run rather than independent unchecked values.
- `page_reads`: non-null `corpus_id` referencing `corpora` from creation, with unique
  `(id, corpus_id)`. Optional run and version references use same-corpus composite foreign
  keys. Keep per-run page uniqueness as `(corpus_id, run_id, page_number)` while attached;
  detached rows retain their UUID identity and are not deduplicated by page number alone.
  Run cleanup clears only `run_id` in the scoped transaction, never ownership. Existing
  version-deletion cascade behaviour can remain, with relationship tests. Reads, version
  attachment, disputes and retention cleanup require the page's corpus even when both
  parent IDs are null. Disputes inherit through their mandatory page reference.

Corpus ownership is immutable during ordinary updates. Moving content requires an
explicit copy, not reassigning a root and leaving dependent rows behind. Synthetic
upgrade validation must include roots without documents and page evidence without
surviving parents; missing provenance is not permission to select a default owner.

Derived data is content too. Vocabulary, embeddings, reference links, acquisition
records, retained text, review output and job artifacts must not become cross-corpus
lookup channels. Audit registry uniqueness and reference resolution separately;
matching legal identifiers must never silently join content from different corpora.

## API contract

Use a required keyword-only `corpus_id: UUID` on corpus storage entry points.
A UUID is sufficient for this boundary; a new session wrapper or permissions framework
is not required. Application access contexts may carry the UUID after authorisation.
There is no default corpus and no `None` meaning all corpora.

- Save and source upsert require the corpus before any lookup or write. Source upsert
  returns the owned source UUID. Duplicate detection is local to the corpus.
- Read by UUID or legal URI includes corpus in the query. A missing or foreign record
  returns the same empty result. No foreign row is loaded first for later filtering.
- Law listings, counts, facets, existence checks and cursor lookups apply corpus
  scope before pagination or aggregation, even when search is disabled.
- Supersede and repair constrain every selected row, lock and mutation by corpus.
  Existing compare-and-replace and replay checks remain additional requirements.
- Parent/source IDs are validated before mutation and constrained in the database.
  Concurrent changes cannot bypass the relationship rule.
- Recursive queries constrain their initial row and every recursive step. Pagination
  cursors and retrieval candidate sets are resolved within the same scope.
- Mutating functions retain the existing caller-owned transaction contract. They do
  not commit to manufacture an ownership boundary.

Cross-corpus maintenance is a separate, explicitly privileged application operation.
It must not be an optional flag on the ordinary storage API. Core cannot establish
caller authority by accepting an arbitrary corpus identifier.

## First implementation slice

Implement source persistence, document persistence, document projection and inherited
source retrieval together, including their derived-row writes and law readers.
Create required corpus ownership on acquisition/job roots and retained page evidence
before enabling those paths; the native-document slice cannot legitimise unscoped jobs. Scope lexicon writes
in the same slice so persisting private text cannot populate a shared vocabulary.
Every remaining reader or writer stays unavailable to multi-corpus callers until scoped;
disabling search alone does not protect law listings or job/artifact endpoints.

Use synthetic native AKN in fictional jurisdiction `xa`; no model calls are needed.
The acceptance cases are:

1. Save a source and document in corpus A; read both as A.
2. Read the same UUIDs and legal URIs as B; obtain no content or metadata.
3. Save identical bytes and legal URIs in B; get independent records without changing A.
4. Retry within A; preserve the documented reuse outcome without touching B.
5. Attempt cross-corpus source, law, parent and section associations, including direct
   SQL writes; the database rejects them.
6. Fail after source or body insertion; caller rollback leaves no partial document,
   retained text or vocabulary. Retry succeeds within the same corpus.
7. Derive a version and read an inherited source; the ancestry stays within its corpus.
8. Reuse a pooled connection in A then B; no implicit previous scope survives.
9. List laws, resolve cursors, query counts/facets and test existence in mixed holdings;
   neither corpus observes the other's records or aggregate contributions.
10. Record the same acquisition URL/hash in both corpora; deduplication stays local.
    Create jobs without versions and reject cross-corpus parent runs and page attachment.
11. Retain page evidence after failed-run cleanup with no version; verify its owner can
    read it and the other corpus cannot. Check artifact reads through their owning run
    and artifact deletion on run cleanup.
12. Emit events in both corpora with the same synthetic actor. Verify actor, entity and
    recent feeds, counts, cursor anchors and title enrichment remain scoped. Reject
    cross-corpus references on emission; retain the original owner after entity deletion.
    Exercise concurrent action reservations within and across corpora, and verify any
    separate application-wide limit still holds after the caller migration.
13. Remove each scope predicate or relationship constraint in controlled mutations;
    the corresponding isolation test fails.

Run both directions and mixed holdings. Test the actual PostgreSQL constraints and
queries against a disposable database; mock-only tests cannot establish enforcement.
Compile and inspect generated SQL offline as a supplement, not a substitute.

## Compatibility and rollout

This is a storage API change, not a backwards-compatible optional filter. Unscoped
callers must fail visibly rather than inherit a default corpus. Update the standalone
CLI, service, acquisition/import entry points, amendment and repair paths, tests and
public exports alongside the relevant signatures. Calling applications must carry
scope through background work and retries without changing recorded workflow identities
or silently assigning a corpus to an old serialised job.

Use new forward migrations from the current migration head. Do not edit published
migration ancestry. The baseline has build/adopt behaviour and does not support offline
SQL generation; validate both a fresh database and an upgraded synthetic database.

Plan rollout in separately validated stages:

1. Add corpus records and source UUIDs without claiming isolation. Inventory existing
   relationships and prepare an explicit assignment map supplied by the operator.
2. Backfill only from that approved map. Audit-event backfill needs an explicitly
   authorised migration procedure for the append-only trigger; test that normal
   update/delete attempts remain blocked afterwards. Events with deleted entities
   require independent provenance or block enforcement, never an inferred owner. Country, filename, source hash and last writer
   do not establish ownership. Ambiguous records block enforcement; the library must
   not silently assign, copy, expose or delete them.
3. Validate counts and relationships, replace global uniqueness, and enforce required
   ownership. Drain incompatible writers; deploy scope-aware consumers before allowing
   multiple corpora. A database shape alone does not make old readers safe.
4. Exercise application rollback only to a compatible scoped version. Once duplicate
   legal URIs or hashes exist across corpora, a downgrade to global uniqueness cannot
   preserve the data. Refuse that downgrade rather than collapse or delete records.

The exact migration revisions, locking duration, assignment procedure and release
compatibility range must be settled in the implementation PR. This proposal is not
an executable migration or a claim that a partial rollout is safe. Database row-level
security may provide another boundary later; it requires role, transaction and pool
validation and does not replace these query and relationship contracts.
