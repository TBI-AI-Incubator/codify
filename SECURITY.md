# Security reporting

Where to report: GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability"). Until the repository is public and that
form is enabled, report directly to a maintainer listed in `CODEOWNERS`.

What to send: a description of the class of problem and a way to reproduce it.

What not to send: credentials, personal data, customer documents, or a working
exploit. Do not open a public issue for a suspected vulnerability.

Supported release: the latest minor on PyPI and `main`. Fixes land on `main` first
and ship in the next release; the advisory names the fixed version.

Keep model-provider and database credentials outside source control, and never
expose the development Compose database beyond your own machine.
