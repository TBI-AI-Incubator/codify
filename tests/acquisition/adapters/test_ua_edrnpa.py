"""The Ministry of Justice register export: streaming, card classes, Rada ref rules."""

from __future__ import annotations

import io
from datetime import date

import pytest

from codify.acquisition.adapters.ua.edrnpa import (
    UNIT_BREAK,
    Card,
    RefMatcher,
    attribution,
    card_class,
    card_record,
    compose_units,
    iter_cards,
    iter_texts,
    rada_convocation,
    ref_candidates,
    registration_of,
    snapshot_date,
)

_LAW = "Верховна Рада України"
_CABINET = "Кабінет Міністрів України"


def _item(name: str, *values: str) -> str:
    texts = "".join(f"<text>\n{v}\n</text>\n" for v in values)
    body = f"<textlist>\n{texts}</textlist>" if len(values) > 1 else texts
    return f'<item name="{name}">\n{body}</item>\n'


def _export(*documents: str) -> io.BytesIO:
    body = "".join(f"<document>\n{d}</document>\n" for d in documents)
    xml = f"<?xml version='1.0' encoding='utf-8'?>\n<rna>\n<database>\n{body}</database>\n</rna>"
    return io.BytesIO(xml.encode("utf-8") + b"\r\n\x1a")


def _card(**fields: str | list[str]) -> Card:
    return {k: [v] if isinstance(v, str) else v for k, v in fields.items()}


def test_cards_stream_past_the_end_of_file_byte_and_keep_lists() -> None:
    raw = _export(
        _item("publisher", _LAW)
        + _item("type", "Закон України")
        + _item("number", "1038-VIII")
        + _item("name", "Про вигадані\n відносини")
        + _item(
            "publication", "Офіційний вісник України#01.03.2016#20#5", "Голос України#02.03.16#1#1"
        )
        + _item("reestr_code", "81504/2016")
    )
    cards = list(iter_cards(raw))
    assert len(cards) == 1
    assert cards[0]["name"] == ["Про вигадані відносини"]
    assert len(cards[0]["publication"]) == 2


def test_texts_keep_one_line_per_source_line() -> None:
    raw = _export(
        _item("reestr_kod", "12/1997") + '<item name="TEXT_ED">\n<richtext>\n<par def="1">\n'
        "ЗАКОН УКРАЇНИ\nСтаття 1. Тест\n</par>\n</richtext>\n</item>\n"
    )
    [(code, text)] = list(iter_texts(raw))
    assert code == "12/1997"
    assert [line for line in text.splitlines() if line] == ["ЗАКОН УКРАЇНИ", "Стаття 1. Тест"]


def test_the_snapshot_date_comes_from_the_member_name() -> None:
    assert snapshot_date("edrnpa_cards_2026-11-08.xml") == date(2026, 11, 8)
    assert snapshot_date("cards.xml") is None


@pytest.mark.parametrize(
    ("card", "kind", "expected"),
    [
        (
            _card(publisher=_LAW, type="Закон України", number="1038-VIII", date_acc="11.02.2016"),
            "law",
            "1038-19",
        ),
        (
            _card(publisher=_LAW, type="Закон України", number="2341-XIV", date_acc="05.04.2001"),
            "law",
            "2341-14",
        ),
        (
            _card(publisher=_LAW, type="Закон України", number="710/97-ВР", date_acc="10.12.1997"),
            "law",
            "710/97-вр",
        ),
        (
            _card(publisher=_CABINET, type="Постанова", number="274", date_acc="30.03.2016"),
            "cabinet_resolution",
            "274-2016-п",
        ),
        (
            _card(publisher=_CABINET, type="Постанова", number="1473", date_acc="29.12.1997"),
            "cabinet_resolution",
            "1473-97-п",
        ),
        (
            _card(publisher=_CABINET, type="Розпорядження", number="270-р", date_acc="30.03.2016"),
            "cabinet_order",
            "270-2016-р",
        ),
        (
            _card(
                publisher="Президент України", type="Указ", number="121/2016", date_acc="30.03.2016"
            ),
            "president",
            "121/2016",
        ),
        (
            _card(
                publisher="Міністерство юстиції України",
                type="Наказ",
                number="639/5",
                date_acc="01.03.2016",
                reg_number="364/28494",
                reg_date="11.03.2016",
            ),
            "minjust_registered",
            "z0364-16",
        ),
        (
            _card(
                publisher="Правління Національного банку України",
                type="Постанова",
                number="333",
                date_acc="01.09.1996",
            ),
            "central_bank",
            "v0333500-96",
        ),
    ],
)
def test_each_issuer_numbers_to_its_rada_ref(card: Card, kind: str, expected: str) -> None:
    assert card_class(card) == kind
    assert expected in ref_candidates(card)


