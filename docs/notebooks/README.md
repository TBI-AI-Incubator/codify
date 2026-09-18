# Notebooks

Four walks through the library, in order. Each runs top to bottom on a clean clone with
`uv sync --group dev --extra migrations --extra serve --extra mcp` and a `.env` copied from
`.env.example` with a key added. The material is synthetic (`xa`, `xu`) so nothing quotes
a real statute book.

| Notebook | Covers | Needs |
| --- | --- | --- |
| [Codify 101](codify-101.ipynb) | Jurisdiction config, Bluebell to Akoma Ntoso, the typed document, validation, the anchor scan and model body-fill | A chat model for the last section; everything before it is offline |
| [Codify 201](codify-201.ipynb) | Loading a corpus into Postgres, embedding, hybrid search, the HTTP routes and the MCP tools in-process | The compose Postgres, migrated; an embeddings endpoint |
| [Codify 301a](codify-301a.ipynb) | What a jurisdiction configuration declares, one act scanned under five of them, calendar and era, structuring under three, then one rule edited in a scratch data root and what moved | A chat model for sections 4 and 5; the rest is offline |
| [Codify 301](codify-301.ipynb) | Comparing one act against a reference instrument, provision by provision, and reading the report | A chat model and an embeddings endpoint |

Model spend for all four is a few cents.

## Re-executing

The outputs are committed so the notebooks read without running. After a change to what
they call, re-execute in order against a fresh database and commit the outputs:

```
docker compose down -v && docker compose up -d --wait postgres
uv run alembic -c alembic.ini upgrade head
for nb in 101 201 301a 301; do
  uv run jupyter execute --inplace docs/notebooks/codify-$nb.ipynb
done
```

`jupyter execute` comes with `nbclient`, which the dev group installs. Check the outputs
carry no key, home path or run-specific timestamp before committing. There is no CI job:
the run costs model calls and the outputs are the record.

The FRBR expression date in the outputs is the day of the run: Bluebell stamps the
generation date at parse time, so it moves on every re-execution and is not a defect.
