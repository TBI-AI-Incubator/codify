"""Cellar's amendment annotations as an effects feed: the recorded CSV shape of
the SPARQL answer, no network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from codify.acquisition.effects import adapter_for
from codify.acquisition.effects.cellar import (
    BATCH,
    CellarEffectsAdapter,
    fallback_eid,
    location_eid,
    parse_results,
    publisher_id,
    sparql_for,
)

_CDM = "http://publications.europa.eu/ontology/cdm#"
_AUTH = "http://publications.europa.eu/resource/authority"

# The shape Cellar's endpoint answers for `sparql_for`, recorded on 2026-09-06
# and re-keyed to synthetic CELEX ids: an amended act, its amending acts, the
# coded subdivision, the coded change type and the start date.
_CSV = (Path(__file__).parent / "adapters" / "fixtures" / "cellar_effects_sample.csv").read_text()


def _result(celexes: list[str] | None = None):  # type: ignore[no-untyped-def]
    return parse_results(_CSV, celexes or ["31982D9043", "31973D9391", "31964L9225"])


def test_every_row_is_an_effect_with_our_work_uris_and_a_stable_id() -> None:
    results = _result()
    effects = results["31982D9043"].effects
    assert len(effects) == 9
    first = effects[0]
    assert first.affected_work == "/akn/eu/act/dec/1982/9043"
    assert first.affecting_work == "/akn/eu/act/dec/1995/9420"
    assert first.affected_provision.startswith("{AR|") and first.affecting_provision == ""
    assert first.in_force_date == date(1996, 1, 1)
    assert first.publisher_id.startswith("cellar:") and len(first.publisher_id) == 31
    # Derived from the facts: the same row again is the same id, a different date another.
    assert first.publisher_id == publisher_id(
        "31995D9420",
        "resource_legal_amends_resource_legal",
        "31982D9043",
        first.affected_provision,
        "J",
        "1996-01-01",
    )
    assert first.publisher_id != publisher_id(
        "31995D9420",
        "resource_legal_amends_resource_legal",
        "31982D9043",
        first.affected_provision,
        "J",
        "1997-01-01",
    )
    assert results["31964L9225"].effects == [] and results["31964L9225"].skipped == {}


def test_the_crosswalk_reads_the_change_type_and_falls_to_the_relation() -> None:
    effects = _result()["31982D9043"].effects
    by_raw = {e.raw_type: (e.akn_action, e.akn_category) for e in effects}
    assert by_raw["J:amends_resource_legal"] == ("insertion", "textual")
    assert by_raw["R:amends_resource_legal"] == ("substitution", "textual")
    assert by_raw["M:amends_resource_legal"] == (None, None)
    assert by_raw["SU:amends_resource_legal"] == ("repeal", "textual")
    assert by_raw["AP:amends_resource_legal"] == ("repeal", "textual")
    assert by_raw["-:repeals_resource_legal"] == ("repeal", "textual")
    corrected = _result()["31973D9391"].effects[0]
    assert (corrected.akn_action, corrected.akn_category) == ("substitution", "textual")
    assert corrected.raw_type == "-:corrects_resource_legal"


def test_skips_are_counted_by_reason_never_dropped() -> None:
    result = _result()["31982D9043"]
    assert result.skipped == {
        "undated": 1,
        "unmapped_type": 2,
        "foreign_source": 1,
        "point_collapsed": 2,
        "work_level": 1,
        "unparsed_location": 1,
        "division_collapsed": 1,
    }
    assert result.dated == 8
    foreign = next(e for e in result.effects if e.affecting_work is None)
    assert foreign.affected_provision.endswith(" 11")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (f"{{AR|{_AUTH}/fd_370/AR}} 8", ("art_8", None)),
        (f"{{AR|{_AUTH}/fd_370/AR}} 8.3", ("art_8__para_3", None)),
        (f"{{AR|{_AUTH}/fd_370/AR}} 8 {{PA|{_AUTH}/fd_370/PA}} 3", ("art_8__para_3", None)),
        (f"{{AR|{_AUTH}/fd_370/AR}} 8a", ("art_8a", None)),
        (
            f"{{AR|{_AUTH}/fd_370/AR}} 23 {{PA|{_AUTH}/fd_370/PA}} 1 "
            f"{{PTA|{_AUTH}/fd_370/PTA}} (j)",
            ("art_23__para_1", "point_collapsed"),
        ),
        (f"{{AR|{_AUTH}/fd_370/AR}} 1. 2", ("art_1__para_2", None)),
        (f"{{AN|{_AUTH}/fd_370/AN}} II {{PO|{_AUTH}/fd_370/PO}} 18", ("att_2", "point_collapsed")),
        (f"{{AN|{_AUTH}/fd_370/AN}} 1.A", ("att_1", None)),
        (f"{{TIT|{_AUTH}/fd_370/TIT}} IV", (None, "division_collapsed")),
        ("CH8 DEVIENT CH 7", (None, "unparsed_location")),
        ("", (None, "work_level")),
    ],
)
def test_a_coded_location_maps_to_an_eid_or_names_why_not(
    raw: str, expected: tuple[str | None, str | None]
) -> None:
    assert location_eid(raw) == expected


def test_a_paragraph_falls_back_to_its_article_and_nothing_else_falls() -> None:
    assert fallback_eid("art_8__para_3") == "art_8"
    assert fallback_eid("art_8") is None
    assert fallback_eid("att_2") is None


@pytest.mark.asyncio
async def test_fetch_many_batches_fifty_celex_a_query_and_fetch_is_one_of_them() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["query"])
        return httpx.Response(200, text=_CSV, headers={"content-type": "text/csv"})

    adapter = CellarEffectsAdapter(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    celexes = [f"3200{i:01d}R{i:04d}" for i in range(BATCH + 1)]
    results = await adapter.fetch_many(celexes)
    assert len(queries) == 2
    assert queries[0].count('"3200') == BATCH and queries[1].count('"3200') == 1
    assert set(results) >= set(celexes)
    one = await adapter.fetch("31982D9043")
    assert len(one.effects) == 9 and len(queries) == 3


def test_the_query_names_every_relation_and_the_batch() -> None:
    q = sparql_for(["31982D9043", "31973D9391"])
    assert 'VALUES ?celex { "31982D9043" "31973D9391" }' in q
    for relation in (
        "amends",
        "repeals",
        "implicitly_repeals",
        "corrects",
        "completes",
        "derogates",
    ):
        assert f"resource_legal_{relation}_resource_legal" in q
    assert "ann:reference_to_modified_location" in q and "ann:start_of_validity" in q


def test_the_registry_knows_the_publisher() -> None:
    adapter = adapter_for("eu", httpx.AsyncClient())
    assert isinstance(adapter, CellarEffectsAdapter)


def test_generic_amendment_keeps_source_evidence_without_inventing_action() -> None:
    result = _result()["31982D9043"]
    effect = next(e for e in result.effects if e.raw_type == "M:amends_resource_legal")
    assert (effect.akn_action, effect.akn_category) == (None, None)
    assert effect.publisher_id.startswith("cellar:")
    assert effect.affected_work == "/akn/eu/act/dec/1982/9043"
    assert effect.in_force_date is not None
    assert len([e for e in result.effects if e.raw_type == "M:amends_resource_legal"]) == 2
    assert result.skipped["unmapped_type"] == 2


@pytest.mark.parametrize("celex", ["32020L0001", "32020R1234", "32020D9043"])
def test_publisher_key_round_trips_the_canonical_work(celex: str) -> None:
    from codify.acquisition.adapters.eu.cellar import celex_to_frbr

    work = celex_to_frbr(celex)[0]
    assert CellarEffectsAdapter.publisher_path_for(work) == celex


@pytest.mark.parametrize(
    "work",
    [
        "/akn/xa/act/dir/2020/1",
        "/akn/eu/act/dir/2020/1/eng@2020-01-01",
        "/akn/eu/act/dir/2020/01",
        "/akn/eu/act/unknown/2020/1",
        "nonsense",
    ],
)
def test_publisher_key_refuses_foreign_or_noncanonical_work(work: str) -> None:
    assert CellarEffectsAdapter.publisher_path_for(work) is None


def test_registry_adapter_implements_the_writer_contract() -> None:
    from codify.acquisition.effects import EffectsAdapter

    adapter = adapter_for("eu", httpx.AsyncClient())
    assert isinstance(adapter, EffectsAdapter)
    assert adapter.target_eid(f"{{AR|{_AUTH}/fd_370/AR}} 8") == "art_8"
    assert adapter.target_eid("unrecognised location") is None
    assert all(e.applied is None for result in _result().values() for e in result.effects)


@pytest.mark.parametrize("role", ["M", "UNKNOWN"])
def test_an_explicit_unmapped_role_is_not_replaced_by_the_relation(role: str) -> None:
    import csv
    import io

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["celex", "p", "other", "loc", "role", "from"])
    writer.writeheader()
    writer.writerow(
        {
            "celex": "31982D9043",
            "p": _CDM + "resource_legal_repeals_resource_legal",
            "role": "{" + role + "|" + _AUTH + "/fd_375/" + role + "}",
        }
    )
    result = parse_results(output.getvalue(), ["31982D9043"])["31982D9043"]
    assert (result.effects[0].akn_action, result.effects[0].akn_category) == (None, None)
    assert result.effects[0].raw_type.startswith(role + ":")
    assert result.skipped["unmapped_type"] == 1


@pytest.mark.parametrize("role,relation", [("C", "amends"), ("", "completes")])
def test_completion_crosswalk_is_substitution(role: str, relation: str) -> None:
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(_CSV)))
    row = rows[0].copy()
    row["role"] = f"{{{role}|{_AUTH}/fd_375/{role}}}" if role else ""
    row["p"] = f"{_CDM}resource_legal_{relation}_resource_legal"
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    effect = parse_results(stream.getvalue(), ["31982D9043"])["31982D9043"].effects[0]
    assert effect.akn_action == "substitution"
