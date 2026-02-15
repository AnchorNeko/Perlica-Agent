from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List

from perlica.providers.acp_transport import StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig


def test_transport_handles_side_request_and_main_response(monkeypatch):
    events: List[str] = []
    written: List[Dict[str, Any]] = []

    transport = StdioACPTransport(
        config=ACPClientConfig(command="python3"),
        event_sink=lambda event_type, payload: events.append(event_type),
    )

    monkeypatch.setattr(transport, "start", lambda: None)

    def _write_payload(payload: Dict[str, Any]) -> None:
        written.append(dict(payload))

    monkeypatch.setattr(transport, "_write_payload", _write_payload)

    def _feed() -> None:
        time.sleep(0.05)
        transport._stdout_queue.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/request_permission",
                    "params": {"interaction_id": "int_1", "question": "Q"},
                },
                ensure_ascii=True,
            )
        )
        time.sleep(0.05)
        transport._stdout_queue.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "side-1",
                    "result": {"ok": True},
                },
                ensure_ascii=True,
            )
        )
        time.sleep(0.05)
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

    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()

    side_responses: List[Dict[str, Any]] = []

    def _notification_handler(notification: Dict[str, Any]):
        assert notification.get("method") == "session/request_permission"
        return {
            "jsonrpc": "2.0",
            "id": "side-1",
            "method": "session/reply",
            "params": {"interaction_id": "int_1", "text": "ok"},
        }

    response = transport.request(
        {"jsonrpc": "2.0", "id": "req-1", "method": "session/prompt", "params": {}},
        timeout_sec=1,
        notification_handler=_notification_handler,
        side_response_sink=lambda payload: side_responses.append(dict(payload)),
    )
    feeder.join(timeout=2)

    assert response.get("id") == "req-1"
    assert any(item.get("method") == "session/reply" for item in written)
    assert len(side_responses) == 1
    assert "provider.acp.notification.received" in events
