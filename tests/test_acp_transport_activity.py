from __future__ import annotations

import json
import threading
import time

import pytest

from perlica.providers.acp_transport import ACPTransportTimeout, StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig


def test_transport_prompt_notifications_are_emitted_without_local_timeout(monkeypatch):
    events = []
    transport = StdioACPTransport(
        config=ACPClientConfig(command="python3"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )

    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_payload", lambda payload: None)

    def _feed_lines() -> None:
        time.sleep(0.2)
        for _ in range(5):
            transport._stdout_queue.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "perlica/session_progress",
                        "params": {"stage": "session/prompt"},
                    },
                    ensure_ascii=True,
                )
            )
            time.sleep(0.2)
        transport._stdout_queue.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req-1",
                    "result": {"assistant_text": "done"},
                },
                ensure_ascii=True,
            )
        )

    feeder = threading.Thread(target=_feed_lines, daemon=True)
    feeder.start()

    response = transport.request(
        {"id": "req-1", "method": "session/prompt"},
        timeout_sec=1,
    )
    feeder.join(timeout=5)
    assert response.get("id") == "req-1"

    notification_events = [payload for name, payload in events if name == "provider.acp.notification.received"]
    assert notification_events
    sample = notification_events[-1]
    assert sample.get("method") == "perlica/session_progress"
    params = sample.get("params")
    assert isinstance(params, dict)
    assert params.get("stage") == "session/prompt"


def test_transport_non_prompt_request_still_times_out(monkeypatch):
    transport = StdioACPTransport(config=ACPClientConfig(command="python3"))
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_payload", lambda payload: None)

    with pytest.raises(ACPTransportTimeout):
        transport.request({"id": "req-init", "method": "initialize"}, timeout_sec=1)
