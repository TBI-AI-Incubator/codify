# Synthetic scanner baseline

`xz-fixture-scan.json` records the current deterministic scanner output for the
five text fixtures under `tests/fixtures/corpus`, using the included synthetic
Zerzura (`xz`) configuration. It is a regression baseline, not a measurement of
real legal data or a claim of extraction accuracy.

The fixtures exercise an invented publication act, unreadable text, damaged
keywords, mixed marker forms and a table of contents followed by body text.
The test also requires all five files, so a missing fixture cannot silently
reduce coverage. No model or database is used.

Regenerate from the repository root after reviewing an intentional scanner
change:

```bash
uv run codify scan-corpus tests/fixtures/corpus --jurisdiction xz > docs/corpus-baselines/xz-fixture-scan.json
uv run pytest tests/quality/test_fixture_baseline.py -q
```

Review the measurement diff before accepting a new baseline. Successful
regeneration alone does not establish that changed behaviour is correct.
