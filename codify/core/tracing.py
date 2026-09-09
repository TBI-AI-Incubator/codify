"""Correlation helpers shared by Langfuse, LiteLLM and application logs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Literal

import httpx
import structlog
from langfuse.types import TraceContext
from opentelemetry import propagate
from opentelemetry import trace as otel_trace

from codify.core.pricing import cost_details

if TYPE_CHECKING:
    # Importing it eagerly costs ~575ms, and everything imports this module.
    from pydantic_ai.settings import ModelSettings

logger = structlog.get_logger()


DEFAULT_ACTOR_ID = "codify-dev"
DEFAULT_RELEASE = os.environ.get("CODIFY_RELEASE", "dev")


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    """Stable identifiers for one Codify operation.

    ``langfuse_trace_id`` is derived from the durable run id, so a DBOS replay
    or process recovery rejoins the same trace instead of creating a new root.
    """

    run_id: str
    workflow_kind: str
    langfuse_trace_id: str
    jurisdiction: str | None = None
    actor_id: str | None = None
    org_id: str | None = None
    session_id: str | None = None
    release: str = DEFAULT_RELEASE

    def log_fields(self) -> dict[str, str]:
        fields = {
            "run_id": self.run_id,
            "workflow_kind": self.workflow_kind,
            "langfuse_trace_id": self.langfuse_trace_id,
            "release": self.release,
        }
        if self.jurisdiction:
            fields["jurisdiction"] = self.jurisdiction
        if self.actor_id:
            fields["actor_id"] = self.actor_id
        if self.session_id:
            fields["session_id"] = self.session_id
        return fields


current_actor: ContextVar[str] = ContextVar("codify_current_actor", default=DEFAULT_ACTOR_ID)
current_session: ContextVar[str | None] = ContextVar("codify_current_session", default=None)
current_release: ContextVar[str] = ContextVar("codify_current_release", default=DEFAULT_RELEASE)
current_telemetry: ContextVar[TelemetryContext | None] = ContextVar(
    "codify_current_telemetry", default=None
)


def set_current_actor(actor_id: str | None) -> None:
    current_actor.set(actor_id or DEFAULT_ACTOR_ID)


def get_current_actor() -> str:
    return current_actor.get()


def set_current_session(session_id: str | None) -> None:
    current_session.set(session_id or None)


def get_current_session() -> str | None:
    return current_session.get()


def get_current_release() -> str:
    return current_release.get()


def get_telemetry_context() -> TelemetryContext | None:
    return current_telemetry.get()


def make_run_telemetry(run_id: str, workflow_kind: str, params: dict[str, Any]) -> TelemetryContext:
    """Build deterministic correlation state from durable workflow inputs."""
    from langfuse import get_client

    return TelemetryContext(
        run_id=run_id,
        workflow_kind=workflow_kind,
        langfuse_trace_id=get_client().create_trace_id(seed=run_id),
        jurisdiction=params.get("jurisdiction_code"),
        actor_id=params.get("actor_id"),
        org_id=params.get("org_id"),
        session_id=params.get("session_id"),
        # Read at workflow entry rather than relying on the import-time default.
        release=os.environ.get("CODIFY_RELEASE", DEFAULT_RELEASE),
    )


def make_request_telemetry(
    kind: str,
    correlation_id: str,
    *,
    jurisdiction: str | None = None,
    org_id: str | None = None,
) -> TelemetryContext:
    """Correlation state for an HTTP path, which has no durable run to key on.
    `correlation_id` takes the run id's place, so something stable across a
    conversation shares one trace. It names no `runs` row; `workflow_kind` says
    which surface it was. Actor and session are already stamped per request.
    """
    from langfuse import get_client

    return TelemetryContext(
        run_id=correlation_id,
        workflow_kind=kind,
        langfuse_trace_id=get_client().create_trace_id(seed=correlation_id),
        jurisdiction=jurisdiction,
        actor_id=get_current_actor(),
        org_id=org_id,
        session_id=get_current_session(),
        release=os.environ.get("CODIFY_RELEASE", DEFAULT_RELEASE),
    )


@contextmanager
def bind_telemetry(context: TelemetryContext) -> Iterator[TelemetryContext]:
    """Bind one operation to contextvars, structlog and the active OTel span."""
    telemetry_token = current_telemetry.set(context)
    actor_token = current_actor.set(context.actor_id or DEFAULT_ACTOR_ID)
    session_token = current_session.set(context.session_id)
    release_token = current_release.set(context.release)
    fields = context.log_fields()
    try:
        span = otel_trace.get_current_span()
        if span and span.is_recording():
            for key, value in fields.items():
                span.set_attribute(f"codify.{key}", value)
        with structlog.contextvars.bound_contextvars(**fields):
            yield context
    finally:
        current_release.reset(release_token)
        current_session.reset(session_token)
        current_actor.reset(actor_token)
        current_telemetry.reset(telemetry_token)


def langfuse_trace_context() -> TraceContext | None:
    context = get_telemetry_context()
    return {"trace_id": context.langfuse_trace_id} if context else None


def proxy_trace_headers() -> dict[str, str]:
    """W3C plus LiteLLM's native Langfuse callback correlation contract, which maps
    lowercase ``langfuse_*`` proxy headers to callback metadata.
    ``existing_trace_id`` rather than ``trace_id``: the latter follows the
    trace-create path and can replace root fields on every generation.
    """
    headers: dict[str, str] = {}
    propagate.inject(headers)
    context = get_telemetry_context()
    if context is None:
        return headers

    headers.update(
        {
            "langfuse_existing_trace_id": context.langfuse_trace_id,
            "langfuse_run_id": context.run_id,
            "langfuse_workflow_kind": context.workflow_kind,
        }
    )
    if context.jurisdiction:
        headers["langfuse_jurisdiction"] = context.jurisdiction
    if context.actor_id:
        headers["langfuse_actor_id"] = context.actor_id
    if context.session_id:
        # Avoid LiteLLM's special ``session_id`` key: in the existing-trace
        # branch it is popped but not applied. The trace itself carries it.
        headers["langfuse_codify_session_id"] = context.session_id
    try:
        from langfuse import get_client

        parent_id = get_client().get_current_observation_id()
        if parent_id:
            headers["langfuse_parent_observation_id"] = parent_id
    except Exception as exc:  # noqa: BLE001 - tracing must never break a model call
        logger.warning("langfuse_parent_lookup_failed", error_type=type(exc).__name__)
    return headers


def proxy_call_metadata() -> dict[str, Any]:
    """Per-call attribution sent as request `metadata`, which LiteLLM forwards to its
    callbacks; the keys are a documented contract, not a vendor import.
    `session_id` is deliberately absent: LiteLLM resolves the trace id from it
    before `metadata.trace_id`, which would scatter every run's calls.
    """
    context = get_telemetry_context()
    if context is None:
        # Without `user_id` LiteLLM falls back to the trace id for
        # `distinct_id`, minting a profile per call. Costs still count.
        return {"$process_person_profile": False}
    groups: dict[str, str] = {}
    if context.org_id:
        groups["organisation"] = context.org_id
    if context.jurisdiction:
        groups["jurisdiction"] = context.jurisdiction
    metadata: dict[str, Any] = {
        "user_id": context.actor_id or DEFAULT_ACTOR_ID,
        # The run id seeds it, so a DBOS replay rejoins the trace it started.
        "trace_id": context.langfuse_trace_id,
        # Prefixed: the Langfuse callback lifts `langfuse_*` headers into the
        # same metadata dict and warns on every key it has to overwrite.
        "codify_run_id": context.run_id,
        "codify_workflow_kind": context.workflow_kind,
        "codify_release": context.release,
    }
    if context.jurisdiction:
        metadata["codify_jurisdiction"] = context.jurisdiction
    if groups:
        metadata["$groups"] = groups
    if context.session_id:
        metadata["codify_ui_session"] = context.session_id
    return metadata


def attach_trace_attribution(
    langfuse_client: Any,  # noqa: ARG001, kept for API stability
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    release: str | None = None,
    tags: list[str] | None = None,
    trace_name: str | None = None,
    metadata: dict[str, str] | None = None,
) -> None:
    """Stamp documented Langfuse v4 OTel trace attributes on the current span."""
    if not any((user_id, session_id, release, tags, trace_name, metadata)):
        return
    try:
        span = otel_trace.get_current_span()
        if not span or not span.is_recording():
            return
        if trace_name is not None:
            span.set_attribute("langfuse.trace.name", trace_name)
        if user_id is not None:
            span.set_attribute("user.id", user_id)
        if session_id is not None:
            span.set_attribute("session.id", session_id)
        if release is not None:
            span.set_attribute("langfuse.release", release)
        if tags:
            # Union with whatever the run bound, because this replaces rather
            # than merges: a pipeline stage tagging `pipeline:ingest` would
            # otherwise drop the workspace the spend is billed to.
            context = get_telemetry_context()
            merged = list(tags)
            if context is not None:
                merged += [t for t in context_tags(context) if t not in merged]
            span.set_attribute("langfuse.trace.tags", json.dumps(merged))
        for key, value in (metadata or {}).items():
            span.set_attribute(f"langfuse.trace.metadata.{key}", value)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "attach_trace_attribution_failed",
            error_type=type(exc).__name__,
            user_id=user_id,
            release=release,
            tags=tags,
        )


def context_tags(context: TelemetryContext) -> list[str]:
    """The tags every trace of this run should carry, in one place. Callers deeper in
    a pipeline stamp their own and `set_attribute` replaces rather than merges, so
    building from here lets a later caller add tags but never drop the workspace
    the run is billed to.
    """
    tags = [f"workflow:{context.workflow_kind}"]
    if context.jurisdiction:
        tags.append(f"jurisdiction:{context.jurisdiction}")
    # The id, not the name: this is what stays stable when a workspace is
    # renamed. /admin/cost trades it for the name at read time.
    if context.org_id:
        tags.append(f"organisation:{context.org_id}")
    return tags


def attach_current_telemetry(langfuse_client: Any) -> None:
    """Apply the bound run fields to the current Langfuse observation."""
    context = get_telemetry_context()
    if context is None:
        return
    attach_trace_attribution(
        langfuse_client,
        user_id=context.actor_id,
        session_id=context.session_id,
        release=context.release,
        tags=context_tags(context),
        trace_name=f"codify.{context.workflow_kind}",
        metadata={
            "run_id": context.run_id,
            "workflow_kind": context.workflow_kind,
            **({"jurisdiction": context.jurisdiction} if context.jurisdiction else {}),
        },
    )


@contextmanager
def direct_observation(
    name: str,
    *,
    as_type: Literal["generation", "embedding"],
    model: str,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Record a content-free direct-provider call in Langfuse. Inputs and outputs are
    absent on purpose: these routes were untraced, and capturing payloads would
    widen the sensitive-data footprint. Callers may update usage afterwards.
    """
    from langfuse import get_client

    client = get_client()
    caught: Exception | None = None
    with client.start_as_current_observation(
        name=name,
        as_type=as_type,
        trace_context=langfuse_trace_context(),
        model=model,
        metadata=metadata,
    ) as observation:
        attach_current_telemetry(client)
        try:
            yield observation
        except Exception as exc:
            observation.update(level="ERROR", status_message=type(exc).__name__)
            # Exit Langfuse's OTel span normally. An exceptional context exit
            # records the original message and stack, which can contain a
            # provider response body even though our explicit update is safe.
            caught = exc
    if caught is not None:
        raise caught.with_traceback(caught.__traceback__)


