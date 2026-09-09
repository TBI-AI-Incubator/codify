"""PII redaction for operational logs, masks emails/JWTs/tokens/IPs; fail-safe."""

from __future__ import annotations

import copy
import logging
import re
import traceback
from collections.abc import MutableMapping
from typing import Any

_REDACTED_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "authorization",
        "secret",
        "client_secret",
        "cookie",
        "prompt",
        "query",
        "search_query",
        "content",
        "body",
        "request_body",
        "response_body",
    }
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"), "<redacted:email>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"),
        "<redacted:jwt>",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "bearer <redacted>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<redacted:ip>"),
    (re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b"), "<redacted:ip>"),
)


def _scrub(s: str) -> str:
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def _scrub_value(v: Any) -> Any:
    if isinstance(v, str):
        return _scrub(v)
    if isinstance(v, dict):
        return {
            k: ("<redacted>" if k in _REDACTED_KEYS else _scrub_value(val)) for k, val in v.items()
        }
    if isinstance(v, (list, tuple)):
        return [_scrub_value(x) for x in v]
    return v


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: mask sensitive keys + recursively regex-scrub values."""
    for k in list(event_dict.keys()):
        try:
            event_dict[k] = "<redacted>" if k in _REDACTED_KEYS else _scrub_value(event_dict[k])
        except Exception:  # fail safe: drop the offending value rather than leak it
            event_dict[k] = "<redaction-error>"
    return event_dict


# structlog stashes its bound logger + processor meta on each LogRecord (via the
# stdlib `extra=` bridge) for the stdout formatter. On an export path they are
# noise, and `_logger` is a non-primitive the OTLP handler rejects; drop them.
_STRUCTLOG_META = ("_logger", "_name", "_from_structlog", "_record")


def redact_log_record(record: logging.LogRecord) -> logging.LogRecord | None:
    """A copy of ``record`` made safe for an external log store: structlog internals
    dropped, the message and any ``extra=`` attributes regex-scrubbed, and an
    exception rendered, scrubbed, then folded into the body so nothing downstream
    re-derives an unredacted stacktrace. Returns ``None`` to drop the record, the
    OTLP exporter's own failure logs, which would otherwise feed back into its
    queue and self-sustain.

    structlog-emitted records are already redacted by the processor chain; this is
    the safety net for foreign stdlib records (library warnings, tracebacks) that
    reach a handler without passing through it. Copies, so the shared record other
    handlers format is untouched."""
    if record.name.startswith("opentelemetry"):
        return None
    clone = copy.copy(record)
    # Body: scrub the rendered message. A structlog dict msg is already redacted;
    # re-scrub defensively. Fold in a scrubbed traceback, then clear exc/args so
    # the OTLP handler cannot export an unredacted stacktrace or re-format args.
    if isinstance(record.msg, str) or record.args:
        body: Any = _scrub(record.getMessage())
    else:
        body = _scrub_value(record.msg)
    if record.exc_info:
        body = f"{body}\n{_scrub(''.join(traceback.format_exception(*record.exc_info)))}"
    clone.msg = body
    clone.args = None
    clone.exc_info = None
    clone.exc_text = None
    # Attributes: drop structlog internals; mask by sensitive key then regex-scrub
    # the rest, same two-pronged rule as redact_processor. Foreign `extra=` fields
    # land as top-level record attributes, and an opaque secret under a sensitive
    # key name (extra={"token": ...}) has no regex signature to catch it.
    for key in list(clone.__dict__):
        if key in _STRUCTLOG_META:
            del clone.__dict__[key]
        elif key in _REDACTED_KEYS:
            clone.__dict__[key] = "<redacted>"
        else:
            clone.__dict__[key] = _scrub_value(clone.__dict__[key])
    return clone
