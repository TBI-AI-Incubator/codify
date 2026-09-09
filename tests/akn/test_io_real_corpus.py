from __future__ import annotations

import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from lxml import etree

from codify.akn import Document, parse_akn, to_akn
from codify.akn._schema import AKN_NS, parse_xml

from ._helpers import structurally_equal

# Foreign-emitter AKN. legislation.gov.uk's vocabulary (restriction, eventRef,
# ins, commentary, level) is markup our own emitter never produces, so a parser
# regression on imported documents surfaces here and nowhere else in the suite.
# This repository commits the two samples. Fetching is opt-in, behind
# `CODIFY_FETCH_AKN_SAMPLES`: automatic, it put a network call inside the CI step
# that deletes the corpus to prove the suite runs without one.
_SOURCES = {
    "uk_ukpga_1981_64.akn.xml": "https://www.legislation.gov.uk/ukpga/1981/64/data.akn",
    "uk_uksi_1990_1304.akn.xml": "https://www.legislation.gov.uk/uksi/1990/1304/data.akn",
}

_COMMITTED = Path(__file__).resolve().parents[4] / "data" / "samples" / "akn"
_CACHE = Path(__file__).parent / ".akn-samples"

# Whitespace-only text contributes nothing useful, collapse runs to a single space
# before counting, so emitter formatting differences don't move the ratio.
_WS = re.compile(r"\s+")


_FETCH_ENV = "CODIFY_FETCH_AKN_SAMPLES"


def _sample(name: str) -> Path:
    """Committed copy, else the cache, else a skip. Fetch only when asked."""
    for candidate in (_COMMITTED / name, _CACHE / name):
        if candidate.is_file():
            return candidate
    if not os.environ.get(_FETCH_ENV):
        pytest.skip(f"{name} is absent; set {_FETCH_ENV}=1 to fetch it")
    _CACHE.mkdir(parents=True, exist_ok=True)
    try:
        # noqa reason: the URL is a module constant, not caller input.
        with urllib.request.urlopen(_SOURCES[name], timeout=30) as response:  # noqa: S310
            body = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        pytest.skip(f"{name} is absent and could not be fetched: {exc}")
    # An error page is not a transport error, so it misses the except above and
    # would cache. Checked by parsing and by the root, not by a leading `<`.
    try:
        root = parse_xml(body)
    except etree.XMLSyntaxError as exc:
        pytest.skip(f"{name} fetched as {len(body)} bytes that do not parse: {exc}")
    if root.tag != f"{{{AKN_NS}}}akomaNtoso":
        pytest.skip(f"{name} fetched as {root.tag!r} rather than an AKN document")
    # Published by atomic rename: under `-n auto` both parametrised tests can
    # fetch at once, and a reader must never see a half-written file.
    with tempfile.NamedTemporaryFile(dir=_CACHE, delete=False) as staged:
        staged.write(body)
    os.replace(staged.name, _CACHE / name)
    return _CACHE / name


def _normalise(s: str) -> str:
    return _WS.sub(" ", s).strip()


def _source_body_text(xml: bytes) -> str:
    root = parse_xml(xml)
    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return ""
    return _normalise("".join(body.itertext()))


def _document_body_text(doc: Document) -> str:
    parts: list[str] = []

    def visit(elements: list) -> None:
        for el in elements:
            if el.heading:
                parts.append(el.heading)
            if el.number:
                parts.append(el.number)
            if el.intro:
                parts.append(el.intro)
            if el.text:
                parts.append(el.text)
            visit(el.children)
            if el.wrap_up:
                parts.append(el.wrap_up)

    visit(doc.body)
    return _normalise(" ".join(parts))


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_real_akn_round_trips(name: str) -> None:
    src = _sample(name).read_bytes()
    doc = parse_akn(src)
    rebuilt = parse_akn(to_akn(doc))
    assert structurally_equal(doc, rebuilt)


@pytest.mark.integration
@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_parse_captures_body_text(name: str) -> None:
    """Catch silent text-loss bugs by comparing body-text length source vs parsed.

    Mass-balance check: counts characters, not content. Relies on the parser and
    `itertext()` measuring the same underlying string set; if a future parser
    change ever fabricates text, this would silently false-pass.
    """
    sample_path = _sample(name)
    src = sample_path.read_bytes()
    doc = parse_akn(src)
    src_chars = len(_source_body_text(src))
    parsed_chars = len(_document_body_text(doc))
    if src_chars == 0:
        pytest.skip(f"{sample_path.name} has no body text")
    coverage = parsed_chars / src_chars
    assert coverage >= 0.90, (
        f"{sample_path.name}: body-text coverage too low "
        f"({parsed_chars}/{src_chars} = {coverage:.1%}); a structural element is being dropped"
    )
