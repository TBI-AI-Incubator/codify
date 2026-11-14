# Notebooks

Learn to structure legal documents, search a small corpus and compare laws in Python.
The notebooks include saved outputs, so you can read them without running the code.
All sample laws are fictional.

| Notebook | What you will do | Requirements |
| --- | --- | --- |
| [Codify 101](codify-101.ipynb) | Convert text to Akoma Ntoso, inspect the document and validate its structure | Sections 1 to 4 and the scan in section 5 run offline; the rest of section 5 needs a chat model |
| [Codify 201](codify-201.ipynb) | Store laws in PostgreSQL, search provisions and use HTTP and MCP | A local database with migrations applied. Sections 1 and 3 need nothing else; the rest need an embeddings endpoint |
| [Codify 301a](codify-301a.ipynb) | Test jurisdiction rules and see how a configuration change affects the output | Sections 1 to 3 and the scan in section 5 run offline; section 4 and the last cell of section 5 need a chat model |
| [Codify 301](codify-301.ipynb) | Compare a document with a reference instrument and inspect the findings | Section 1 runs offline; the rest need chat and embeddings endpoints. Section 4 also needs the database from Codify 201 |

## Setup

The notebooks need a source checkout. They read sample laws from
`tests/fixtures/synthetic`, which a package installed from PyPI does not carry.

From the repository root:

```bash
uv sync --group dev --extra migrations --extra serve --extra mcp
```

Open a notebook in VS Code or another notebook editor and select the repository's
`.venv` as the Python kernel. Run its cells in order.

For model-assisted sections, copy `.env.example` to `.env` and configure the
endpoints you plan to use. Calls may incur charges; cost depends on the model,
document size and retries. See the
[environment reference](../reference/environment.md) for the chat and embeddings
settings.

For Codify 201, use a dedicated local database. The notebook writes sample records:

```bash
docker compose up -d --wait postgres
uv run codify migrate
```

Docker runs PostgreSQL here, not the notebook or API server.
See [serve and publish](../guide/serve-and-publish.md) for API, MCP and web app setup.

## What the saved outputs show

A cell that needs no model and no embeddings endpoint was last run on 14 November
2026, on code after the 0.7.0 release. Its output is what the code printed then.

A cell that calls a chat model or an embeddings endpoint keeps the output of an
earlier run, and so does any later cell that prints its result. Those outputs are
real but older than the code. Two differences are known:

- Codify 201, section 2: the saved output shows 58 provisions embedded for the
  Legislation Act, 1992. The same act is now stored as 62 provisions.
- Codify 301a, last cell: the manifest's coverage block now also carries `state`
  and `anchor_ratio`, and can carry `body_fill` and `pages`.

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
