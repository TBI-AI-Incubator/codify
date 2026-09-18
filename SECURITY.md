# Security reporting

Report a suspected vulnerability privately through GitHub's private vulnerability
reporting on this repository (Security tab, "Report a vulnerability"). Until the
repository is public and that form is enabled, report directly to a repository
maintainer through the private collaboration channel. Do not open a public issue for it, and do not include credentials, personal data or a working
exploit in the report; a description of the class of problem and a way to reproduce it
are enough.

Supported release: the latest minor on PyPI and `main`. Fixes for a reported problem
land on `main` first and ship in the next release; the advisory names the fixed
version.

Keep model-provider credentials and database credentials outside source control, and
never expose the development Compose database beyond your own machine.
