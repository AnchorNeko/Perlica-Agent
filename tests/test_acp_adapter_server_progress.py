from __future__ import annotations

import time

from perlica.kernel.types import LLMResponse
from perlica.providers.acp_adapter_server import ACPAdapterServer


class _SlowProvider:
    def generate(self, req):
        del req
        time.sleep(0.15)
        return LLMResponse(assistant_text="ok", tool_calls=[], finish_reason="stop")


def test_adapter_emits_progress_notifications_during_prompt():
    notifications = []
    server = ACPAdapterServer(notify=lambda payload: notifications.append(payload), prompt_heartbeat_sec=0.05)
    server._providers["claude"] = _SlowProvider()

    init_resp = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "r1",
            "method": "initialize",
            "params": {"provider_id": "claude"},
        }
    )
    assert init_resp.get("result", {}).get("provider_id") == "claude"

    new_resp = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "r2",
            "method": "session/new",
            "params": {"provider_id": "claude"},
        }
    )
    session_id = new_resp.get("result", {}).get("session_id")
    assert isinstance(session_id, str) and session_id

    prompt_resp = server.handle(
        {
            "jsonrpc": "2.0",
            "id": "r3",
            "method": "session/prompt",
            "params": {
                "provider_id": "claude",
                "session_id": session_id,
                "conversation_id": "conv",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [],
                "context": {},
            },
        }
    )
    assert prompt_resp.get("result", {}).get("assistant_text") == "ok"
    progress = [item for item in notifications if item.get("method") == "perlica/session_progress"]
    # Adapter prompt path is synchronous to avoid subprocess pipe deadlocks.
    assert progress == []
