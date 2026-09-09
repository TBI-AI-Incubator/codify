"""Progress emitter for long async iterables.

Wraps ``asyncio.gather`` with a bounded semaphore plus periodic
``ProgressEvent`` emission, by item count, by wall-clock interval, or
both. A concurrent heartbeat task fires events even when zero items
have completed in a window, so silent stalls (LLM gateway hung,
network partition) are detectable from the structured log alone.

Designed for any pipeline stage whose loop is plausibly longer than
30 seconds: the comparator's directive-provision loop, the
chunked-structurer's per-chunk LLM calls, etc.

Usage:

    results = await with_progress(items, work, name=..., total=..., every_n=10,
                                  every_s=30.0, on_event=cb, concurrency=4)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class ProgressEvent:
    """Snapshot of a long loop's state at a moment in time.

    Emitted both per-item (every ``every_n``) and per-time (every
    ``every_s``), consumers should dedupe by ``done`` if they need to.
    """

    name: str
    done: int
    total: int
    elapsed_s: float
    eta_s: float | None
    latest: str | None


async def with_progress(
    items: Iterable[T],
    work: Callable[[T], Awaitable[R]],
    *,
    name: str,
    total: int,
    every_n: int = 10,
    every_s: float = 30.0,
    on_event: Callable[[ProgressEvent], None] | None = None,
    on_result: Callable[[R], None] | None = None,
    concurrency: int = 1,
    label_of: Callable[[T], str] | None = None,
) -> list[R]:
    """Run ``work`` over every item with periodic progress emission.

    Returns results in input order. Emits a ``ProgressEvent`` every
    ``every_n`` completed items OR every ``every_s`` wall-clock seconds
    (whichever first), plus a heartbeat that fires when no items have
    completed in ``every_s`` so silent stalls are visible.

    ``on_result`` fires per completed item in completion order (not input
    order), under the same lock as progress emission, so downstream consumers
    can bridge results to a streaming surface without waiting for the whole
    loop to gather. Must not block or await (holds the emit lock). A raise
    from ``on_result`` kills the loop, same contract as ``on_event``, wrap
    the callback yourself if you need swallow-and-log semantics.

    ``concurrency`` caps in-flight work via a semaphore; ``label_of``
    builds the per-event ``latest`` string for hang debugging (default
    ``repr`` truncated to 40 chars).
    """
    if every_n <= 0:
        raise ValueError("every_n must be positive")
    if every_s <= 0:
        raise ValueError("every_s must be positive")
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")

    items_list = list(items)
    if total < len(items_list):
        # The caller's declared total may be smaller than the actual
        # iterable when items have been pre-filtered upstream; trust the
        # iterable length for monotonic safety.
        total = len(items_list)

    started_at = time.monotonic()
    done = 0
    latest: str | None = None
    last_emit_done = 0
    last_emit_at = started_at
    emit_lock = asyncio.Lock()

    def _label(item: T) -> str:
        if label_of is not None:
            return label_of(item)
        return repr(item)[:40]

    def _build_event() -> ProgressEvent:
        elapsed = time.monotonic() - started_at
        eta: float | None = None
        if done > 0 and elapsed > 0:
            rate = done / elapsed
            remaining = max(total - done, 0)
            eta = remaining / rate if rate > 0 else None
        return ProgressEvent(
            name=name,
            done=done,
            total=total,
            elapsed_s=elapsed,
            eta_s=eta,
            latest=latest,
        )

    async def _maybe_emit_locked() -> None:
        # Caller must hold `emit_lock`.
        nonlocal last_emit_done, last_emit_at
        if on_event is None:
            return
        now = time.monotonic()
        if done - last_emit_done >= every_n or now - last_emit_at >= every_s:
            on_event(_build_event())
            last_emit_done = done
            last_emit_at = now

    async def _heartbeat() -> None:
        # Re-emits whenever no item has completed in `every_s`. Without
        # this a stalled LLM call leaves the log silent.
        nonlocal last_emit_at
        if on_event is None:
            return
        try:
            while True:
                await asyncio.sleep(every_s)
                async with emit_lock:
                    now = time.monotonic()
                    if now - last_emit_at >= every_s:
                        on_event(_build_event())
                        last_emit_at = now
        except asyncio.CancelledError:
            return

    sem = asyncio.Semaphore(concurrency)

    async def _one(idx: int, item: T) -> tuple[int, R]:
        nonlocal done, latest
        async with sem:
            result = await work(item)
            # Mutate + emit under one lock so concurrency>1 can't
            # interleave done/latest with a parallel emit.
            async with emit_lock:
                done += 1
                latest = _label(item)
                await _maybe_emit_locked()
                if on_result is not None:
                    on_result(result)
            return idx, result

    heartbeat_task = asyncio.create_task(_heartbeat())
    try:
        coros = [_one(i, item) for i, item in enumerate(items_list)]
        pairs = await asyncio.gather(*coros)
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(BaseException):
            await heartbeat_task

    # One final event so consumers always see a terminal snapshot with
    # ``done == total``. Bypasses the cadence gate.
    if on_event is not None:
        on_event(_build_event())

    pairs.sort(key=lambda p: p[0])
    return [r for _, r in pairs]
