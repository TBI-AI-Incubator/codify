"""Strict AKN 3.0 schema check for the paths that write `versions.akn_xml`.

Two entry points, because not every writer's input is clean. `gate_akn` refuses
the write; `report_akn` logs the error and lets it through. Emitters that build
a document themselves enforce. Pipeline paths report, because they store AKN
carrying defects they did not create and failing them would stop ingest on a
fault it cannot fix.

`pipeline.enrich.validator.validate_akn` is a different function: advisory
findings, no `strict`, refuses nothing.
"""

from __future__ import annotations

import structlog
from lxml import etree

from codify.akn._schema import validate_akn

logger = structlog.get_logger()


class AknSchemaError(ValueError):
    """Stored AKN would not validate. Raised at the write, not discovered later."""


def _first_error(akn_xml: str) -> str | None:
    if not akn_xml:
        return None
    try:
        validate_akn(akn_xml, strict=True)
    except etree.DocumentInvalid as exc:
        return str(exc)
    return None


def gate_akn(akn_xml: str, *, where: str) -> None:
    """Raise `AknSchemaError` unless `akn_xml` is valid. `where` names the
    write path, so the error says which one produced it."""
    error = _first_error(akn_xml)
    if error is None:
        return
    logger.warning("akn_schema_gate_failed", where=where, error=error[:500])
    raise AknSchemaError(f"{where}: stored AKN fails the schema: {error}")


def report_akn(akn_xml: str, *, where: str, **context: object) -> str | None:
    """Log and return the first schema error, or None. Does not refuse the write."""
    error = _first_error(akn_xml)
    if error is not None:
        logger.warning("akn_schema_invalid", where=where, error=error[:500], **context)
    return error


__all__ = ["AknSchemaError", "gate_akn", "report_akn"]
