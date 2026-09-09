"""Extract PDF info-dictionary metadata for source provenance."""

from __future__ import annotations

import io
import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, TypedDict

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PdfMetadata(TypedDict, total=False):
    pdf_title: str | None
    pdf_author: str | None
    pdf_producer: str | None
    pdf_creation_date: datetime | None
    page_count: int | None


# PDF 1.7 §7.9.4, D:YYYYMMDDHHmmSSOHH'mm'
# All trailing fields are optional. Most legal-corpus PDFs omit timezone.
_PDF_DATE_RE = re.compile(
    r"^D:"
    r"(?P<year>\d{4})"
    r"(?P<month>\d{2})?"
    r"(?P<day>\d{2})?"
    r"(?P<hour>\d{2})?"
    r"(?P<minute>\d{2})?"
    r"(?P<second>\d{2})?"
    r"(?P<tz>[+\-Z]\d{0,2}'?\d{0,2}'?)?"
)


def _parse_pdf_date(raw: Any) -> datetime | None:
    """Parse a PDF info-dict date value to a Python `datetime`. Returns None
    for malformed inputs, the producer string is auditable in `pdf_producer`."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # Strip surrounding parens that some producers add.
    text = text.strip("()")
    m = _PDF_DATE_RE.match(text)
    if not m:
        return None
    year = int(m.group("year"))
    month = int(m.group("month") or 1)
    day = int(m.group("day") or 1)
    hour = int(m.group("hour") or 0)
    minute = int(m.group("minute") or 0)
    second = int(m.group("second") or 0)
    tz_raw = m.group("tz") or ""
    try:
        if tz_raw in ("", "Z"):
            tz = UTC
        else:
            sign = 1 if tz_raw[0] == "+" else -1
            digits = re.findall(r"\d+", tz_raw)
            tz_hours = int(digits[0]) if digits else 0
            tz_minutes = int(digits[1]) if len(digits) > 1 else 0
            tz = timezone(sign * timedelta(hours=tz_hours, minutes=tz_minutes))
        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except (ValueError, TypeError):
        # Out-of-range fields (month=13, day=32, etc.), leave NULL.
        return None


def _clean_string(raw: Any) -> str | None:
    """Normalise PDF text fields by removing NULs and other C0 controls."""
    if raw is None:
        return None
    text = str(raw)
    text = "".join(c for c in text if c == "\t" or c == "\n" or ord(c) >= 0x20)
    text = text.strip()
    return text or None


def extract_pdf_metadata(pdf_bytes: bytes) -> PdfMetadata:
    """Extract the PDF info dict + page count from raw PDF bytes.

    Best-effort: a corrupted PDF returns an empty dict rather than raising,
    so callers can still record `source_documents` rows for files we want
    to retain even when their info dict is unreadable.
    """
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # noqa: BLE001, malformed PDF should not block ingest
        logger.warning("pdf_metadata_read_failed", exc_info=exc)
        return {}
    md: dict[str, Any] = dict(reader.metadata or {})
    out: PdfMetadata = {
        "pdf_title": _clean_string(md.get("/Title")),
        "pdf_author": _clean_string(md.get("/Author")),
        "pdf_producer": _clean_string(md.get("/Producer")),
        "pdf_creation_date": _parse_pdf_date(md.get("/CreationDate")),
    }
    try:
        out["page_count"] = len(reader.pages)
    except Exception:  # noqa: BLE001
        out["page_count"] = None
    return out


__all__ = ["PdfMetadata", "extract_pdf_metadata"]
