from __future__ import annotations

import json

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runtime import Runtime
from perlica.service.orchestrator import ServiceOrchestrator
from perlica.service.store import ServiceStore


class _NoopChannel:
    channel_name = "imessage"


def _read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_runtime_events_are_written_to_debug_log(tmp_path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="debug", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        runtime.emit(
            "inbound.message.received",
            {"text": "hello", "token": "abc"},
            conversation_id="session.demo",
            run_id="run-1",
            trace_id="trace-1",
        )
        runtime.log_diagnostic(
            level="warn",
            component="runner",
            kind="diagnostic",
            message="authorization=Bearer super-secret token=abc",
            data={"authorization": "Bearer hidden"},
            conversation_id="session.demo",
            run_id="run-1",
            trace_id="trace-1",
        )
    finally:
        runtime.close()

    rows = _read_jsonl(tmp_path / ".perlica_config" / "contexts" / "debug" / "logs" / "debug.log.jsonl")
    event_rows = [row for row in rows if row.get("event_type") == "inbound.message.received"]
    assert event_rows
    assert event_rows[-1]["kind"] == "event"
    assert event_rows[-1]["run_id"] == "run-1"
    assert event_rows[-1]["trace_id"] == "trace-1"
    assert event_rows[-1]["context_id"] == "debug"

    diagnostic_rows = [row for row in rows if row.get("component") == "runner"]
    assert diagnostic_rows
    assert "***REDACTED***" in diagnostic_rows[-1]["message"]
    assert diagnostic_rows[-1]["data"]["authorization"] == "***REDACTED***"


def test_service_emit_writes_service_event_logs_even_without_event_sink(tmp_path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="debug-service", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    store = ServiceStore(tmp_path / ".perlica_config" / "service" / "service_bridge.db")
    orchestrator = ServiceOrchestrator(
        runtime=runtime,
        store=store,
        channel=_NoopChannel(),
        provider_id=None,
        yes=True,
    )
    try:
        orchestrator._emit(
            "system",
            "service diagnostic emitted",
            contact_id="user@example.com",
            chat_id="chat-1",
            meta={"event_type": "service.test"},
        )
    finally:
        store.close()
        runtime.close()

    rows = _read_jsonl(
        tmp_path / ".perlica_config" / "contexts" / "debug-service" / "logs" / "debug.log.jsonl"
    )
    service_rows = [row for row in rows if row.get("kind") == "service_event"]
    assert service_rows
    target = service_rows[-1]
    assert target["component"] == "service"
    assert target["event_type"] == "service.test"
    assert target["message"] == "service diagnostic emitted"
    assert target["data"]["contact_id"] == "user@example.com"
