"""#967 proof: the structuring pipeline reaches a valid AKN with no gateway.

An outside contributor has no LiteLLM gateway and no model key, and CI on a fork
has no secrets. Structuring is anchor-driven: a deterministic skeleton from the
jurisdiction config plus per-window body-fill. This test drives the real
``text_to_bluebell_scaffolded`` → ``parse_to_akn`` → ``validate_akn`` path on a
SYNTHETIC document with a deterministic fake body-fill client, so the offline
pipeline shape is exercised end to end.

It deliberately does NOT prove body-fill QUALITY (a real model writing faithful
prose from source), that is the online eval's job, and faking a quality proof
in CI would breach abstain-is-not-pass. The fake fills each anchor with a fixed
sentence; what is proven is that scan, scaffold, window fill, assembly, Bluebell
parse and AKN validation all run and agree with no model call.
"""

from __future__ import annotations

import re

import pytest
from lxml import etree

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.scaffold import BodyBlock, BodyFillResponse
from codify.pipeline.enrich.structure import text_to_bluebell_scaffolded
from codify.pipeline.enrich.validator import validate_akn

# A fictional Atlantis (xa) act. No real legislation: the open tree ships no
# corpus content, and the synthetic jurisdiction config drives the anchor scan.
_SYNTHETIC_ACT = """\
PART I
PRELIMINARY

Section 1
This Act may be cited as the Harbour Widgets Act.

Section 2
In this Act, unless the context otherwise requires, "widget" means a mechanical
device registered under this Act.

PART II
THE AUTHORITY

Section 3
There is established a body to be known as the Harbour Widgets Authority.

Section 4
The Authority shall maintain the register and publish it in the Gazette.
"""


class _DeterministicBodyFillClient:
    """Offline stand-in for the body-fill LLM. Reads the eIds the pipeline wrote
    into each window scaffold and returns a fixed body line per eId. Exercises
    scan → scaffold → fill → assemble → validate with no gateway; it models the
    pipeline shape, not body-fill quality. Every non-body-fill entry point raises
    so a regression that routes structuring through chat()/vision() is loud."""

    _EID_RE = re.compile(r"eid=(\S+)")

    def __init__(self) -> None:
        self.schema_calls = 0

    async def chat_schema(self, prompt, schema, system=None, model=None):
        self.schema_calls += 1
        eids = self._EID_RE.findall(prompt)
        return BodyFillResponse(
            bodies=[BodyBlock(eid=eid, lines=[f"Body text for {eid}."]) for eid in eids]
        )

    async def chat(self, *a, **k):
        raise AssertionError("offline structuring must not call chat()")

    async def chat_stream(self, *a, **k):
        raise AssertionError("offline structuring must not call chat_stream()")

    async def chat_json(self, *a, **k):
        raise AssertionError("offline structuring must not call chat_json()")

    async def chat_schema_stream(self, *a, **k):
        raise AssertionError("offline structuring must not call chat_schema_stream()")

    async def vision(self, *a, **k):
        raise AssertionError("offline structuring must not call vision()")

    async def ocr(self, *a, **k):
        raise AssertionError("offline structuring must not call ocr()")


_FAULT_SEVERITIES = {"error", "fault", "blocking"}


@pytest.mark.asyncio
async def test_structuring_reaches_valid_akn_with_no_gateway() -> None:
    fake = _DeterministicBodyFillClient()

    bluebell = await text_to_bluebell_scaffolded(
        _SYNTHETIC_ACT, client=fake, country="xa", doctype="act"
    )
    assert fake.schema_calls > 0, "body-fill never ran; pipeline did not reach the fill stage"

    akn = parse_to_akn(bluebell, "xa", doctype="act", date="1994", number="1")

    # Every synthetic anchor became a STRUCTURAL element, not just prose: assert
    # on the eId attribute (which the fake's body text `Body text for <eid>.`
    # cannot satisfy) and on the element counts. eIds are parent-prefixed.
    tree = etree.fromstring(akn.encode())
    ns = {"a": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"}
    part_eids = {e.get("eId") for e in tree.iterfind(".//a:part", ns)}
    section_eids = {e.get("eId") for e in tree.iterfind(".//a:section", ns)}
    assert part_eids == {"part_I", "part_II"}, part_eids
    assert section_eids == {
        "part_I__sec_1",
        "part_I__sec_2",
        "part_II__sec_3",
        "part_II__sec_4",
    }, section_eids

    findings = validate_akn(akn)
    faults = [f for f in findings if str(f.get("severity", "")).lower() in _FAULT_SEVERITIES]
    assert not faults, f"offline pipeline produced AKN with validator faults: {faults}"
