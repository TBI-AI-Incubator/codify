"""The gateway metadata contract.

Keys here are read by LiteLLM and forwarded to its callbacks, so this is a wire
contract rather than an internal shape: `user_id` becomes the person, `trace_id`
groups a run's calls, and anything unrecognised passes through as a property.
"""

from __future__ import annotations

from codify.core.tracing import (
    TelemetryContext,
    agent_model_settings,
    bind_telemetry,
    proxy_call_metadata,
)


def _context(**over: object) -> TelemetryContext:
    fields: dict[str, object] = {
        "run_id": "run-1",
        "workflow_kind": "ingest",
        "langfuse_trace_id": "trace-abc",
        "jurisdiction": "ua",
        "actor_id": "alice@example.com",
        "org_id": "org-1",
        "session_id": "sess-1",
    }
    fields.update(over)
    return TelemetryContext(**fields)  # type: ignore[arg-type]


def test_an_unbound_call_carries_only_a_refusal_to_make_a_person() -> None:
    """Without a `user_id`, LiteLLM falls back to the trace id for `distinct_id`
    and PostHog mints a person per call: 759 events became 470 "people". The
    flag keeps the event and its cost and drops only the phantom."""
    assert proxy_call_metadata() == {"$process_person_profile": False}


def test_identity_and_trace_reach_the_callback() -> None:
    with bind_telemetry(_context()):
        meta = proxy_call_metadata()
    assert meta["user_id"] == "alice@example.com"
    # Seeded from the run id, so a DBOS replay rejoins the trace it started.
    assert meta["trace_id"] == "trace-abc"
    assert meta["$groups"] == {"organisation": "org-1", "jurisdiction": "ua"}
    assert meta["codify_run_id"] == "run-1"
    assert meta["codify_workflow_kind"] == "ingest"


def test_our_session_never_occupies_litellms_session_key() -> None:
    """LiteLLM resolves the trace id from `metadata.session_id` BEFORE
    `metadata.trace_id`, so ours living there would silently become the trace
    and scatter every run's calls into separate traces."""
    with bind_telemetry(_context()):
        meta = proxy_call_metadata()
    assert "session_id" not in meta
    assert meta["codify_ui_session"] == "sess-1"


def test_a_run_with_no_workspace_sends_no_empty_group() -> None:
    with bind_telemetry(_context(org_id=None, jurisdiction=None)):
        meta = proxy_call_metadata()
    assert "$groups" not in meta
    assert "codify_jurisdiction" not in meta


def test_body_keys_do_not_collide_with_the_header_contract() -> None:
    """The Langfuse callback lifts `langfuse_*` headers into the same metadata
    dict and warns on every key it overwrites, once per model call."""
    from codify.core.tracing import proxy_trace_headers

    with bind_telemetry(_context()):
        body = set(proxy_call_metadata())
        headers = {
            k[len("langfuse_") :] for k in proxy_trace_headers() if k.startswith("langfuse_")
        }
    assert not (body & headers), body & headers


def test_agent_settings_carry_both_halves_of_the_contract() -> None:
    """Langfuse reads the headers, PostHog reads the body. An agent that sends
    only headers is attributed in one system and invisible in the other."""
    with bind_telemetry(_context()):
        settings = agent_model_settings()
        expected = proxy_call_metadata()
    assert settings["extra_headers"]["langfuse_existing_trace_id"] == "trace-abc"
    assert settings["extra_body"] == {"metadata": expected}
    assert expected["codify_run_id"] == "run-1"


def test_an_unbound_agent_call_still_refuses_to_make_a_person() -> None:
    settings = agent_model_settings()
    assert not any(k.startswith("langfuse_") for k in settings["extra_headers"])
    assert settings["extra_body"] == {"metadata": {"$process_person_profile": False}}


def test_a_bound_call_never_carries_the_refusal() -> None:
    with bind_telemetry(_context()):
        assert "$process_person_profile" not in proxy_call_metadata()
