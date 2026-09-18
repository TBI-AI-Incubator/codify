# Working on Codify

Conventions for changing this repository, the standalone Python core published
as `codify-core`. Do not import hosted application code or assume a
neighbouring repository. Keep generic plugin interfaces independent
of private detector implementations and catalogues.

Use focused regression tests, strict source typing and the checks in
`.github/workflows/ci.yml`. See `docs/offline-suite.md` for test scope. Never run
database tests against shared data or invoke paid providers without permission.

New fixtures must be independently invented. Do not include customer identifiers,
operational history, private source documents or credentials. Preserve useful
assertions when replacing fixtures; do not make failures disappear with skips.

Use concise comments, British English prose and American English identifiers.
AKN schema validity is a structural check, not a promise of accurate legal
content or universal compatibility with downstream products.
