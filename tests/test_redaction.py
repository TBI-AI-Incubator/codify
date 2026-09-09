"""Unit tests for operational-log PII redaction: processor + stdlib LogRecord."""

from __future__ import annotations

import logging
from typing import Any

from codify.core.redaction import redact_log_record, redact_processor


def test_redaction_masks_pii() -> None:
    out = redact_processor(
        None,
        "info",
        {"event": "login by alice@example.com", "token": "sekret", "msg": "bearer abc.def.ghi"},
    )
    assert "<redacted:email>" in out["event"]
    assert out["token"] == "<redacted>"
    assert "bearer <redacted>" in out["msg"]


def _logrec(msg: Any, *, name: str = "app", args: Any = None, exc_info: Any = None, **extra: Any):
    rec = logging.LogRecord(name, logging.INFO, __file__, 10, msg, args, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def test_redact_log_record_scrubs_foreign_message() -> None:
    rec = _logrec("contact alice@example.com bearer abc.def.ghi")
    safe = redact_log_record(rec)
    body = safe.getMessage()
    assert "alice@example.com" not in body
    assert "<redacted:email>" in body and "bearer <redacted>" in body
    assert "alice@example.com" in rec.getMessage()  # original left intact for stdout


def test_redact_log_record_scrubs_args_and_extra_attributes() -> None:
    rec = _logrec("user %s", args=("bob@example.com",), client="ip 10.0.0.1")
    safe = redact_log_record(rec)
    assert "bob@example.com" not in safe.getMessage()
    # foreign extra= fields land in __dict__ and OTel would export them verbatim
    assert safe.__dict__["client"] == "ip <redacted:ip>"


def test_redact_log_record_masks_sensitive_extra_by_key() -> None:
    # an opaque secret under a sensitive key name has no regex signature; the
    # key-based mask (as in redact_processor) is what catches it
    rec = _logrec("auth", token="sekret-opaque-value", authorization="Basic zzz")
    safe = redact_log_record(rec)
    assert safe.__dict__["token"] == "<redacted>"
    assert safe.__dict__["authorization"] == "<redacted>"


def test_redact_log_record_folds_and_scrubs_traceback() -> None:
    import sys

    try:
        raise ValueError("leaked eyJabcdef.ghijkl.mnopqr and alice@example.com")
    except ValueError:
        rec = _logrec("boom", exc_info=sys.exc_info())
    safe = redact_log_record(rec)
    body = safe.getMessage()
    assert "alice@example.com" not in body and "<redacted:jwt>" in body
    assert "Traceback" in body  # the scrubbed trace is folded into the body
    assert safe.exc_info is None  # cleared so OTel cannot export an unredacted trace


def test_redact_log_record_drops_structlog_meta() -> None:
    rec = _logrec("hi", _logger=object(), _from_structlog=True)
    safe = redact_log_record(rec)
    assert "_logger" not in safe.__dict__ and "_from_structlog" not in safe.__dict__
    assert "_logger" in rec.__dict__  # original intact


def test_redact_log_record_drops_exporter_own_logs() -> None:
    # the OTLP exporter's failure logs must not re-enter the export queue
    rec = _logrec("Failed to export logs batch", name="opentelemetry.exporter.otlp")
    assert redact_log_record(rec) is None
