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

| Area                                                       | Current behaviour                                                                                         | Required change                                                                  |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `codify/storage/models.py`                                 | Work and expression URIs are globally unique. Source hashes identify source rows.                         | Make URI and hash uniqueness local to a corpus.                                  |
| `codify/storage/sources.py`                                | Upload deduplication uses a hash; inherited-source lookup walks version ancestry.                         | Scope deduplication and every lineage hop; return only the owned source.         |
| `codify/storage/repository.py`                             | Save, reuse and replacement select laws by work URI; repeat detection also considers language and source. | Apply scope before selecting, locking, reusing or replacing a row.               |
| `codify/storage/versions.py`                               | Version lookup, listing, lineage and amendment persistence use document identifiers.                      | Require scope on entry; preserve it through derived versions and cursor lookups. |
| `codify/storage/documents.py`                              | Document projection loads body rows for a version.                                                        | Resolve the scoped version before loading its body.                              |
| `codify/storage/repair.py`                                 | Source retrieval can select a source directly by hash.                                                    | Use the owned source identity, including repair evidence reads.                  |
| `codify/storage/lexicon.py`                                | Expansion terms and counts come from a shared lexicon.                                                    | Scope terms, counts, writes, rebuilds and pruning.                               |
| `codify/storage/retrieval.py`, `codify/retrieve/hybrid.py` | Search combines filters and candidate limits.                                                             | Apply corpus constraints before candidate selection, ranking and pagination.     |
| `codify/storage/partitions.py`                             | Embeddings are partitioned by jurisdiction.                                                               | Keep partitioning a performance choice, never an ownership boundary.             |
| `codify/cli_store.py`, `codify/serve/app.py`               | Standalone entry points persist without corpus selection.                                                 | Require explicit configured corpus selection.                                    |

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
source retrieval together, including their derived-row writes. Scope lexicon writes
in the same slice so persisting private text cannot populate a shared vocabulary.
Search exposure remains disabled for multiple corpora until its readers are scoped.

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
9. Remove each scope predicate or relationship constraint in controlled mutations;
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
2. Backfill only from that approved map. Country, filename, source hash and last writer
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
