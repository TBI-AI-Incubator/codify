# Repository topology

This repository builds the `codify-core` Python distribution, imported as
`codify`. The hosted platform is a separate consumer of that distribution and
owns its HTTP services, workflow orchestration and access controls.

Dependencies point from applications to this library. Core must not import
the platform's packages or require a neighbouring platform checkout. Tests and
build scripts use paths within this repository or explicit caller-provided paths.

The current extraction is a library and CLI. A standalone web interface is
separate work and is not included in this tree.
