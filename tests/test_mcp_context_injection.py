from __future__ import annotations

from pathlib import Path

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse
from perlica.mcp.types import MCPToolSpec


class _CaptureProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        return LLMResponse(assistant_text="ok", tool_calls=[], finish_reason="stop")


def test_runner_injects_mcp_context_and_emits_counts(monkeypatch, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        provider = _CaptureProvider()
        runtime.register_provider(provider)

        monkeypatch.setattr(
            runtime,
            "mcp_prompt_context_blocks",
            lambda: ["MCP Resources:\n- [demo] resource://x\nhello"],
        )
        monkeypatch.setattr(
            runtime.mcp_manager,
            "list_tool_specs",
            lambda: [MCPToolSpec(server_id="demo", tool_name="echo")],
        )
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        runner.run_text("test mcp", assume_yes=True, session_ref=session.session_id)

        req = provider.requests[0]
        contents = [str(item.get("content") or "") for item in req.messages]
        assert any("MCP Resources" in item for item in contents)
        assert "macOS control agent" in contents[0]

        events = runtime.event_log.list_events(limit=200)
        llm_requested = [item for item in events if item.event_type == "llm.requested"]
        assert llm_requested
        payload = llm_requested[-1].payload
        assert int(payload.get("mcp_tools_count") or 0) >= 1
        assert int(payload.get("mcp_context_blocks_count") or 0) >= 1
    finally:
        runtime.close()
