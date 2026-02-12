"""In-memory MCP object registry."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from perlica.mcp.types import MCPPrompt, MCPResource, MCPServerState, MCPToolSpec


class MCPRegistry:
    """Stores loaded MCP server objects for lookup and prompt injection."""

    def __init__(self) -> None:
        self._tool_specs: Dict[str, MCPToolSpec] = {}
        self._resources: List[MCPResource] = []
        self._prompts: List[MCPPrompt] = []
        self._server_errors: Dict[str, str] = {}

    def reset(self) -> None:
        self._tool_specs.clear()
        self._resources = []
        self._prompts = []
        self._server_errors.clear()

    def ingest_states(self, states: Iterable[Tuple[str, MCPServerState]]) -> None:
        self.reset()
        for server_id, state in states:
            if state.error:
                self._server_errors[server_id] = state.error
                continue
            for tool in state.tools:
                self._tool_specs[tool.qualified_name] = tool
            self._resources.extend(state.resources)
            self._prompts.extend(state.prompts)

    def get_tool(self, qualified_name: str) -> Optional[MCPToolSpec]:
        return self._tool_specs.get(qualified_name)

    def list_tools(self) -> List[MCPToolSpec]:
        return [self._tool_specs[name] for name in sorted(self._tool_specs.keys())]

    def list_resources(self) -> List[MCPResource]:
        return list(self._resources)

    def list_prompts(self) -> List[MCPPrompt]:
        return list(self._prompts)

    def server_errors(self) -> Dict[str, str]:
        return dict(self._server_errors)
