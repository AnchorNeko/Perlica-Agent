from __future__ import annotations

import subprocess

from perlica.security.permission_probe import run_startup_permission_checks


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_permission_probe_success(monkeypatch):
    def fake_run(*args, **kwargs):
        return _completed(0, stdout="ok")

    monkeypatch.setattr("perlica.security.permission_probe.subprocess.run", fake_run)
    report = run_startup_permission_checks(trigger_applescript=True)
    assert report["ok"] is True
    checks = report["checks"]
    assert checks["shell"]["ok"] is True
    assert checks["applescript"]["ok"] is True


def test_permission_probe_applescript_denied(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "osascript":
            return _completed(1, stderr="not authorized")
        return _completed(0, stdout="ok")

    monkeypatch.setattr("perlica.security.permission_probe.subprocess.run", fake_run)
    report = run_startup_permission_checks(trigger_applescript=True)
    assert report["ok"] is False
    checks = report["checks"]
    assert checks["shell"]["ok"] is True
    assert checks["applescript"]["ok"] is False
    assert checks["applescript"]["status"] in {"denied", "error"}


def test_permission_probe_shell_failure(monkeypatch):
    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "/bin/sh":
            return _completed(2, stderr="permission denied")
        return _completed(0, stdout="ok")

    monkeypatch.setattr("perlica.security.permission_probe.subprocess.run", fake_run)
    report = run_startup_permission_checks(trigger_applescript=False)
    assert report["ok"] is False
    checks = report["checks"]
    assert checks["shell"]["ok"] is False
