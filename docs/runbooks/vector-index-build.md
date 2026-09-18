# Building the per-jurisdiction vector indexes

`provision_embeddings` is partitioned by `jurisdiction_id` (core migration 0020).
The parent index `provision_embeddings_hnsw_idx` is partitioned too. The DEFAULT
partition, created after it, carries its own HNSW from the start; partitions for
the jurisdictions that existed when 0020 ran do not, because a build over millions
of vectors takes hours and a migration must not hold that lock. Until a partition's
index is attached, searches on it fall back to the exact scan they ran before.

A jurisdiction created after 0020 writes to DEFAULT. When it is large enough to
want its own partition, a migration runs `codify.storage.partitions.promote_sql`
in one transaction with writers drained: the ATTACH clones the parent's foreign
keys under a lock that waits on every open writer of `provisions`.

Each step below runs outside a transaction (`CONCURRENTLY` demands it), so run
it from `psql` with autocommit, not from a migration. Time it away from deploys
and drain writers to the partition first if the deploy guard watches for long
transactions: a concurrent build holds no transaction open past its own scans,
but it is a long-running statement.

## Which partitions still need one

```sql
SELECT j.code, c.relname AS partition, pg_size_pretty(pg_relation_size(c.oid)) AS size,
       c.reltuples::bigint AS rows,
       EXISTS (SELECT 1 FROM pg_index i JOIN pg_inherits h ON h.inhrelid = i.indexrelid
               WHERE i.indrelid = c.oid
                 AND h.inhparent = 'provision_embeddings_hnsw_idx'::regclass) AS attached
FROM pg_inherits p JOIN pg_class c ON c.oid = p.inhrelid
JOIN jurisdictions j ON c.relname = 'provision_embeddings_p_' || replace(j.id::text, '-', '')
WHERE p.inhparent = 'provision_embeddings'::regclass
ORDER BY c.reltuples DESC;
```

## Build and attach, one partition at a time

```sql
SET maintenance_work_mem = '2GB';
SET max_parallel_maintenance_workers = 4;
CREATE INDEX CONCURRENTLY provision_embeddings_p_<id>_hnsw
    ON provision_embeddings_p_<id>
    USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64);
ALTER INDEX provision_embeddings_hnsw_idx ATTACH PARTITION provision_embeddings_p_<id>_hnsw;
ANALYZE provision_embeddings_p_<id>;
```

Roughly 1.5 KB of index per vector: a 100k-row partition is minutes, ten
million rows is hours. Start with the small ones; each attach serves that
jurisdiction's searches at once. `pg_stat_progress_create_index` shows where a
build is. A build interrupted by a restart leaves an `INVALID` index; drop it and
rerun. The parent turns valid on its own once every partition has attached.

## Check

`EXPLAIN` of a scoped search names `Index Scan using provision_embeddings_p_<id>_hnsw on provision_embeddings_p_<id>` (a partition created after the parent index carries an auto-named `_embedding_idx`, trimmed to 63 characters);
before the build it names the partition's `version_id` btree and a sort.
