from __future__ import annotations

from pathlib import Path

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import ToolCall
from perlica.tools.mcp_tool import MCPTool


class _FakeMCPManager:
    def __init__(self) -> None:
        self.calls = []

    def call_tool(self, qualified_name, arguments):
        self.calls.append((qualified_name, dict(arguments)))
        return {"ok": True, "echo": arguments}

    def close(self):
        return None


def test_mcp_tool_executes_via_dispatcher(tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        runtime.mcp_manager = _FakeMCPManager()  # type: ignore[assignment]
        runtime.register_tool(MCPTool("mcp.demo.echo", description="Echo"))

        call = ToolCall(
            call_id="call-1",
            tool_name="mcp.demo.echo",
            arguments={"text": "hello"},
            risk_tier="low",
        )
        dispatched = runtime.dispatcher.dispatch(
            call=call,
            runtime=runtime,
            assume_yes=True,
        )

        assert dispatched.blocked is False
        assert dispatched.result.ok is True
        assert dispatched.result.output["result"]["ok"] is True
    finally:
        runtime.close()
