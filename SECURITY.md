# Security reporting

Where to report: if the Security tab offers "Report a vulnerability", use it. If it
does not, report directly to a maintainer listed in `.github/CODEOWNERS`.

What to send: a description of the class of problem and a minimal, non-destructive
way to reproduce it against a local install.

What not to send: credentials, personal data, customer documents, or exploit code
aimed at a running system. Do not open a public issue for a suspected vulnerability.

Supported release: `main`, and the latest minor on PyPI once one is published.
Fixes land on `main` first and ship in the next release; the advisory names the
fixed version.

Keep model-provider and database credentials outside source control, and never
expose the development Compose database beyond your own machine.
