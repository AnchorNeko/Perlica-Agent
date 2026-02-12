"""Runtime registry for providers, tools, middleware, and plugin commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from perlica.kernel.types import LLMProvider, Middleware, Tool

PluginCommand = Callable[[List[str], Any], int]


@dataclass
class Registry:
    providers: Dict[str, LLMProvider] = field(default_factory=dict)
    tools: Dict[str, Tool] = field(default_factory=dict)
    middlewares: Dict[str, Middleware] = field(default_factory=dict)
    plugin_commands: Dict[str, PluginCommand] = field(default_factory=dict)

    def register_provider(self, provider: LLMProvider) -> None:
        self.providers[provider.provider_id] = provider

    def register_tool(self, tool: Tool) -> None:
        self.tools[tool.tool_name] = tool

    def register_middleware(self, middleware: Middleware) -> None:
        self.middlewares[middleware.middleware_id] = middleware

    def register_plugin_command(self, plugin_id: str, handler: PluginCommand) -> None:
        self.plugin_commands[plugin_id] = handler

    def get_provider(self, provider_id: str) -> Optional[LLMProvider]:
        return self.providers.get(provider_id)

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        return self.tools.get(tool_name)

    def list_provider_ids(self) -> List[str]:
        return sorted(self.providers.keys())

    def list_tool_ids(self) -> List[str]:
        return sorted(self.tools.keys())
