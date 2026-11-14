"""Plain-text Rada input: one paragraph per line, later units as attachments."""

from __future__ import annotations

import pytest
from lxml import etree

from codify.jurisdictions import load_config
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.parsers import get_parser, registered_parsers
from codify.pipeline.parsers.rada_html import UNIT_BREAK

_NS = "{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}"
_LAW = """ЗАКОН УКРАЇНИ
Про вигадані відносини
Верховна Рада України постановляє:
Розділ I
ЗАГАЛЬНІ ПОЛОЖЕННЯ
Стаття 1. Сфера дії
1. Цей Закон регулює вигадані відносини.
2. Дія цього Закону не поширюється на справжні відносини.
Стаття 2. Терміни
У цьому Законі терміни вживаються в такому значенні.
Розділ II
ПРИКІНЦЕВІ ПОЛОЖЕННЯ
Стаття 3. Набрання чинності
Цей Закон набирає чинності з дня його опублікування.
Президент України
"""
_ANNEX = """ПОРЯДОК
Зареєстровано в Міністерстві юстиції України
1 березня 2016 р. за N 365/28495
1. Цей Порядок визначає вигадану процедуру.
"""


def _parse(text: str) -> object:
    config = load_config("ua")
    assert config is not None
    return get_parser("rada_text").parse(text.encode("utf-8"), config=config, doctype="act")


def _akn(text: str) -> etree._Element:
    parsed = _parse(text)
    akn = parse_to_akn(
        parsed.bluebell_text,  # type: ignore[attr-defined]
        country="ua",
        doctype="act",
        date="2016-02-11",
        number="1038-VIII",
        language="ukr",
    )
    return etree.fromstring(akn.encode("utf-8"))


def test_registry_lists_rada_text() -> None:
    assert "rada_text" in registered_parsers()


def test_lines_structure_as_the_html_paragraphs_do() -> None:
    root = _akn(_LAW)
    nums = [a.findtext(f"{_NS}num") for a in root.iter(f"{_NS}article")]
    assert nums == ["1", "2", "3"]
    assert len(root.findall(f".//{_NS}section")) == 2
    assert "справжні відносини" in "".join(root.itertext())


def test_a_later_unit_lands_as_an_attachment() -> None:
    root = _akn(f"{_LAW}{UNIT_BREAK}{_ANNEX}")
    [attachment] = root.iter(f"{_NS}attachment")
    text = "".join(attachment.itertext())
    assert "вигадану процедуру" in text and "365/28495" in text
    assert len(root.findall(f".//{_NS}article")) == 3


def test_an_empty_text_is_refused() -> None:
    with pytest.raises(ValueError):
        _parse("\n \n")