def agent_model_settings() -> ModelSettings:
    """Attribution for a Pydantic AI agent run, as serialized model settings, which
    survive independent recovery of the nested DBOS workflow where ambient
    contextvars do not. Headers carry the Langfuse contract, the body the one
    LiteLLM reads for PostHog.
    """
    settings: ModelSettings = {"extra_headers": proxy_trace_headers()}
    metadata = proxy_call_metadata()
    if metadata:
        settings["extra_body"] = {"metadata": metadata}
    return settings


_UNPRICED: set[str] = set()


def record_usage(observation: Any, model: str, usage: Mapping[str, Any]) -> None:
    """Set usage on a direct observation, and cost where the model has a price, so a
    new direct route cannot forget the cost half. Non-integer values are dropped,
    bools included: providers mix strings and flags into the usage blob, and
    Langfuse would sum a flag into the unit total as a 1.
    """
    details = {key: value for key, value in usage.items() if type(value) is int}
    if not details:
        return
    observation.update(usage_details=details)
    costs = cost_details(model, details)
    if costs is not None:
        observation.update(cost_details=costs)
    elif model not in _UNPRICED:
        # Once per model. Unpriced is honest, but silently unpriced is the thing
        # this module exists to end: it reads as free.
        _UNPRICED.add(model)
        logger.warning("llm_model_unpriced", model=model)


