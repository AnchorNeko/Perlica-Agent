from __future__ import annotations

from pathlib import Path

from perlica.mcp.config import load_mcp_server_configs
from perlica.mcp.manager import MCPManager
from perlica.mcp.types import MCPPrompt, MCPResource, MCPToolSpec


def test_mcp_config_parser_handles_valid_and_invalid_rows(tmp_path: Path):
    config_file = tmp_path / "servers.toml"
    config_file.write_text(
        """
[[servers]]
id = "good"
command = "echo"
args = ["ok"]
enabled = true

[[servers]]
id = ""
command = "missing-id"
enabled = true
""".strip(),
        encoding="utf-8",
    )

    configs, errors = load_mcp_server_configs(config_file)
    assert len(configs) == 1
    assert configs[0].server_id == "good"
    assert errors


def test_mcp_manager_loads_enabled_servers(monkeypatch, tmp_path: Path):
    config_file = tmp_path / "servers.toml"
    config_file.write_text(
        """
[[servers]]
id = "demo"
command = "demo"
enabled = true

[[servers]]
id = "disabled"
command = "demo"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    class FakeClient:
        def __init__(self, config):
            self.server_id = config.server_id

        def start(self):
            return None

        def close(self):
            return None

        def list_tools(self):
            return [MCPToolSpec(server_id=self.server_id, tool_name="echo", description="Echo")]

        def list_resources(self):
            return [
                MCPResource(
                    server_id=self.server_id,
                    uri="resource://demo",
                    name="demo",
                    content="resource text",
                )
            ]

        def list_prompts(self):
            return [MCPPrompt(server_id=self.server_id, name="default", content="prompt text")]

    monkeypatch.setattr("perlica.mcp.manager.StdioMCPClient", FakeClient)
    manager = MCPManager(config_file)
    report = manager.load()
    try:
        assert report.loaded_servers == 1
        assert report.tool_count == 1
        status = manager.status()
        assert status["loaded_servers"] == 1
        assert status["tool_count"] == 1
        assert manager.list_tool_specs()[0].qualified_name == "mcp.demo.echo"
    finally:
        manager.close()
