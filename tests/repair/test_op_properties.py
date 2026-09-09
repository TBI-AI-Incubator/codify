"""Property tests over the op vocabulary: atomic failure and text preservation.

For arbitrary (often nonsensical) op sequences, `apply_plan` must either accept
with body prose preserved, or return the byte-identical original, never a
half-applied document, never silent text loss.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.repair.edit_ops import SourceEvidence, _body_text_len, apply_plan
from codify.repair.ops import (
    Annotate,
    Delete,
    Merge,
    Move,
    MoveToConclusions,
    RenumberSequence,
    RepairOp,
    RestoreFromSource,
    SetBody,
    SetNum,
    Split,
)

_FIXTURE = parse_to_akn(
    "BODY\n  SECTION 1\n    ARTICLE 1\n      Body of article one.\n"
    "    ARTICLE 2\n      Body of article two.\n    ARTICLE 3\n",
    country="gb",
    doctype="act",
    number="1",
    date="2020-01-01",
)

_EIDS = st.sampled_from(
    ["sec_1", "sec_1__art_1", "sec_1__art_2", "sec_1__art_3", "art_9", "nope", ""]
)
_TEXTS = st.sampled_from(
    ["Replacement prose.", "ARTICLE 4", "(1) enumerated", "", "plain words here"]
)


def _ops() -> st.SearchStrategy[RepairOp]:
    return st.one_of(
        st.builds(SetBody, eid=_EIDS, bluebell=_TEXTS),
        st.builds(SetNum, eid=_EIDS, num=st.sampled_from(["1", "2", "9", ""])),
        st.builds(Move, eid=_EIDS, new_parent_eid=_EIDS),
        st.builds(Delete, eid=_EIDS),
        st.builds(Merge, eid_a=_EIDS, eid_b=_EIDS),
        st.builds(Split, eid=_EIDS, marker=_TEXTS),
        st.builds(Annotate, eid=_EIDS, note=_TEXTS),
        st.builds(
            RenumberSequence,
            parent_eid=_EIDS,
            kind=st.sampled_from(["article", "point"]),
            nums=st.lists(st.sampled_from(["1", "2", "3"]), max_size=4),
        ),
        st.builds(RestoreFromSource, eid=_EIDS),
        st.builds(MoveToConclusions, eid=_EIDS),
    )


_EVIDENCE = SourceEvidence(
    text="Article 1\nBody of article one.\n\nArticle 2\nBody of article two.\n",
    closing_phrases=["Issued in"],
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ops=st.lists(_ops(), min_size=1, max_size=3))
def test_apply_is_atomic_and_never_loses_prose(ops: list[RepairOp]) -> None:
    before_len = _body_text_len(_FIXTURE)
    res = apply_plan(_FIXTURE, ops, country="gb", evidence=_EVIDENCE)
    if not res.ok:
        assert res.xml == _FIXTURE  # rejection returns the original bytes
        return
    destructive = any(isinstance(op, (Delete, Merge, Split)) for op in ops)
    if not destructive:
        assert _body_text_len(res.xml) >= before_len
