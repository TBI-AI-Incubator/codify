"""Riigi Teataja annexes published as files: the bytes the XML embeds beside each
link, read under the converter's attachment eIds, and their transcripts written in."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lxml import etree

from codify.akn._schema import AKN_NS, parse_xml
from codify.pipeline.enrich.ocr import _strip_control_chars
from codify.pipeline.formats.eu_annex_splice import TRANSCRIBED_NAME, transcript_to_content

ANNEX_URL = "https://www.riigiteataja.ee/aktilisa/{}/{}"


@dataclass(frozen=True)
class AnnexFile:
    """One annex: the attachment it fills, its file name, and its bytes when the
    source carries them."""

    eid: str
    name: str | None
    data: bytes | None


def annex_elements(root: etree._Element) -> list[etree._Element]:
    """Every annex in the order the transformer numbers its attachments."""
    found: list[etree._Element] = root.findall(".//{*}lisaViide") + root.findall(".//{*}lisa")
    return found


def annex_act_id(root: etree._Element) -> str | None:
    """The text's own `globaalID`, which files its annexes; the first `aktViide` names
    the original publication, which lacks a consolidated text's later annexes."""
    own = (root.findtext("{*}metaandmed/{*}globaalID") or "").strip()
    return own or None


def annex_url(act_id: str, file_name: str) -> str:
    return ANNEX_URL.format(act_id, file_name)


def _decoded(fail: etree._Element) -> bytes | None:
    """The file's bytes; None for an empty or damaged payload."""
    payload = "".join("".join(fail.itertext()).split())
    try:
        return base64.b64decode(payload, validate=True) or None
    except ValueError:  # binascii.Error, and non-ASCII text
        return None


def annex_files(source_xml: str | bytes) -> list[AnnexFile]:
    """Each attachment the transformer writes, with the file its annex embeds.

    Every annex is listed, so the eIds match the transformer's; `name` is None for
    an annex that links no file, and `data` None where the source holds no bytes."""
    root = parse_xml(source_xml, huge_tree=True)
    out: list[AnnexFile] = []
    for index, lisa in enumerate(annex_elements(root), start=1):
        fail = lisa.find(".//{*}fail")
        out.append(
            AnnexFile(
                eid=f"att_{index}",
                name=fail.get("failNimi") if fail is not None else None,
                data=_decoded(fail) if fail is not None else None,
            )
        )
    return out


def fill_annex_text(akn_xml: str, pages: Mapping[str, Sequence[str]]) -> tuple[str, int]:
    """Each named attachment's pages after its link, one `hcontainer` per non-blank page;
    one already transcribed is left alone. Returns the document and the count filled."""
    root = parse_xml(akn_xml, huge_tree=True)
    filled = 0
    for main in root.iterfind(f".//{{{AKN_NS}}}attachment/{{{AKN_NS}}}doc/{{{AKN_NS}}}mainBody"):
        eid = main.getparent().getparent().get("eId") or ""
        if eid not in pages:
            continue
        if main.find(f"{{{AKN_NS}}}hcontainer[@name='{TRANSCRIBED_NAME}']") is not None:
            continue
        added = False
        for number, text in enumerate(pages[eid], start=1):
            text = _strip_control_chars(text)
            if not text.strip():
                continue
            page_eid = f"{eid}__hcontainer_{number}"
            hc = etree.SubElement(
                main, f"{{{AKN_NS}}}hcontainer", eId=page_eid, name=TRANSCRIBED_NAME
            )
            hc.append(transcript_to_content(text, page_eid))
            added = True
        filled += added
    out: bytes = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    return out.decode("utf-8"), filled


__all__ = [
    "ANNEX_URL",
    "AnnexFile",
    "annex_act_id",
    "annex_elements",
    "annex_files",
    "annex_url",
    "fill_annex_text",
]
