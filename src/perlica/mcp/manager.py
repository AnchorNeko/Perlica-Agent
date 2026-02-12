"""MCP manager: config loading, server lifecycle, and tool/context access."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from perlica.mcp.config import load_mcp_server_configs
from perlica.mcp.registry import MCPRegistry
from perlica.mcp.stdio_client import MCPClientError, StdioMCPClient
from perlica.mcp.types import MCPReloadReport, MCPServerState


class MCPManager:
    """Loads MCP servers and exposes tools/resources/prompts to runtime."""

    def __init__(self, config_file: Path) -> None:
        self._config_file = Path(config_file)
        self._clients: Dict[str, StdioMCPClient] = {}
        self._registry = MCPRegistry()
        self._report = MCPReloadReport()
        self._config_errors: List[str] = []

    @property
    def config_file(self) -> Path:
        return self._config_file

    def load(self) -> MCPReloadReport:
        self.close()
        configs, parse_errors = load_mcp_server_configs(self._config_file)
        self._config_errors = list(parse_errors)

        report = MCPReloadReport()
        for config in configs:
            state = MCPServerState(config=config)
            if not config.enabled:
                report.states[config.server_id] = state
                continue

            client = StdioMCPClient(config)
            try:
                client.start()
                state.tools = client.list_tools()
                state.resources = client.list_resources()
                state.prompts = client.list_prompts()
                self._clients[config.server_id] = client
            except Exception as exc:
                state.error = str(exc)
                try:
                    client.close()
                except Exception:
                    pass
            report.states[config.server_id] = state

        self._registry.ingest_states(report.states.items())
        self._report = report
        return report

    def reload(self) -> MCPReloadReport:
        return self.load()

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.close()
            except Exception:
                pass
        self._clients = {}
        self._registry.reset()

    def list_tool_specs(self):
        return self._registry.list_tools()

    def get_tool_spec(self, qualified_name: str):
        return self._registry.get_tool(qualified_name)

    def call_tool(self, qualified_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        spec = self._registry.get_tool(qualified_name)
        if spec is None:
            raise MCPClientError("unknown mcp tool: {0}".format(qualified_name))
        client = self._clients.get(spec.server_id)
        if client is None:
            raise MCPClientError("mcp server not loaded: {0}".format(spec.server_id))
        return client.call_tool(spec.tool_name, arguments)

    def build_prompt_context_blocks(self) -> List[str]:
        blocks: List[str] = []

        resources = self._registry.list_resources()
        if resources:
            parts: List[str] = ["MCP Resources:"]
            for resource in resources[:16]:
                header = "- [{0}] {1}".format(resource.server_id, resource.uri)
                if resource.name:
                    header = "{0} ({1})".format(header, resource.name)
                if resource.content:
                    parts.append("{0}\n{1}".format(header, resource.content[:2000]))
                else:
                    parts.append(header)
            blocks.append("\n".join(parts))

        prompts = self._registry.list_prompts()
        if prompts:
            parts = ["MCP Prompts:"]
            for prompt in prompts[:16]:
                header = "- [{0}] {1}".format(prompt.server_id, prompt.name)
                if prompt.description:
                    header = "{0}: {1}".format(header, prompt.description)
                if prompt.content:
                    parts.append("{0}\n{1}".format(header, prompt.content[:2000]))
                else:
                    parts.append(header)
            blocks.append("\n".join(parts))

        return blocks

    def status(self) -> Dict[str, Any]:
        return {
            "config_file": str(self._config_file),
            "loaded_servers": self._report.loaded_servers,
            "failed_servers": self._report.failed_servers,
            "tool_count": self._report.tool_count,
            "errors": {
                **self._report.errors,
                **{"config[{0}]".format(idx): err for idx, err in enumerate(self._config_errors)},
            },
            "servers": self._server_summaries(),
        }

    def adapter_mcp_servers_payload(self) -> Dict[str, Dict[str, Any]]:
        """Return MCP server definitions for ACP adapters that require them."""

        payload: Dict[str, Dict[str, Any]] = {}
        for server_id, state in sorted(self._report.states.items()):
            if not state.config.enabled:
                continue
            payload[server_id] = {
                "command": str(state.config.command),
                "args": list(state.config.args),
                "env": dict(state.config.env),
            }
        return payload

    def _server_summaries(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for server_id, state in sorted(self._report.states.items()):
            rows.append(
                {
                    "server_id": server_id,
                    "enabled": bool(state.config.enabled),
                    "loaded": state.loaded,
                    "tool_count": len(state.tools),
                    "resource_count": len(state.resources),
                    "prompt_count": len(state.prompts),
                    "error": state.error,
                }
            )
        return rows
