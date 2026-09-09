"""The pdf/format lane collects the same scan-side validator evidence as ingest.

Wave-2b CLI-rubric fix: the format lane (the ingest-one CLI path) used to pass
only anchor-summary + source_text to validate_akn, so it graded on a weaker
rubric. _ScanCollector harvests orphaned_drops + container from the scan trace
(mirroring ingest_step_structure) while still forwarding to a caller's on_scan."""

from __future__ import annotations

from codify.pipeline.enrich.structure import AmbiguitySpan, ScanTrace
from codify.pipeline.formats.pdf import _ScanCollector


def _span(emitted_by: str, detail: dict, start: int, kind: str = "orphan_text") -> AmbiguitySpan:
    return AmbiguitySpan(
        kind=kind,
        start=start,
        end=start + 1,
        eid="",
        emitted_by=emitted_by,
        resolved=False,
        detail=detail,
    )


def test_collects_orphaned_drops_and_container_and_forwards() -> None:
    forwarded: list[ScanTrace] = []
    collector = _ScanCollector(forward=forwarded.append)
    trace = ScanTrace(
        anchors=(),
        coverage=None,
        ambiguity=(
            _span("declare_orphaned_drops", {"why": "lost art 3"}, 42),
            _span("something_else", {"noise": True}, 9, kind="unmatched_marker"),
        ),
        container={"present": True, "found": 2},
    )

    collector(trace)

    # Only declare_orphaned_drops spans, with their start merged in (as ingest does).
    assert collector.orphaned_drops == [{"why": "lost art 3", "start": 42}]
    assert collector.container == {"present": True, "found": 2}
    assert forwarded == [trace]  # the caller's on_scan still fires


def test_tolerates_no_forward_and_no_evidence() -> None:
    collector = _ScanCollector(forward=None)
    collector(ScanTrace(anchors=(), coverage=None))  # no ambiguity, no container
    assert collector.orphaned_drops == []
    assert collector.container is None


def test_accumulates_across_multiple_traces() -> None:
    collector = _ScanCollector(forward=None)
    collector(
        ScanTrace(
            anchors=(), coverage=None, ambiguity=(_span("declare_orphaned_drops", {"a": 1}, 1),)
        )
    )
    collector(
        ScanTrace(
            anchors=(),
            coverage=None,
            ambiguity=(_span("declare_orphaned_drops", {"b": 2}, 5),),
            container={"x": 1},
        )
    )
    assert collector.orphaned_drops == [{"a": 1, "start": 1}, {"b": 2, "start": 5}]
    assert collector.container == {"x": 1}  # last trace's container wins
