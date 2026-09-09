"""Registry of structured parsers, mirroring the acquisition adapter registry."""

from __future__ import annotations

from collections.abc import Callable

from codify.pipeline.parsers.base import (
    AmendmentAnnotation,
    StructuredParse,
    StructuredParser,
    StructuredRef,
)

__all__ = [
    "AmendmentAnnotation",
    "StructuredParse",
    "StructuredParser",
    "StructuredRef",
    "UnknownParserError",
    "get_parser",
    "register_parser",
    "registered_parsers",
]


class UnknownParserError(LookupError):
    pass


_REGISTRY: dict[str, Callable[[], StructuredParser]] = {}


def register_parser(name: str, factory: Callable[[], StructuredParser]) -> None:
    _REGISTRY[name] = factory


def get_parser(name: str) -> StructuredParser:
    try:
        factory = _REGISTRY[name]
    except KeyError as exc:
        raise UnknownParserError(
            f"no structured parser registered as {name!r}; known: {sorted(_REGISTRY)}"
        ) from exc
    return factory()


def registered_parsers() -> list[str]:
    return sorted(_REGISTRY)


def _register_builtin() -> None:
    from codify.pipeline.parsers.rada_html import RadaHtmlParser

    register_parser(RadaHtmlParser.name, lambda: RadaHtmlParser())


_register_builtin()
