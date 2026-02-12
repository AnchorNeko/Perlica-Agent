"""Minimal MCP stdio JSON-RPC client."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional

from perlica.mcp.types import MCPPrompt, MCPResource, MCPServerConfig, MCPToolSpec


class MCPClientError(RuntimeError):
    """Raised for MCP transport/protocol failures."""


class StdioMCPClient:
    """Synchronous stdio MCP client with Content-Length framing."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._next_id = 1
        self._lock = threading.Lock()

    @property
    def server_id(self) -> str:
        return self._config.server_id

    def start(self) -> None:
        if self._proc is not None:
            return

        env = {**os.environ, **self._config.env}
        command = [self._config.command] + list(self._config.args)
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except Exception as exc:
            raise MCPClientError(
                "failed to start MCP server '{0}': {1}".format(self._config.server_id, exc)
            ) from exc

        self._initialize()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def list_tools(self) -> List[MCPToolSpec]:
        payload = self.request("tools/list", {})
        tools = payload.get("tools")
        if not isinstance(tools, list):
            return []
        result: List[MCPToolSpec] = []
        for row in tools:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            result.append(
                MCPToolSpec(
                    server_id=self.server_id,
                    tool_name=name,
                    description=str(row.get("description") or ""),
                    input_schema=dict(row.get("inputSchema") or {}),
                )
            )
        return result

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.request(
            "tools/call",
            {"name": tool_name, "arguments": dict(arguments or {})},
        )
        return payload if isinstance(payload, dict) else {}

    def list_resources(self) -> List[MCPResource]:
        payload = self.request("resources/list", {})
        rows = payload.get("resources")
        if not isinstance(rows, list):
            return []
        result: List[MCPResource] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            uri = str(row.get("uri") or "").strip()
            if not uri:
                continue
            text = self._read_resource_text(uri)
            result.append(
                MCPResource(
                    server_id=self.server_id,
                    uri=uri,
                    name=str(row.get("name") or ""),
                    description=str(row.get("description") or ""),
                    content=text,
                )
            )
        return result

    def list_prompts(self) -> List[MCPPrompt]:
        payload = self.request("prompts/list", {})
        rows = payload.get("prompts")
        if not isinstance(rows, list):
            return []
        result: List[MCPPrompt] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            content = self._get_prompt_text(name)
            result.append(
                MCPPrompt(
                    server_id=self.server_id,
                    name=name,
                    description=str(row.get("description") or ""),
                    content=content,
                )
            )
        return result

    def request(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1

            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )

            while True:
                message = self._read()
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    raise MCPClientError(
                        "mcp server '{0}' {1} failed: {2}".format(
                            self.server_id,
                            method,
                            message.get("error"),
                        )
                    )
                result = message.get("result")
                if isinstance(result, dict):
                    return result
                return {}

    def notify(self, method: str, params: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params or {},
                }
            )

    def _initialize(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "perlica", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        self.notify("notifications/initialized", {})

    def _send(self, payload: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPClientError("mcp client not started")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = "Content-Length: {0}\r\n\r\n".format(len(data)).encode("ascii")
        try:
            proc.stdin.write(header + data)
            proc.stdin.flush()
        except Exception as exc:
            raise MCPClientError("failed to write to mcp server") from exc

    def _read(self) -> Dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise MCPClientError("mcp client not started")

        headers: Dict[str, str] = {}
        while True:
            line = proc.stdout.readline()
            if not line:
                raise MCPClientError("mcp server closed stdout")
            if line in {b"\r\n", b"\n"}:
                break
            try:
                text = line.decode("utf-8").strip()
            except Exception:
                continue
            if ":" not in text:
                continue
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

        length_raw = headers.get("content-length")
        if not length_raw:
            raise MCPClientError("missing Content-Length header")
        try:
            length = int(length_raw)
        except ValueError as exc:
            raise MCPClientError("invalid Content-Length header") from exc

        body = proc.stdout.read(length)
        if not body:
            raise MCPClientError("mcp server returned empty body")
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise MCPClientError("invalid JSON payload from mcp server") from exc
        if not isinstance(payload, dict):
            raise MCPClientError("mcp payload must be an object")
        return payload

    def _read_resource_text(self, uri: str) -> str:
        try:
            result = self.request("resources/read", {"uri": uri})
        except Exception:
            return ""
        return _extract_text_content(result.get("contents"))

    def _get_prompt_text(self, name: str) -> str:
        try:
            result = self.request("prompts/get", {"name": name, "arguments": {}})
        except Exception:
            return ""
        return _extract_text_content(result.get("messages"))


def _extract_text_content(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    parts: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            parts.append(str(item.get("text")))
            continue
        content = item.get("content")
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            parts.append(str(content.get("text")))
            continue
        if isinstance(content, list):
            for row in content:
                if isinstance(row, dict) and isinstance(row.get("text"), str):
                    parts.append(str(row.get("text")))
    return "\n".join(part for part in parts if part).strip()
