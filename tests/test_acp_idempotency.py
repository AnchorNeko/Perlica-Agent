from __future__ import annotations

import json

from perlica.providers.acp_transport import StdioACPTransport
from perlica.providers.acp_types import ACPClientConfig


def test_transport_drops_duplicate_response_ids(monkeypatch):
    transport = StdioACPTransport(config=ACPClientConfig(command="python3"))

    monkeypatch.setattr(transport, "start", lambda: None)
    monkeypatch.setattr(transport, "_write_payload", lambda payload: None)

    first = {"jsonrpc": "2.0", "id": "req-1", "result": {"ok": 1}}
    duplicate_old = {"jsonrpc": "2.0", "id": "req-1", "result": {"ok": 1}}
    second = {"jsonrpc": "2.0", "id": "req-2", "result": {"ok": 2}}

    transport._stdout_queue.put(json.dumps(first, ensure_ascii=True))
    response_1 = transport.request({"id": "req-1"}, timeout_sec=1)
    assert response_1.get("result", {}).get("ok") == 1

    transport._stdout_queue.put(json.dumps(duplicate_old, ensure_ascii=True))
    transport._stdout_queue.put(json.dumps(second, ensure_ascii=True))

    response_2 = transport.request({"id": "req-2"}, timeout_sec=1)
    assert response_2.get("result", {}).get("ok") == 2
