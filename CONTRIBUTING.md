# Contributing

The [code of conduct](CODE_OF_CONDUCT.md) governs participation. There is no
contributor licence agreement.

Use Python 3.12 or newer and install with
`uv sync --frozen --group dev --extra migrations --extra serve --extra mcp`.
[Testing](docs/project/testing.md) gives every CI check as a local command;
[Contributing](docs/project/contributing.md) lists the files a change must also
update (changelog, HTTP contract, product-surface list, baselines, notebooks).

Keep the core independent of applications built on it. Add focused regression
tests for changed behaviour. Write new fixtures from invented documents; do not
commit customer data, source scans, operational identifiers, credentials or
private catalogue content. A public source is not thereby free to redistribute.

Only configs flagged `synthetic` or `public_reference` are packaged; see
[Write a jurisdiction config](docs/guide/write-a-jurisdiction-config.md).

Use concise comments, British English prose and American English identifiers.
Sign off with `git commit -s` under the Developer Certificate of Origin
(https://developercertificate.org/). The project is under Apache 2.0;
third-party components keep their own terms.
