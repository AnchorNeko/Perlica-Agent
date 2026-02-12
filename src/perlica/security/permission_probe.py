"""Startup permission probes for shell and AppleScript execution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class PermissionProbe:
    name: str
    ok: bool
    status: str
    detail: str
    hint: str = ""

    def as_dict(self) -> Dict[str, str]:
        data = {
            "name": self.name,
            "ok": bool(self.ok),
            "status": self.status,
            "detail": self.detail,
        }
        if self.hint:
            data["hint"] = self.hint
        return data


def probe_shell_permission(workspace_dir: Optional[Path] = None) -> PermissionProbe:
    cwd = str((workspace_dir or Path.cwd()).resolve())
    try:
        completed = subprocess.run(
            ["/bin/sh", "-lc", "pwd >/dev/null"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return PermissionProbe(
            name="shell",
            ok=False,
            status="error",
            detail=str(exc),
            hint="请确认 shell 可执行并且当前目录可访问。",
        )

    if completed.returncode == 0:
        return PermissionProbe(
            name="shell",
            ok=True,
            status="ok",
            detail="shell command execution available",
        )

    return PermissionProbe(
        name="shell",
        ok=False,
        status="error",
        detail=(completed.stderr or completed.stdout or "unknown shell error").strip(),
        hint="请检查终端权限与 shell 环境配置。",
    )


def probe_applescript_permission(trigger: bool = True) -> PermissionProbe:
    if trigger:
        script = 'tell application "System Events" to get name of first process'
    else:
        script = 'return "ok"'

    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except FileNotFoundError:
        return PermissionProbe(
            name="applescript",
            ok=False,
            status="missing",
            detail="osascript not found",
            hint="请在 macOS 上运行，或确认 osascript 可用。",
        )
    except Exception as exc:
        return PermissionProbe(
            name="applescript",
            ok=False,
            status="error",
            detail=str(exc),
            hint="请检查 AppleScript 运行环境。",
        )

    if completed.returncode == 0:
        return PermissionProbe(
            name="applescript",
            ok=True,
            status="ok",
            detail="applescript probe succeeded",
        )

    detail = (completed.stderr or completed.stdout or "unknown applescript error").strip()
    return PermissionProbe(
        name="applescript",
        ok=False,
        status="denied",
        detail=detail,
        hint=(
            "请在 系统设置 -> 隐私与安全性 -> 自动化 中允许终端/解释器控制“系统事件”(System Events)。"
        ),
    )


def run_startup_permission_checks(
    workspace_dir: Optional[Path] = None,
    *,
    trigger_applescript: bool = True,
) -> Dict[str, object]:
    shell = probe_shell_permission(workspace_dir=workspace_dir)
    applescript = probe_applescript_permission(trigger=trigger_applescript)
    checks = {
        "shell": shell.as_dict(),
        "applescript": applescript.as_dict(),
    }
    return {
        "ok": bool(shell.ok and applescript.ok),
        "checks": checks,
    }
