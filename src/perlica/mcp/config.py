"""MCP server configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

try:  # pragma: no cover - Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    import tomli as tomllib  # type: ignore[no-redef]

from perlica.mcp.types import MCPServerConfig


def load_mcp_server_configs(path: Path) -> Tuple[List[MCPServerConfig], List[str]]:
    if not path.exists():
        return [], ["missing mcp config: {0}".format(path)]

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], ["invalid mcp config: {0}".format(exc)]

    if not isinstance(raw, dict):
        return [], ["invalid mcp config root"]

    rows = raw.get("servers")
    if rows is None:
        return [], []
    if not isinstance(rows, list):
        return [], ["invalid mcp config: servers must be an array"]

    configs: List[MCPServerConfig] = []
    errors: List[str] = []
    seen_ids: Dict[str, bool] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append("servers[{0}] must be a table".format(index))
            continue

        server_id = str(row.get("id") or "").strip()
        command = str(row.get("command") or "").strip()
        args = row.get("args")
        env = row.get("env")
        enabled = bool(row.get("enabled", True))

        if not server_id:
            errors.append("servers[{0}] missing id".format(index))
            continue
        if not command:
            errors.append("servers[{0}] missing command".format(index))
            continue
        if server_id in seen_ids:
            errors.append("duplicate mcp server id: {0}".format(server_id))
            continue

        parsed_args: List[str] = []
        if isinstance(args, list):
            parsed_args = [str(item) for item in args]
        elif args is not None:
            errors.append("servers[{0}] args must be an array".format(index))
            continue

        parsed_env: Dict[str, str] = {}
        if isinstance(env, dict):
            parsed_env = {str(key): str(value) for key, value in env.items()}
        elif env is not None:
            errors.append("servers[{0}] env must be a table".format(index))
            continue

        seen_ids[server_id] = True
        configs.append(
            MCPServerConfig(
                server_id=server_id,
                command=command,
                args=parsed_args,
                env=parsed_env,
                enabled=enabled,
            )
        )

    return configs, errors
