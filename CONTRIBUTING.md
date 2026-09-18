# Contributing

Participation is governed by the [code of conduct](CODE_OF_CONDUCT.md).

Use Python 3.12 or newer and install with
`uv sync --group dev --extra migrations`. Run the offline tests described in
[the test guide](docs/offline-suite.md). CI also checks Ruff, strict source typing
and a wheel build; its exact commands are in `.github/workflows/ci.yml`.

Keep core independent of applications. Add focused regression tests for changed
behaviour. Use invented documents for new fixtures; do not commit customer data,
source scans, operational identifiers, credentials or private catalogue content.
Public source availability alone does not establish redistribution permission.

For configurations, see [adding a jurisdiction](docs/jurisdictions/adding-a-jurisdiction.md).
Only configurations flagged `synthetic` or `public_reference` are packaged; the
guide explains both flags.

Use concise comments, British English prose and American English identifiers.
Sign off contributions with `git commit -s` under the Developer Certificate of
Origin (https://developercertificate.org/). The project licence is Apache 2.0;
third-party components retain their own terms.
