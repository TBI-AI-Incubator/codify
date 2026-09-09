"""Injected passive modifications must round-trip through the AKN reader."""

from __future__ import annotations

from codify.akn.io import parse_akn
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.structured_meta import inject_passive_mods
from codify.pipeline.parsers.base import AmendmentAnnotation

_BLUEBELL = """PREFACE
  Тестовий закон

BODY

  ARTICLE 1
    Перша стаття.

  ARTICLE 2
    Друга стаття.
"""


def test_injected_mods_surface_as_amendment_effects() -> None:
    akn = parse_to_akn(
        _BLUEBELL, country="ua", doctype="act", date="2020-01-01", number="1", language="ukr"
    )
    note = AmendmentAnnotation(
        target_eid="art_2",
        akn_action="insertion",
        amender_href="/go/3384-20",
        amender_label="№ 3384-IX від 20.09.2023",
        text="{Статтю 2 доповнено ...}",
    )
    enriched = inject_passive_mods(akn, [note])
    doc = parse_akn(enriched)
    mods = [m for m in doc.textual_mods if m.authority_uri == "/go/3384-20"]
    assert len(mods) == 1
    mod = mods[0]
    assert mod.akn_action == "insertion"
    # destination points at the amended unit of THIS work, not the amender.
    assert mod.target_frbr_uri.endswith("#art_2")
    assert doc.frbr_work_uri in mod.target_frbr_uri
    assert mod.target_akn_wid == "art_2"


def test_injection_is_idempotent() -> None:
    akn = parse_to_akn(
        _BLUEBELL, country="ua", doctype="act", date="2020-01-01", number="1", language="ukr"
    )
    note = AmendmentAnnotation(
        target_eid="art_1",
        akn_action="repeal",
        amender_href="/go/198-19",
        amender_label="№ 198-VIII",
        text="{Статтю 1 виключено}",
    )
    once = inject_passive_mods(akn, [note])
    twice = inject_passive_mods(once, [note])
    assert once.count("textualMod") == twice.count("textualMod")
