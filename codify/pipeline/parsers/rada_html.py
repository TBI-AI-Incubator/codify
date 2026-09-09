"""Deterministic converter for the Verkhovna Rada's anchored HTML.

The data.rada.gov.ua document representation is uniform across the corpus:
flat ``<p>`` paragraphs, each preceded by a stable ``<a name="nNNN">``
anchor; cross-references editorially resolved as ``<a href="/go/{ref}">``
links; amendment history as brace-wrapped editorial notes. Structure is
typographic (Розділ / Глава / Стаття), recovered with the standard anchor
scan; bodies are spliced verbatim, no model in the loop.

Standalone use:

    python -m codify.pipeline.parsers.rada_html document.html > out.akn.xml
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lxml import etree

from codify.jurisdictions import JurisdictionConfig
from codify.pipeline.enrich.verbatim import text_to_bluebell_verbatim
from codify.pipeline.parsers.base import (
    AmendmentAnnotation,
    StructuredParse,
    StructuredRef,
)

_GO_HREF = re.compile(r"^/go/|^https?://zakon\.rada\.gov\.ua/(?:laws/show|go)/")
_BRACE_NOTE = re.compile(r"^\{.*\}\s*$", re.S)

# Paragraph-head window exposed to the anchor scan: long enough for any
# heading keyword + number, short enough to hide mid-sentence mentions.
_SCAN_HEAD_CHARS = 60

_QUOTE_CLOSE_RE = re.compile(r'"[;.]?\s*$')


def _closes_quote(plain: str) -> bool:
    return bool(_QUOTE_CLOSE_RE.search(plain))


# Editorial verb → AKN textual-mod action.
_ACTIONS = (
    ("доповнено", "insertion"),
    ("включено", "insertion"),
    ("виключено", "repeal"),
    ("втратила чинність", "repeal"),
    ("в редакції", "substitution"),
    ("замінено", "substitution"),
    ("змінено", "substitution"),
)


@dataclass
class _Para:
    anchor: str | None
    text: str  # with inline {{>}} / {{*}} markup applied
    plain: str  # markup-free text
    links: list[tuple[str, str]]  # (href, label)
    is_note: bool


def _classify_action(text: str) -> str:
    lowered = text.lower()
    for verb, action in _ACTIONS:
        if verb in lowered:
            return action
    return "substitution"


def _para_text(p: etree._Element) -> tuple[str, str, list[tuple[str, str]]]:
    """(marked-up text, plain text, links) for one paragraph element."""
    marked: list[str] = []
    plain: list[str] = []
    links: list[tuple[str, str]] = []

    def walk(node: etree._Element) -> None:
        if node.text:
            marked.append(node.text)
            plain.append(node.text)
        for child in node:
            if child.tag == "a":
                href = child.get("href") or ""
                label = "".join(child.itertext()).strip()
                if _GO_HREF.search(href) and label:
                    if href.startswith("/go/"):
                        target = href
                    else:
                        target = "/go/" + href.rsplit("/", 1)[-1]
                    links.append((target, label))
                    marked.append(f"{{{{>{target} {label}}}}}")
                    plain.append(label)
                elif label:
                    marked.append(label)
                    plain.append(label)
            elif child.tag not in ("script", "style"):
                walk(child)
            if child.tail:
                marked.append(child.tail)
                plain.append(child.tail)

    walk(p)
    join_m = re.sub(r"\s+", " ", "".join(marked)).strip()
    join_p = re.sub(r"\s+", " ", "".join(plain)).strip()
    return join_m, join_p, links


class RadaHtmlParser:
    name: str = "rada_html"
    media_suffixes: tuple[str, ...] = (".html", ".htm")

    def parse(self, content: bytes, *, config: JurisdictionConfig, doctype: str) -> StructuredParse:
        root = etree.fromstring(content, etree.HTMLParser(encoding="utf-8"))
        paras = self._paragraphs(root)
        if not paras:
            raise ValueError("no anchored paragraphs found — not a rada document page")

        text, offsets = self._stream(paras)
        scan_text = self._scan_stream(paras, offsets, len(text))
        bluebell, anchor_summary, eid_to_anchor = text_to_bluebell_verbatim(
            text, country=config.code, doctype=doctype, scan_text=scan_text
        )

        anchor_eid_map = self._map_anchors(paras, offsets, eid_to_anchor)
        refs = self._refs(paras, anchor_eid_map)
        amendments = self._amendments(paras, offsets, eid_to_anchor)
        metadata = self._metadata(paras)

        return StructuredParse(
            bluebell_text=bluebell,
            metadata=metadata,
            anchor_summary=anchor_summary,
            refs=refs,
            amendments=amendments,
            anchor_eid_map=anchor_eid_map,
        )

    # ── decomposition ────────────────────────────────────────────────────

    def _paragraphs(self, root: etree._Element) -> list[_Para]:
        out: list[_Para] = []
        for p in root.iter("p"):
            anchor = None
            first = p.find("a")
            if first is not None and (first.get("name") or "").startswith("n"):
                anchor = first.get("name")
            marked, plain, links = _para_text(p)
            if not plain:
                continue
            is_note = bool(_BRACE_NOTE.match(plain))
            if is_note:
                marked = f"{{{{*{marked}}}}}"
            out.append(_Para(anchor=anchor, text=marked, plain=plain, links=links, is_note=is_note))
        return out

    def _stream(self, paras: list[_Para]) -> tuple[str, list[tuple[int, int]]]:
        """Concatenate paragraphs; return the text and per-para (start, end)."""
        parts: list[str] = []
        offsets: list[tuple[int, int]] = []
        pos = 0
        for para in paras:
            parts.append(para.text)
            offsets.append((pos, pos + len(para.text)))
            pos += len(para.text) + 1  # newline separator
        return "\n".join(parts), offsets

    def _scan_stream(self, paras: list[_Para], offsets: list[tuple[int, int]], length: int) -> str:
        """Offset-aligned copy of the stream exposing only paragraph heads.

        Genuine headings (Розділ / Глава / Стаття …) always begin their own
        paragraph in this format; mid-paragraph declension mentions and
        editorial notes are prose. Blanking everything else stops the scan's
        duplicate filters from ever preferring a prose mention over the
        real heading."""
        buf = [" "] * length
        in_block = False  # ASCII-quoted amendment blocks span paragraphs
        for para, (start, end) in zip(paras, offsets, strict=True):
            plain = para.plain.strip()
            opens = plain.startswith('"') and not _closes_quote(plain)
            if not para.is_note and not in_block and not plain.startswith('"'):
                head = para.text[:_SCAN_HEAD_CHARS]
                buf[start : start + len(head)] = head
            if in_block and _closes_quote(plain):
                in_block = False
            elif opens:
                in_block = True
            # Guillemet pairs must survive blanking so the scan's quote mask
            # still suppresses headings quoted inside «…» spans.
            for i in range(start, min(end, length)):
                ch = para.text[i - start]
                if ch in "«»":
                    buf[i] = ch
        return "".join(buf)

    def _map_anchors(
        self,
        paras: list[_Para],
        offsets: list[tuple[int, int]],
        eid_to_anchor: dict[str, Any],
    ) -> dict[str, str]:
        """Rada paragraph anchor → eId of the structural unit it falls in."""
        units = sorted(
            ((a.char_offset, eid) for eid, a in eid_to_anchor.items()),
            key=lambda t: t[0],
        )
        out: dict[str, str] = {}
        ui = -1
        for para, (start, _end) in zip(paras, offsets, strict=True):
            while ui + 1 < len(units) and units[ui + 1][0] <= start:
                ui += 1
            if para.anchor and ui >= 0:
                out[para.anchor] = units[ui][1]
        return out

    def _refs(self, paras: list[_Para], anchor_eid_map: dict[str, str]) -> list[StructuredRef]:
        out: list[StructuredRef] = []
        for para in paras:
            if para.is_note:
                continue
            for href, label in para.links:
                out.append(
                    StructuredRef(
                        source_anchor=para.anchor or "",
                        source_eid=anchor_eid_map.get(para.anchor or ""),
                        href=href,
                        label=label,
                        context=para.plain[:200],
                    )
                )
        return out

    def _amendments(
        self,
        paras: list[_Para],
        offsets: list[tuple[int, int]],
        eid_to_anchor: dict[str, Any],
    ) -> list[AmendmentAnnotation]:
        units = sorted(
            ((a.char_offset, eid) for eid, a in eid_to_anchor.items()),
            key=lambda t: t[0],
        )
        out: list[AmendmentAnnotation] = []
        ui = -1
        for para, (start, _end) in zip(paras, offsets, strict=True):
            while ui + 1 < len(units) and units[ui + 1][0] <= start:
                ui += 1
            if not para.is_note or not para.links or ui < 0:
                continue
            href, label = para.links[0]
            out.append(
                AmendmentAnnotation(
                    target_eid=units[ui][1],
                    akn_action=_classify_action(para.plain),
                    amender_href=href,
                    amender_label=label,
                    text=para.plain,
                )
            )
        return out

    def _metadata(self, paras: list[_Para]) -> dict[str, Any]:
        """Title = the first substantial non-doctype paragraph of the head."""
        head = [p.plain for p in paras[:8] if not p.is_note]
        title = ""
        for line in head:
            squashed = line.replace(" ", "")
            if any(k in squashed for k in ("ЗАКОНУКРАЇНИ", "ПОСТАНОВА", "УКАЗ")):
                continue
            if not any("\u0400" <= ch <= "\u04ff" for ch in line):
                continue  # image placeholders, latin boilerplate
            if len(line) > 8:
                title = line
                break
        return {"title": title, "language": "ukr"}


def _main() -> None:
    import argparse
    import sys

    from codify.jurisdictions import load_config
    from codify.pipeline.enrich.bluebell import parse_to_akn

    ap = argparse.ArgumentParser(description="Rada HTML → Akoma Ntoso 3.0")
    ap.add_argument("html_file")
    ap.add_argument("--doctype", default="act")
    ap.add_argument("--date", default="", help="work date YYYY-MM-DD")
    ap.add_argument("--number", default="", help="citation number, e.g. 1700-VII")
    args = ap.parse_args()

    config = load_config("ua")
    with open(args.html_file, "rb") as fh:
        parsed = RadaHtmlParser().parse(fh.read(), config=config, doctype=args.doctype)
    akn = parse_to_akn(
        parsed.bluebell_text,
        country="ua",
        doctype=args.doctype,
        date=args.date,
        number=args.number,
        language="ukr",
    )
    sys.stdout.write(akn)


if __name__ == "__main__":
    _main()
