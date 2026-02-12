from __future__ import annotations

from pathlib import Path

from perlica.kernel.eventlog import EventLog


def test_eventlog_append_and_replay(tmp_path: Path):
    log = EventLog(tmp_path / "eventlog.db", context_id="ctx")

    event_a = log.append("inbound.message.received", {"text": "hello"}, conversation_id="conv")
    event_b = log.append("llm.requested", {"provider": "mock"}, conversation_id="conv")
    event_c = log.append("llm.responded", {"text": "hi"}, conversation_id="conv")

    replay = log.list_by_conversation("conv")
    assert [item.event_type for item in replay] == [
        "inbound.message.received",
        "llm.requested",
        "llm.responded",
    ]
    assert replay[0].event_id == event_a.event_id
    assert replay[1].event_id == event_b.event_id
    assert replay[2].event_id == event_c.event_id


def test_eventlog_idempotency_key_deduplicates(tmp_path: Path):
    log = EventLog(tmp_path / "eventlog.db", context_id="ctx")

    first = log.append(
        "inbound.message.received",
        {"text": "hello"},
        conversation_id="conv",
        idempotency_key="msg-1",
    )
    second = log.append(
        "inbound.message.received",
        {"text": "hello-again"},
        conversation_id="conv",
        idempotency_key="msg-1",
    )

    assert first.event_id == second.event_id
    all_events = log.list_events()
    assert len(all_events) == 1
    assert all_events[0].payload["text"] == "hello"
