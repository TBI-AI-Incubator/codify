# Security reporting

Report privately through the repository's Security tab ("Report a
vulnerability"), never in a public issue. Send the class of problem and a
minimal, non-destructive way to reproduce it against a local install. Leave out
credentials, personal data, customer documents and exploit code aimed at a
running system.

Supported releases: `main` and the latest minor release on PyPI. Fixes land on
`main` first and ship in the next release; the advisory names the fixed version.

`codify serve`, `codify mcp --http` and the web app have no authentication:
anyone who reaches them can read every stored law and trigger model calls billed
to you. Both servers bind to `127.0.0.1` unless given `--host`.
[Security](docs/project/security.md) in the documentation says what the core
protects against and what it leaves to your deployment.

Keep model-provider and database credentials out of source control, and never
expose the development Compose database beyond your own machine.
