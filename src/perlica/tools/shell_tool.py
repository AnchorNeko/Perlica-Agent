"""Shell execution tool."""

from __future__ import annotations

import os
import subprocess
from typing import Dict

from perlica.kernel.dispatcher import DISPATCH_ACTIVE
from perlica.kernel.types import ToolCall, ToolResult


SAFE_ENV_KEYS = ["PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM", "TMPDIR"]


class ShellTool:
    tool_name = "shell.exec"

    def execute(self, call: ToolCall, runtime: object) -> ToolResult:
        if not DISPATCH_ACTIVE.get():
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error="direct_execution_forbidden",
                output={},
            )

        cmd = str(call.arguments.get("cmd") or "").strip()
        if not cmd:
            return ToolResult(call_id=call.call_id, ok=False, error="missing_cmd", output={})

        timeout_sec = int(call.arguments.get("timeout_sec") or 15)
        safe_env: Dict[str, str] = {}
        for key in SAFE_ENV_KEYS:
            value = os.getenv(key)
            if value is not None:
                safe_env[key] = value

        workspace_dir = getattr(runtime, "workspace_dir", None)
        cwd = str(workspace_dir) if workspace_dir is not None else os.getcwd()

        try:
            completed = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(1, timeout_sec),
                cwd=cwd,
                env=safe_env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                call_id=call.call_id,
                ok=False,
                error="timeout",
                output={"timeout_sec": timeout_sec, "cwd": cwd},
            )

        return ToolResult(
            call_id=call.call_id,
            ok=(completed.returncode == 0),
            output={
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "exit_code": completed.returncode,
                "cwd": cwd,
            },
            error=None if completed.returncode == 0 else "non_zero_exit",
        )
