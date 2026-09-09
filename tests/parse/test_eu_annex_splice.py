"""A transcribed page image replaces its marker in place, eId kept."""

from __future__ import annotations

from lxml import etree

from codify.akn._schema import AKN_NS
from codify.pipeline.formats.eu_annex_splice import (
    find_image_markers,
    splice_transcriptions,
    transcript_to_content,
)

NS = {"a": AKN_NS}


def _marker(n: int, kind: str, fileref: str) -> str:
    return (
        f'<hcontainer name="includedSource" eId="att_1__includedsource_{n}">'
        f"<content><p>[{kind} not transcribed: {fileref}]</p></content></hcontainer>"
    )


_AKN = (
    f'<akomaNtoso xmlns="{AKN_NS}"><act name="act"><body>'
    '<attachments><attachment eId="att_1"><doc name="annex"><mainBody>'
    '<hcontainer name="paragraph" eId="att_1__paragraph_1"><content><p>Form:</p></content>'
    "</hcontainer>"
    + _marker(1, "TIFF", "L_1999123EN.01000201.tif")
    + _marker(2, "TIFF", "L_1999123EN.01000301.tif")
    + _marker(3, "DOC", "other.xml")
    + "</mainBody></doc></attachment></attachments></body></act></akomaNtoso>"
)

_TRANSCRIPT = """# Section A

Name of applicant: [box] natural person [box] legal person

| Code | Product | Unit |
|------|---------|------|
| 0101 | Horses  | head |
| 0102 | Cattle  | head |

Signed at Brussels."""


def test_markers_are_found_in_order_with_their_file_and_kind() -> None:
    markers = find_image_markers(_AKN)
    assert [(m.eid, m.fileref, m.kind) for m in markers] == [
        ("att_1__includedsource_1", "L_1999123EN.01000201.tif", "TIFF"),
        ("att_1__includedsource_2", "L_1999123EN.01000301.tif", "TIFF"),
        ("att_1__includedsource_3", "other.xml", "DOC"),
    ]


def test_a_transcript_becomes_paragraphs_and_a_table_with_eids() -> None:
    content = transcript_to_content(_TRANSCRIPT, "att_1__includedsource_1")
    tags = [etree.QName(c).localname for c in content]
    assert tags == ["p", "p", "table", "p"]
    assert content[0].text == "Section A"
    assert content[1].text == "Name of applicant: [box] natural person [box] legal person"
    table = content[2]
    assert table.get("eId") == "att_1__includedsource_1__tbl_1"
    rows = table.findall("a:tr", NS)
    assert [r.get("eId") for r in rows] == [
        f"att_1__includedsource_1__tbl_1__tr_{i}" for i in (1, 2, 3)
    ]
    assert [c.tag.split("}")[1] for c in rows[0]] == ["th", "th", "th"]
    assert [c.find("a:p", NS).text for c in rows[1]] == ["0101", "Horses", "head"]


def test_splice_replaces_content_keeps_the_eid_and_counts_the_rest() -> None:
    xml, counts = splice_transcriptions(
        _AKN,
        {
            "att_1__includedsource_1": _TRANSCRIPT,
            "att_1__includedsource_2": "   ",
            "att_1__nothing": "stray",
        },
    )
    assert counts == {"spliced": 1, "blank": 1, "unmatched": 1}
    root = etree.fromstring(xml.encode())
    done = root.find(".//a:hcontainer[@eId='att_1__includedsource_1']", NS)
    assert done is not None and done.get("name") == "transcribedSource"
    assert done.find("a:content/a:table", NS) is not None
    assert "not transcribed" not in "".join(done.itertext())
    kept = root.find(".//a:hcontainer[@eId='att_1__includedsource_2']", NS)
    assert kept is not None and kept.get("name") == "includedSource"
    assert "[TIFF not transcribed: L_1999123EN.01000301.tif]" in "".join(kept.itertext())
    # Nothing else moved: the prose block before the images is untouched.
    assert root.find(".//a:hcontainer[@eId='att_1__paragraph_1']/a:content/a:p", NS).text == "Form:"


def test_splicing_nothing_changes_nothing_but_the_serialisation() -> None:
    xml, counts = splice_transcriptions(_AKN, {})
    assert counts == {"spliced": 0, "blank": 0, "unmatched": 0}
    assert len(find_image_markers(xml)) == 3
