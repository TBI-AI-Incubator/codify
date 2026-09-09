# Repository topology

This repository builds the `codify-core` Python distribution, imported as
`codify`. Codify Platform is a separate consumer of that distribution and owns
its hosted HTTP services, workflow orchestration and access controls.

Dependencies point from applications to this library. Core must not import
Platform packages or require a neighbouring Platform checkout. Tests and build
scripts use paths within this repository or explicit caller-provided paths.

The current extraction is a library and CLI. A standalone web interface is
separate work and is not included in this tree.
