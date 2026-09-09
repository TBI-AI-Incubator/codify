# Test scopes

Install development dependencies with `uv sync --group dev --extra migrations`.
The default GitHub test job runs:

```sh
uv run pytest tests -m "not integration and not live_llm" -q
```

This checks deterministic code without running the database or model-provider
suites. Tests requiring jurisdiction configurations omitted from this
publication retain explicit skips; a pass does not validate those configurations.
No historic monorepo pass count is a result for this repository.

Database tests need a disposable PostgreSQL instance with pgvector and
pg_textsearch. Build the supplied local development image with
`docker compose up --build -d --wait postgres`. Set an
explicit `POSTGRES_URL` pointing only to a disposable test database, apply
`uv run alembic -c alembic.ini upgrade head`, and run the selected integration
tests with `REQUIRE_DB=1`. Some tests commit or recreate data. Do not use a shared
or production database. Database integration is not exercised by the default CI.

`live_llm` tests are separate and require configured providers; they can incur
charges. Neither offline tests nor schema checks certify the accuracy of a
real legal corpus.
