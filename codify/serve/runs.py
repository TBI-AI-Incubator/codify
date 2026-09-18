"""Runs as background tasks over an in-memory table: events buffered for late clients,
nothing surviving a restart, and each run saying so in `lost_on_restart`."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from codify.pipeline.events import Complete, Failed, IngestionEvent

Status = Literal["queued", "running", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

# What a run does: given its params, yield pipeline events until Complete or Failed.
Runner = Callable[[dict[str, Any]], AsyncIterator[IngestionEvent]]


@dataclass
class Run:
    kind: str
    params: dict[str, Any]
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: Status = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    retry_of: uuid.UUID | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    lost_on_restart: bool = True

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "retry_of": str(self.retry_of) if self.retry_of else None,
            "events": len(self.events),
            "lost_on_restart": self.lost_on_restart,
        }


def _slim(event: IngestionEvent) -> dict[str, Any]:
    """The event as JSON, minus the document and AKN a Complete carries."""
    if isinstance(event, Complete):
        return {
            "kind": event.kind,
            "work_uri": event.document.frbr_work_uri,
            "expression_uri": event.document.frbr_expression_uri,
        }
    return event.model_dump(mode="json")


class QueueFull(Exception):
    """More runs are waiting than the table admits; try again later."""


class RunTable:
    """Enqueue, watch, cancel and retry runs; `concurrency` bounds how many execute at
    once, `keep` how many finished runs stay readable before the oldest is forgotten."""

    def __init__(
        self, runner: Runner, *, concurrency: int = 2, keep: int = 200, max_queued: int = 100
    ) -> None:
        self._runner = runner
        self._keep = keep
        self._max_queued = max_queued
        self._runs: dict[uuid.UUID, Run] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[Status]] = {}
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any] | None]]] = {}
        self._gate = asyncio.Semaphore(concurrency)

    def get(self, run_id: uuid.UUID) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[Run]:
        return sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)

    def enqueue(
        self, kind: str, params: dict[str, Any], *, retry_of: uuid.UUID | None = None
    ) -> Run:
        queued = sum(r.status == "queued" for r in self._runs.values())
        if queued >= self._max_queued:
            raise QueueFull(f"{queued} runs already queued")
        run = Run(kind=kind, params=params, retry_of=retry_of)
        self._runs[run.id] = run
        self._subscribers[run.id] = set()
        task = asyncio.create_task(self._execute(run))
        task.add_done_callback(lambda t: self._finish(run, t))
        self._tasks[run.id] = task
        return run

    def retry(self, run_id: uuid.UUID) -> Run | None:
        """A new run with the old params; the old one keeps its outcome."""
        old = self._runs.get(run_id)
        if old is None or old.status not in TERMINAL:
            return None
        return self.enqueue(old.kind, dict(old.params), retry_of=old.id)

    def cancel(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        run = self._runs.get(run_id)
        if task is None or run is None or run.status in TERMINAL:
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        """Cancel what is running and wait for every task to end."""
        for run_id in list(self._tasks):
            self.cancel(run_id)
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def stream(self, run_id: uuid.UUID) -> AsyncIterator[dict[str, Any]]:
        """Every event so far, then each new one, until the run ends."""
        run = self._runs.get(run_id)
        if run is None:
            return
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        try:
            # Snapshot both under the subscription: a run that ends during the replay
            # has put its last events on the queue, and the drain below reads them.
            replay, was_terminal = list(run.events), run.status in TERMINAL
            for event in replay:
                yield event
            if was_terminal:
                return
            while (queued := await queue.get()) is not None:
                yield queued
        finally:
            # The run may have been forgotten while this subscriber was draining.
            self._subscribers.get(run_id, set()).discard(queue)

    def _publish(self, run: Run, event: dict[str, Any] | None) -> None:
        if event is not None:
            run.events.append(event)
        for queue in self._subscribers[run.id]:
            queue.put_nowait(event)

    async def _execute(self, run: Run) -> Status:
        """Returns the verdict; `_finish` applies it, so status and completion move together."""
        async with self._gate:
            run.status = "running"
            run.started_at = datetime.now(UTC)
            # The verdict waits for the iterator to end: a lane may report a Failed
            # pass and still reach Complete, so the last of the two decides.
            outcome: Complete | Failed | None = None
            async for event in self._runner(run.params):
                self._publish(run, _slim(event))
                if isinstance(event, Complete | Failed):
                    outcome = event
            if isinstance(outcome, Complete):
                run.result = _slim(outcome)
                return "succeeded"
            run.error = (
                f"{outcome.stage}: {outcome.error}"
                if isinstance(outcome, Failed)
                else "the pipeline ended without Complete or Failed"
            )
            return "failed"

    def _finish(self, run: Run, task: asyncio.Task[Status]) -> None:
        """Runs when the task ends, however it ends: cancelled before its first step included."""
        if task.cancelled():
            run.status = "cancelled"
        elif (exc := task.exception()) is not None:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
        else:
            run.status = task.result()
        run.completed_at = datetime.now(UTC)
        self._publish(run, None)
        self._forget_oldest()

    def _forget_oldest(self) -> None:
        # Only runs whose done callback has run: a task that has returned but not
        # yet called _finish still needs its maps to publish the end sentinel.
        done = sorted(
            (r for r in self._runs.values() if r.completed_at is not None),
            key=lambda r: r.completed_at or r.created_at,
        )
        for old in done[: max(0, len(done) - self._keep)]:
            for table in (self._runs, self._tasks, self._subscribers):
                table.pop(old.id, None)
