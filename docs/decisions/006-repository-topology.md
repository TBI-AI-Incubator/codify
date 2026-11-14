# Repository topology

This repository builds the `codify-core` Python distribution, imported as
`codify`. The hosted platform is a separate consumer of that distribution and
owns its HTTP services, workflow orchestration and access controls.

Dependencies point from applications to this library. Core must not import
the platform's packages or require a neighbouring platform checkout. Tests and
build scripts use paths within this repository or explicit caller-provided paths.

The tree is a library, a CLI, optional servers and a local web app.

## Status note, 14 November 2026

The dependency rule holds: nothing under `codify/`, `scripts/` or `tests/`
imports a platform package.

The first paragraph describes the hosted platform's own services, not an
absence of services in the core. The core's server (`codify serve`) has no
authentication and keeps runs in memory, so access control and durable
orchestration remain outside the core.

One helper still assumes an earlier layout: `_CHAINS` in `codify/testing.py`
names migration directories that are not in this repository, so the check that
uses them finds nothing to compare here.
