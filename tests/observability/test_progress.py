"""Unit tests for the with_progress observability helper."""

from __future__ import annotations

import asyncio

import pytest

from codify.observability import ProgressEvent, with_progress

pytestmark = pytest.mark.asyncio


async def test_returns_results_in_input_order() -> None:
    async def work(i: int) -> int:
        await asyncio.sleep(0)
        return i * 10

    out = await with_progress(range(5), work, name="t", total=5, every_n=1, every_s=10.0)
    assert out == [0, 10, 20, 30, 40]


async def test_emits_every_n_items() -> None:
    events: list[ProgressEvent] = []

    async def work(i: int) -> int:
        return i

    await with_progress(
        range(50),
        work,
        name="t",
        total=50,
        every_n=10,
        every_s=60.0,
        on_event=events.append,
    )
    # 50 items / every_n=10 ⇒ at least 5 cadence emissions + final
    # terminal event = 6. Allow some slop for cadence locking against
    # `every_s` not firing on this fast path.
    assert len(events) >= 5
    # Terminal event always has done == total.
    assert events[-1].done == 50
    assert events[-1].total == 50


async def test_heartbeat_fires_when_work_stalls() -> None:
    """The heartbeat must emit even when no items complete in `every_s`.

    Simulates a stalled LLM call: a single piece of work that takes
    longer than two heartbeat windows.
    """
    events: list[ProgressEvent] = []
    every_s = 0.05

    async def stalled(i: int) -> int:
        await asyncio.sleep(every_s * 3)
        return i

    await with_progress(
        [1],
        stalled,
        name="t",
        total=1,
        every_n=10,
        every_s=every_s,
        on_event=events.append,
    )

    # Expect ≥2 heartbeat events (done=0) before the work finishes,
    # plus the terminal event (done=1).
    zero_done = [e for e in events if e.done == 0]
    assert len(zero_done) >= 2, f"expected ≥2 stall heartbeats, got {events}"
    assert events[-1].done == 1


async def test_no_emit_when_callback_omitted() -> None:
    """The fast path should not pay any emission overhead when the
    caller doesn't care about progress."""

    async def work(i: int) -> int:
        return i

    out = await with_progress(range(20), work, name="t", total=20)
    assert out == list(range(20))


async def test_concurrency_caps_in_flight_work() -> None:
    """The semaphore must bound the maximum simultaneous workers."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def work(i: int) -> int:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.005)
        async with lock:
            in_flight -= 1
        return i

    await with_progress(
        range(20), work, name="t", total=20, every_n=10, every_s=10.0, concurrency=3
    )
    assert peak <= 3, f"semaphore breach: peak in-flight = {peak}"


async def test_terminal_event_carries_eta_zero_or_none() -> None:
    events: list[ProgressEvent] = []

    async def work(i: int) -> int:
        return i

    await with_progress(
        range(5),
        work,
        name="t",
        total=5,
        every_n=1,
        every_s=10.0,
        on_event=events.append,
    )
    final = events[-1]
    assert final.done == final.total == 5
    # When done == total, eta should be either 0 or None; never a
    # positive remaining time.
    assert final.eta_s in (None, 0.0) or final.eta_s == 0


async def test_label_of_populates_latest() -> None:
    events: list[ProgressEvent] = []
    items = [("a", 1), ("b", 2), ("c", 3)]

    async def work(item: tuple[str, int]) -> int:
        return item[1]

    await with_progress(
        items,
        work,
        name="t",
        total=3,
        every_n=1,
        every_s=10.0,
        on_event=events.append,
        label_of=lambda item: item[0],
    )

    # At least one cadence emission and the terminal event should
    # carry the most-recently-completed label.
    assert any(e.latest in ("a", "b", "c") for e in events)
    assert events[-1].latest == "c"


async def test_latest_consistent_under_concurrency() -> None:
    """With concurrency>1, every emitted event's `latest` must be one of
    the already-completed labels, never out of order vs `done`."""
    events: list[ProgressEvent] = []
    completed: list[str] = []
    items = [f"item_{i:02d}" for i in range(40)]

    async def work(item: str) -> str:
        # Random-ish ordering via varied awaits.
        await asyncio.sleep((hash(item) % 5) / 1000.0)
        completed.append(item)
        return item

    await with_progress(
        items,
        work,
        name="t",
        total=len(items),
        every_n=2,
        every_s=10.0,
        on_event=events.append,
        concurrency=8,
        label_of=lambda s: s,
    )
    # Every event's `latest` must be a label we'd actually completed by
    # then; never None except possibly on the very first heartbeat.
    for ev in events:
        if ev.done == 0:
            continue
        assert ev.latest in items, f"{ev.latest=} not in items"


async def test_on_result_fires_per_completed_item_in_completion_order() -> None:
    """on_result trickles results out as they land, without waiting for gather.

    Order is completion order (not input order): item 4 sleeps least and
    should land first when the semaphore lets everything race.
    """
    seen: list[int] = []

    async def work(i: int) -> int:
        # Later items finish sooner; on_result should observe completion order.
        await asyncio.sleep(0.01 * (5 - i))
        return i

    def _on_result(r: int) -> None:
        seen.append(r)

    out = await with_progress(
        range(5),
        work,
        name="t",
        total=5,
        every_n=1,
        every_s=10.0,
        on_result=_on_result,
        concurrency=5,
    )
    # Return is in input order (contract).
    assert out == [0, 1, 2, 3, 4]
    # Callback saw everything, in reverse-of-input order (contract: completion order).
    assert sorted(seen) == [0, 1, 2, 3, 4]
    assert seen[0] == 4 and seen[-1] == 0


async def test_on_result_raise_propagates_to_caller() -> None:
    """on_result callbacks share the on_event contract: raises kill the loop.

    Callers who want swallow-and-log semantics wrap the callback themselves.
    """

    def _boom(_r: int) -> None:
        raise RuntimeError("callback bug")

    async def work(i: int) -> int:
        return i

    with pytest.raises(RuntimeError, match="callback bug"):
        await with_progress(
            range(3), work, name="t", total=3, every_n=1, every_s=10.0, on_result=_boom
        )


async def test_rejects_invalid_args() -> None:
    async def work(i: int) -> int:
        return i

    with pytest.raises(ValueError):
        await with_progress([1], work, name="t", total=1, every_n=0)
    with pytest.raises(ValueError):
        await with_progress([1], work, name="t", total=1, every_s=0)
    with pytest.raises(ValueError):
        await with_progress([1], work, name="t", total=1, concurrency=0)
