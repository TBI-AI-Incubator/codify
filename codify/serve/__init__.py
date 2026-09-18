"""The thin HTTP server over the library. `create_app` builds it; `codify serve` runs it."""

from codify.serve.app import create_app

__all__ = ["create_app"]
