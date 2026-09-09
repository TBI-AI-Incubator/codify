"""HTML parsers for zakon.rada.gov.ua document cards."""

from __future__ import annotations

import re
from typing import cast
from urllib.parse import urljoin

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Document body container on zakon.rada, the law text PDF lives inside one of
# these wrappers, distinguishing it from site-chrome PDFs (mobile-app promo,
# privacy notices, etc.) in the footer.
_BODY_CONTAINER_RES = (
    re.compile(
        r'<div[^>]+id="article"[^>]*>(.*?)(?=<footer|<header|</body)', re.DOTALL | re.IGNORECASE
    ),
    re.compile(
        r'<div[^>]+class="[^"]*\bact-content\b[^"]*"[^>]*>(.*?)(?=<footer|</body)',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'<div[^>]+class="[^"]*\bdoc-meta\b[^"]*"[^>]*>(.*?)(?=<footer|</body)',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE),
)
_PDF_RE = re.compile(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>', re.IGNORECASE)


def extract_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if m is None:
        return None
    title = m.group(1).strip()
    # zakon.rada appends " | Офіційний веб-портал ...", drop after |.
    if "|" in title:
        title = title.split("|", 1)[0].strip()
    return title or None


def extract_pdf_url(html: str, *, base_url: str) -> str | None:
    """Return the first body PDF, with a single-link whole-page fallback."""
    body = _extract_body(html)
    if body is not None:
        m = _PDF_RE.search(body)
        if m is None:
            return None
        return cast(str, urljoin(base_url, m.group(1)))

    # No container matched, only return a PDF if there's exactly one on the
    # whole page (any more and we can't tell content from chrome).
    pdfs = _PDF_RE.findall(html)
    if len(pdfs) == 1:
        return cast(str, urljoin(base_url, pdfs[0]))
    return None


def _extract_body(html: str) -> str | None:
    for container_re in _BODY_CONTAINER_RES:
        m = container_re.search(html)
        if m is not None:
            return m.group(1)
    return None


__all__ = ["extract_pdf_url", "extract_title"]
