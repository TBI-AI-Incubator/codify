# Notebooks

Learn to structure legal documents, search a small corpus and compare laws in Python.
The notebooks include saved outputs, so you can read them without running the code.
All sample laws are fictional.

| Notebook | What you will do | Requirements |
| --- | --- | --- |
| [Codify 101](codify-101.ipynb) | Convert text to Akoma Ntoso, inspect the document and validate its structure | Runs offline until the final section, which needs a chat model |
| [Codify 201](codify-201.ipynb) | Store laws in PostgreSQL, search provisions and use HTTP and MCP | A local database with migrations applied and an embeddings endpoint |
| [Codify 301a](codify-301a.ipynb) | Test jurisdiction rules and see how a configuration change affects the output | Runs partly offline; sections 4 and 5 also need a chat model |
| [Codify 301](codify-301.ipynb) | Compare a document with a reference instrument and inspect the findings | Chat and embeddings endpoints; no database |

## Setup

From the repository root:

```bash
uv sync --group dev --extra migrations --extra serve --extra mcp
```

Open a notebook in VS Code or another notebook editor and select the repository's
`.venv` as the Python kernel. Run its cells in order.

For model-assisted sections, copy `.env.example` to `.env` and configure the
endpoints you plan to use. Calls may incur charges; cost depends on the model,
document size and retries. See [storage setup](../usage.md#store-search-compare)
for embeddings settings.

For Codify 201, use a dedicated local database. The notebook writes sample records:

```bash
docker compose up -d --wait postgres
uv run alembic -c alembic.ini upgrade head
```

Docker runs PostgreSQL here, not the notebook or API server.
See [interfaces](../interfaces.md) for API, MCP and web app setup.

## Updating saved outputs

After changing executable examples, run the affected notebooks against a dedicated
local database and review the outputs before committing them. From the repository root:

```bash
for nb in 101 201 301a 301; do
  uv run jupyter execute --inplace docs/notebooks/codify-$nb.ipynb
done
```

The dev dependencies include `nbclient`, which provides `jupyter execute`.
These runs can call model providers, so CI does not execute them.

Remove credentials, local paths and incidental timestamps from saved outputs.
Bluebell uses the run date in FRBR expression identifiers. That date may change
between runs, and rerunning Codify 201 on a later day creates new versions.
Prose-only edits do not need model calls or refreshed outputs.
