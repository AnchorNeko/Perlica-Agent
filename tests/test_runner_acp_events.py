from __future__ import annotations

from pathlib import Path

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse


class _FakeACPClient:
    def __init__(self, *, provider_id, config, event_sink=None) -> None:
        self.provider_id = provider_id
        self.config = config
        self.event_sink = event_sink

    def generate(self, req):
        if callable(self.event_sink):
            self.event_sink("provider.acp.session.started", {"provider_id": self.provider_id, "session_id": "s1"})
            self.event_sink("provider.acp.request.sent", {"provider_id": self.provider_id, "method": "session/prompt"})
            self.event_sink("provider.acp.session.closed", {"provider_id": self.provider_id, "session_id": "s1"})
        return LLMResponse(assistant_text="acp ok", tool_calls=[], finish_reason="stop")


def _runtime(tmp_path: Path) -> Runtime:
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="test", provider="claude", workspace_dir=tmp_path)
    return Runtime(settings)


def test_runner_emits_acp_events(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("perlica.providers.acp_provider.ACPClient", _FakeACPClient)

    runtime = _runtime(tmp_path)
    try:
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="claude",
        )
        runner = Runner(runtime=runtime, provider_id="claude", max_tool_calls=2)
        result = runner.run_text("hello", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "acp ok"

        events = runtime.event_log.list_events(limit=200)
        event_types = [evt.event_type for evt in events]
        assert "provider.acp.session.started" in event_types
        assert "provider.acp.session.closed" in event_types
    finally:
        runtime.close()
