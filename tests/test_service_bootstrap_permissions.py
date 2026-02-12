from __future__ import annotations

import subprocess

from perlica.service.channels.imessage_adapter import IMessageChannelAdapter


def test_imessage_bootstrap_success(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/imsg")

    def fake_run(cmd, capture_output, text, check):
        assert cmd[:2] == ["imsg", "chats"]
        return subprocess.CompletedProcess(cmd, 0, stdout="[1] (x)", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = IMessageChannelAdapter(binary="imsg")
    result = adapter.bootstrap()
    assert result.ok is True
    assert result.needs_user_action is False


def test_imessage_bootstrap_permission_error_opens_settings(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/imsg")
    calls = []

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        if cmd[:2] == ["imsg", "chats"]:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="Operation not permitted",
            )
        if cmd[0] == "open":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError("unexpected command: {0}".format(cmd))

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = IMessageChannelAdapter(binary="imsg")
    result = adapter.bootstrap()
    assert result.ok is False
    assert result.needs_user_action is True
    assert result.opened_system_settings is True
    assert any(cmd[0] == "open" for cmd in calls)

