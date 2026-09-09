# Codify's Postgres: pgvector plus pg_textsearch, which supplies the BM25 index
# access method the lexical search arm ranks on.
#
# Built from source rather than the upstream release tarball: v1.4.0 ships only
# an x86-64 binary, having dropped the arm64 archives v1.3.1 carried, and local
# development is Apple Silicon. It is plain C, so the build is cheap and needs
# no Rust toolchain.
#
# Base and source are both pinned by hash. This image is the datastore for a
# corpus of national law; it should not be the least verified artefact in the
# stack. `docker-compose.yaml` passes `shared_preload_libraries=pg_textsearch`,
# so a base without the extension will refuse to start rather than serve
# unranked results.
FROM pgvector/pgvector:pg18@sha256:2ba9ca5f2e7daa0f0e7723cba1ee9167bab54efd3640516a44ac1a928dd67e7a

ARG PG_TEXTSEARCH_VERSION=1.4.0
ARG PG_TEXTSEARCH_SHA256=cab02b4e8af11f98b428d17cca9c5235ab55e12d79c20fa8fbb950708c4a1e92

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential postgresql-server-dev-18 ca-certificates curl; \
    curl -fsSL -o /tmp/pg_textsearch.tar.gz \
        "https://github.com/timescale/pg_textsearch/archive/refs/tags/v${PG_TEXTSEARCH_VERSION}.tar.gz"; \
    echo "${PG_TEXTSEARCH_SHA256}  /tmp/pg_textsearch.tar.gz" | sha256sum -c -; \
    mkdir -p /tmp/pg_textsearch; \
    tar xzf /tmp/pg_textsearch.tar.gz -C /tmp/pg_textsearch --strip-components=1; \
    make -C /tmp/pg_textsearch; \
    make -C /tmp/pg_textsearch install; \
    install -Dm644 /tmp/pg_textsearch/LICENSE /usr/share/doc/pg_textsearch/LICENSE; \
    install -Dm644 /tmp/pg_textsearch/NOTICE /usr/share/doc/pg_textsearch/NOTICE; \
    apt-get purge -y --auto-remove build-essential postgresql-server-dev-18 curl; \
    rm -rf /var/lib/apt/lists/* /tmp/pg_textsearch /tmp/pg_textsearch.tar.gz

# The pg_textsearch source tree, and with it upstream's LICENSE and NOTICE, is
# gone above. Its PostgreSQL Licence permits this redistribution only while the
# copyright notice and licence paragraphs travel with every copy, so keep both
# in the image and ship the repo-wide notices too (Tiger Data holds the
# pg_textsearch text; licenses/PostgreSQL.txt is the PGDG copy for libpq).
COPY THIRD-PARTY-NOTICES.md /THIRD-PARTY-NOTICES.md
COPY licenses/ /licenses/
