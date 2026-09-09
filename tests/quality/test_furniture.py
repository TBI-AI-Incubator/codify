"""furniture_in_body counts footnote/aside text still in `<body>`, and does not
count a note already lifted into `<authorialNote>`."""

from __future__ import annotations

from codify.pipeline.enrich.regions import Region
from codify.quality.furniture import furniture_in_body

_FN = "(1) This article was amended by Decree-Law 5 of 2019."


def _regions(kind: str, text: str) -> dict[int, list[Region]]:
    block_type = "aside_text" if kind == "aside" else "references"
    return {
        1: [
            Region(
                kind=kind,
                page_number=1,
                block_index=0,
                block_type=block_type,
                text=text,
                top=0,
                bottom=1,
            )
        ]
    }


def _act(body: str) -> str:
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        f'<act name="act"><meta/><body>{body}</body></act></akomaNtoso>'
    )


def test_a_footnote_left_in_body_is_counted() -> None:
    akn = _act(f'<article eId="art_1"><content><p>{_FN}</p></content></article>')
    assert furniture_in_body(akn, _regions("footnote", _FN))["references"] == 1


def test_a_lifted_footnote_is_not_counted() -> None:
    # Inside <authorialNote> it is out of the provision flow, the good end state.
    akn = _act(
        '<article eId="art_1"><content><p>real provision text'
        f'<authorialNote placement="bottom"><p>{_FN}</p></authorialNote></p>'
        "</content></article>"
    )
    assert furniture_in_body(akn, _regions("footnote", _FN))["references"] == 0


def test_an_inline_note_is_not_stripped_so_its_furniture_still_counts() -> None:
    # An authorialNote without placement="bottom" is inline provision text, so a
    # leaked footnote phrase inside it must still count.
    akn = _act(
        '<article eId="art_1"><content><p>real provision text'
        f"<authorialNote><p>{_FN}</p></authorialNote></p></content></article>"
    )
    assert furniture_in_body(akn, _regions("footnote", _FN))["references"] == 1


def test_a_marginal_label_left_in_body_is_counted() -> None:
    akn = _act('<article eId="art_1"><content><p>تعاريف</p></content></article>')
    assert furniture_in_body(akn, _regions("aside", "تعاريف"))["aside_text"] == 1


def test_a_clean_body_is_zero() -> None:
    akn = _act('<article eId="art_1"><content><p>an ordinary provision</p></content></article>')
    assert furniture_in_body(akn, _regions("footnote", _FN)) == {"references": 0, "aside_text": 0}
