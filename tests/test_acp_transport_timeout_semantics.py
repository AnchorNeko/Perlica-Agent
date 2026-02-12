from __future__ import annotations

import json
import threading
import time

import pytest

from perlica.providers.acp_transport import ACPTransportTimeout, StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig


def test_prompt_request_waits_until_final_response(monkeypatch):
    transport = StdioACPTransport(config=ACPClientConfig(command="python3"))

    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_payload", lambda payload: None)

    def _emit_notifications() -> None:
        for _ in range(8):
            transport._stdout_queue.put(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "perlica/session_progress",
                        "params": {"stage": "session/prompt", "elapsed_ms": 1},
                    },
                    ensure_ascii=True,
                )
            )
            time.sleep(0.1)
        transport._stdout_queue.put(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "req-prompt",
                    "result": {"assistant_text": "ok"},
                },
                ensure_ascii=True,
            )
        )

    feeder = threading.Thread(target=_emit_notifications, daemon=True)
    feeder.start()

    response = transport.request(
        {"id": "req-prompt", "method": "session/prompt"},
        timeout_sec=1,
    )
    feeder.join(timeout=5)
    assert response.get("id") == "req-prompt"
    assert response.get("result", {}).get("assistant_text") == "ok"


def test_non_prompt_request_keeps_hard_timeout(monkeypatch):
    transport = StdioACPTransport(config=ACPClientConfig(command="python3"))
    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_payload", lambda payload: None)

    with pytest.raises(ACPTransportTimeout):
        transport.request({"id": "req-hard-timeout", "method": "initialize"}, timeout_sec=1)