def test_convocations_count_from_independence_after_the_fourteenth() -> None:
    assert rada_convocation("IX") == 20
    assert rada_convocation("XII") == 12


def test_treaties_and_soviet_acts_are_their_own_classes() -> None:
    treaty = _card(type="Угода (двосторонній міжурядовий міжнародний договір)")
    assert card_class(treaty) == "treaty"
    assert (
        card_class(_card(publisher="Рада Міністрів Української РСР", type="Постанова")) == "soviet"
    )


def test_a_rule_wins_then_a_unique_title_and_year_fall_back() -> None:
    matcher = RefMatcher.of(
        [
            ("1038-19", "Про вигадані відносини"),
            ("n0001-16", "Про пам'ятний знак"),
            ("n0002-16", "Про двійника"),
            ("n0003-16", "Про двійника"),
        ]
    )
    law = _card(publisher=_LAW, type="Закон України", number="1038-VIII", date_acc="11.02.2016")
    hit = matcher.match(law)
    assert (hit.ref, hit.how) == ("1038-19", "rule")
    titled = _card(type="Угода", name="Про пам’ятний знак", date_acc="01.01.2016")
    hit = matcher.match(titled)
    assert (hit.ref, hit.how) == ("n0001-16", "title")
    assert matcher.match(titled).cause == "title_taken"
    twin = _card(type="Угода", name="Про двійника", date_acc="01.01.2016")
    assert matcher.match(twin).cause == "title_ambiguous"
    stray = _card(publisher=_CABINET, type="Постанова", number="9", date_acc="01.01.2016")
    assert matcher.match(stray).cause == "number_not_in_registry"
    assert matcher.match(_card(type="Угода", name="Інше")).cause == "no_number_scheme"


_REGISTERED = "Зареєстровано в Міністерстві юстиції України\n1 березня 2016 р. за N"
_ANNEX = f"ІНСТРУКЦІЯ\n{_REGISTERED} 365/28495\n1. Текст"


def test_a_registered_annex_unit_follows_the_act() -> None:
    act = f"НАКАЗ\n{_REGISTERED} 364/28494\nТекст"
    assert registration_of(_ANNEX) == "365/28495"
    assert compose_units([_ANNEX, act], "364/28494") == f"{act}{UNIT_BREAK}{_ANNEX}"


def test_two_units_that_could_each_be_the_act_compose_to_nothing() -> None:
    assert compose_units(["ЗАКОН УКРАЇНИ\nЗміст", "ЗАКОН УКРАЇНИ\nСтаття 1."], None) is None
    assert compose_units(["ЗАКОН УКРАЇНИ"], None) == "ЗАКОН УКРАЇНИ"


def test_the_record_carries_iso_dates_and_split_publications() -> None:
    card = _card(
        reestr_code="81504/2016",
        reestr_date="07.04.2016",
        date_acc="11.02.2016",
        status="Чинний",
        publication="Відомості Верховної Ради України#15.04.2016#16#167",
        reg_number="364/28494",
        reg_date="11.03.2016",
    )
    record = card_record(card, "law", "rule")
    assert record["adopted"] == "2016-02-11"
    assert record["publications"] == [
        {
            "source": "Відомості Верховної Ради України",
            "date": "2016-04-15",
            "issue": "16",
            "article": "167",
        }
    ]
    assert record["registrations"] == [{"number": "364/28494", "date": "2016-03-11"}]


def test_the_attribution_names_the_page_licence_snapshot_and_caveat() -> None:
    line = attribution(date(2026, 11, 8))
    assert "data.gov.ua/dataset/" in line and "CC BY 4.0" in line
    assert "snapshot of 8 November 2026" in line
    assert line.endswith("Texts are as adopted and may not reflect later amendments.")
