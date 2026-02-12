"""Typed models for MCP configuration and runtime state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MCPServerConfig:
    server_id: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class MCPToolSpec:
    server_id: str
    tool_name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return "mcp.{0}.{1}".format(self.server_id, self.tool_name)


@dataclass(frozen=True)
class MCPResource:
    server_id: str
    uri: str
    name: str = ""
    description: str = ""
    content: str = ""


@dataclass(frozen=True)
class MCPPrompt:
    server_id: str
    name: str
    description: str = ""
    content: str = ""


@dataclass
class MCPServerState:
    config: MCPServerConfig
    tools: List[MCPToolSpec] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)
    prompts: List[MCPPrompt] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return bool(self.config.enabled and self.error is None)


@dataclass
class MCPReloadReport:
    states: Dict[str, MCPServerState] = field(default_factory=dict)

    @property
    def loaded_servers(self) -> int:
        return sum(1 for state in self.states.values() if state.loaded)

    @property
    def failed_servers(self) -> int:
        return sum(1 for state in self.states.values() if not state.loaded)

    @property
    def tool_count(self) -> int:
        return sum(len(state.tools) for state in self.states.values() if state.loaded)

    @property
    def errors(self) -> Dict[str, str]:
        return {
            server_id: str(state.error)
            for server_id, state in self.states.items()
            if state.error
        }
