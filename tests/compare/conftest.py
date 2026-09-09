"""Shared fixtures for compare/ tests."""

from __future__ import annotations

from datetime import date

import pytest

from codify.akn.document import Document
from codify.akn.elements import Article, BodyElement, Chapter


def _article(eid: str, heading: str, text: str) -> Article:
    return Article(
        akn_eid=eid,
        akn_type="article",
        position=0,
        heading=heading,
        text=text,
    )


def _chapter(eid: str, heading: str, children: list[BodyElement]) -> Chapter:
    return Chapter(
        akn_eid=eid,
        akn_type="chapter",
        position=0,
        heading=heading,
        children=children,
    )


@pytest.fixture
def directive_doc() -> Document:
    return Document(
        frbr_work_uri="/akn/eu/directive/2016/943",
        frbr_expression_uri="/akn/eu/directive/2016/943/eng@2016-06-08",
        language="eng",
        expression_date=date(2016, 6, 8),
        body=[
            _chapter(
                "chp_1",
                "General provisions",
                [
                    _article(
                        "art_1",
                        "Subject matter and scope",
                        "This Directive lays down rules on the protection of trade secrets.",
                    ),
                    _article(
                        "art_2",
                        "Definitions",
                        "For the purposes of this Directive, 'trade secret' means information.",
                    ),
                ],
            ),
        ],
    )


@pytest.fixture
def domestic_doc() -> Document:
    return Document(
        frbr_work_uri="/akn/al/act/2018/35",
        frbr_expression_uri="/akn/al/act/2018/35/eng@2018-04-15",
        language="sqi",
        expression_date=date(2018, 4, 15),
        body=[
            _article(
                "art_a",
                "Object of the law",
                "This law regulates the protection of trade secrets in the Republic.",
            ),
            _article(
                "art_b",
                "Definitions",
                "Trade secret means commercially valuable confidential information.",
            ),
            _article(
                "art_c",
                "Unrelated",
                "This article addresses an unrelated matter.",
            ),
        ],
    )
