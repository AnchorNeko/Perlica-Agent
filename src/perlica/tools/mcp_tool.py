"""MCP tool bridge for Dispatcher execution path."""

from __future__ import annotations

from typing import Any, Dict, Optional

from perlica.kernel.dispatcher import DISPATCH_ACTIVE
from perlica.kernel.types import ToolCall, ToolResult


class MCPTool:
    """One registered runtime tool mapped to a concrete MCP server tool."""

    def __init__(
        self,
        tool_name: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.tool_name = tool_name
        self.description = description
        self.input_schema = dict(input_schema or {})

    def execute(self, call: ToolCall, runtime: object) -> ToolResult:
        if not DISPATCH_ACTIVE.get():
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error="direct_execution_forbidden",
                output={},
            )

        manager = getattr(runtime, "mcp_manager", None)
        if manager is None:
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error="mcp_manager_unavailable",
                output={},
            )

        try:
            result = manager.call_tool(self.tool_name, dict(call.arguments or {}))
        except Exception as exc:
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error="mcp_call_failed",
                output={"error": str(exc)},
            )

        return ToolResult(
            call_id=call.call_id,
            ok=True,
            output={"result": result},
            error=None,
        )
