# Test markers

Two markers gate tests that need external services. Combine with pytest's `-m`
expression syntax to opt in or out.

| Marker        | Requires                                   | CI behaviour                                                                                                                        |
| ------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `integration` | Postgres (and any tables migrated up)      | included in CI for both roots (service Postgres + migrations)                                                                       |
| `live_llm`    | A reachable LLM (LiteLLM gateway → Gemini) | on-demand `live-llm` workflow (manual dispatch + version tags; LiteLLM container, GEMINI_API_KEY secret); excluded from per-push CI |

Common selectors:

```bash
# What CI runs for packages/codify (pure unit; no DB, no LLM):
uv run pytest packages/codify/tests -m "not integration and not live_llm"

# Local with docker-compose Postgres up; no live LLM:
uv run pytest packages/codify/tests -m "not live_llm"

# Live LLM (LiteLLM gateway up with GEMINI_API_KEY set; embeddings via gemini-embedding-2):
uv run pytest packages/codify/tests -m "live_llm"
```

`codify.testing.postgres_url()` and `codify.testing.ollama_host()` are the
shared helpers for fixtures that talk to those services. Use them; don't
re-roll the env-var lookup per file.