# Messages that are a host, timeout, filename, key or parse offset, never a
# payload. Everything else keeps its type name only.
_SAFE_MESSAGE_TYPES: tuple[type[BaseException], ...] = (
    OSError,
    ImportError,
    KeyError,
    TimeoutError,
    httpx.TransportError,
    json.JSONDecodeError,
)


def _ocr_error_types() -> tuple[type[BaseException], ...]:
    """Imported late: `core.llm` imports this module. Not `OcrStatusError`,
    whose own fields already say more than its message does."""
    from codify.core.llm import OcrNotConfigured

    return (OcrNotConfigured,)


def error_fields(exc: BaseException) -> dict[str, Any]:
    """A provider failure as log fields, with nothing the provider echoed back.
    `str(exc)` is the hazard: provider bodies and pydantic `ValidationError` quote
    the submitted content, and `redact_processor` masks by key, so a field named
    `error` passes it.
    """
    fields: dict[str, Any] = {"error_type": type(exc).__name__}
    for attr in ("status_code", "retry_after"):
        value = getattr(exc, attr, None)
        if value is not None:
            fields[attr] = value
    if isinstance(exc, (*_SAFE_MESSAGE_TYPES, *_ocr_error_types())):
        fields["error"] = str(exc)[:300]
    return fields


def trace_url(trace_id: str | None) -> str | None:
    """Resolve a Langfuse trace ID to a public URL. None when unset / disabled."""
    if not trace_id:
        return None
    try:
        from langfuse import get_client
    except ImportError:  # pragma: no cover
        return None
    try:
        client = get_client()
        return client.get_trace_url(trace_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace_url_failed", error=str(exc), trace_id=trace_id)
        return None


__all__ = [
    "DEFAULT_ACTOR_ID",
    "DEFAULT_RELEASE",
    "TelemetryContext",
    "agent_model_settings",
    "attach_current_telemetry",
    "attach_trace_attribution",
    "bind_telemetry",
    "current_actor",
    "current_release",
    "current_session",
    "current_telemetry",
    "direct_observation",
    "error_fields",
    "get_current_actor",
    "get_current_release",
    "get_current_session",
    "get_telemetry_context",
    "langfuse_trace_context",
    "make_request_telemetry",
    "make_run_telemetry",
    "proxy_trace_headers",
    "record_usage",
    "set_current_actor",
    "set_current_session",
    "trace_url",
]
