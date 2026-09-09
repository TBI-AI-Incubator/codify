"""Splice transcribed annex page images into the AKN that marked them untranscribed.

The FORMEX converter leaves one `includedSource` block per page image, its text
naming the file. A transcription replaces that block's content in place: the
block keeps its eId, so every citation and stored provision row still resolves.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from lxml import etree

from codify.akn._schema import AKN_NS, safe_parser
from codify.akn.vocabulary import table_row_eid

MARKER_NAME = "includedSource"
TRANSCRIBED_NAME = "transcribedSource"
_MARKER_TEXT = re.compile(r"^\[(?P<kind>\w+) not transcribed: (?P<fileref>.+)\]$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_HEADING = re.compile(r"^#{1,6}\s+")


@dataclass(frozen=True)
class ImageMarker:
    eid: str
    fileref: str
    kind: str


def _akn(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, f"{{{AKN_NS}}}{tag}")
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _marker_of(hc: etree._Element) -> ImageMarker | None:
    text = " ".join("".join(hc.itertext()).split())
    m = _MARKER_TEXT.match(text)
    eid = hc.get("eId")
    if m is None or not eid:
        return None
    return ImageMarker(eid=eid, fileref=m.group("fileref"), kind=m.group("kind"))


def find_image_markers(akn_xml: str) -> list[ImageMarker]:
    """Every untranscribed-image block, in document order."""
    root = etree.fromstring(akn_xml.encode("utf-8"), parser=safe_parser())
    found = []
    for hc in root.iterfind(f".//{{{AKN_NS}}}hcontainer[@name='{MARKER_NAME}']"):
        marker = _marker_of(hc)
        if marker is not None:
            found.append(marker)
    return found


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _emit_table(content: etree._Element, eid: str, lines: list[str]) -> None:
    rows = [ln for ln in lines if not _TABLE_RULE.match(ln)]
    # The rule row marks the header; a table without one has none.
    header = any(_TABLE_RULE.match(ln) for ln in lines[:2])
    table = _akn(content, "table", eId=eid)
    for index, line in enumerate(rows, start=1):
        tr = _akn(table, "tr", eId=table_row_eid(eid, index))
        for cell in _cells(line):
            _akn(_akn(tr, "th" if header and index == 1 else "td"), "p").text = cell


def transcript_to_content(text: str, eid: str) -> etree._Element:
    """A transcript's blank-line-separated chunks as `<content>` blocks: pipe
    tables as `table`, everything else as one `p` per line."""
    content = etree.Element(f"{{{AKN_NS}}}content")
    tables = 0
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.rstrip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        if all(ln.lstrip().startswith("|") for ln in lines) and len(lines) >= 2:
            tables += 1
            _emit_table(content, f"{eid}__tbl_{tables}", lines)
            continue
        for line in lines:
            _akn(content, "p").text = _HEADING.sub("", line.strip())
    return content


def splice_transcriptions(
    akn_xml: str, transcripts: Mapping[str, str]
) -> tuple[str, dict[str, int]]:
    """The document with each marked block's content replaced by its transcript,
    keyed by the block's eId. A blank transcript leaves its marker; a key naming
    no marker is counted and ignored."""
    root = etree.fromstring(akn_xml.encode("utf-8"), parser=safe_parser())
    spliced = blank = 0
    seen: set[str] = set()
    for hc in root.iterfind(f".//{{{AKN_NS}}}hcontainer[@name='{MARKER_NAME}']"):
        marker = _marker_of(hc)
        if marker is None or marker.eid not in transcripts:
            continue
        seen.add(marker.eid)
        text = transcripts[marker.eid]
        if not text.strip():
            blank += 1
            continue
        for child in list(hc):
            hc.remove(child)
        hc.set("name", TRANSCRIBED_NAME)
        hc.append(transcript_to_content(text, marker.eid))
        spliced += 1
    counts = {"spliced": spliced, "blank": blank, "unmatched": len(set(transcripts) - seen)}
    serialised: bytes = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    return serialised.decode("utf-8"), counts


__all__ = [
    "ImageMarker",
    "MARKER_NAME",
    "TRANSCRIBED_NAME",
    "find_image_markers",
    "splice_transcriptions",
    "transcript_to_content",
]
