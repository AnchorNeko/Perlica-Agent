from __future__ import annotations

import json
import subprocess

import pytest

from perlica.providers.base import ProviderContractError, ProviderError
from perlica.providers.claude_cli import ClaudeCLIProvider
from perlica.providers.codex_cli import CodexCLIProvider
from perlica.kernel.types import LLMRequest


class DummyCompleted(subprocess.CompletedProcess):
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        super().__init__(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


class DummyPopen:
    def __init__(self, command, *, stdout: str, stderr: str = "", returncode: int = 0, plan=None):
        self.command = list(command)
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._plan = list(plan or [("return", stdout, stderr)])
        self.killed = False

    def communicate(self, timeout=None):
        if not self._plan:
            return self._stdout, self._stderr
        action, out, err = self._plan.pop(0)
        if action == "timeout":
            raise subprocess.TimeoutExpired(cmd=self.command, timeout=timeout, output=out, stderr=err)
        return out, err

    def kill(self):
        self.killed = True


def _patch_claude_popen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str,
    stderr: str = "",
    returncode: int = 0,
    plan=None,
    capture: dict | None = None,
):
    def _popen(command, **kwargs):
        if capture is not None:
            capture["command"] = list(command)
        return DummyPopen(command, stdout=stdout, stderr=stderr, returncode=returncode, plan=plan)

    monkeypatch.setattr(subprocess, "Popen", _popen)


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"tool_name": "shell.exec"}],
        context={},
    )


def test_codex_provider_parses_agent_message(monkeypatch: pytest.MonkeyPatch):
    message = {
        "assistant_text": "ok",
        "tool_calls": [
            {
                "call_id": "c1",
                "tool_name": "shell.exec",
                "arguments": {"cmd": "echo hi"},
                "risk_tier": "low",
            }
        ],
        "finish_reason": "tool_calls",
    }
    jsonl = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": json.dumps(message)},
                }
            ),
        ]
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: DummyCompleted(stdout=jsonl, returncode=0),
    )

    provider = CodexCLIProvider(binary="codex")
    response = provider.generate(_request())

    assert response.assistant_text == "ok"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool_name == "shell.exec"


def test_codex_provider_rejects_command_execution_event(monkeypatch: pytest.MonkeyPatch):
    jsonl = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"type": "command_execution", "command": "echo hi"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "{}"},
                }
            ),
        ]
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: DummyCompleted(stdout=jsonl, returncode=0),
    )

    provider = CodexCLIProvider(binary="codex")
    with pytest.raises(ProviderContractError):
        provider.generate(_request())


def test_claude_provider_structured_output(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "done",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "done"
    assert response.tool_calls == []


def test_claude_provider_allows_missing_tool_calls_key(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "done",
            "finish_reason": "stop",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "done"
    assert response.tool_calls == []


def test_claude_provider_rejects_missing_finish_reason(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "done",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    with pytest.raises(ProviderContractError):
        provider.generate(_request())


def test_claude_provider_falls_back_to_result_when_structured_text_empty(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "result": "fallback text",
        "structured_output": {
            "assistant_text": "",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "fallback text"
    assert response.tool_calls == []
    assert response.finish_reason == "stop"


def test_claude_provider_rejects_empty_text_without_tool_calls(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "result": "",
        "structured_output": {
            "assistant_text": "",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    with pytest.raises(ProviderContractError):
        provider.generate(_request())


def test_claude_provider_falls_back_to_content_items(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "content": [
            {"type": "text", "text": "from content list"},
        ],
        "structured_output": {
            "assistant_text": "",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "from content list"
    assert response.tool_calls == []


def test_claude_provider_falls_back_to_result_object_content(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "result": {
            "content": [
                {"type": "text", "text": "nested result text"},
            ]
        },
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "nested result text"
    assert response.tool_calls == []


def test_claude_provider_falls_back_to_diagnostic_metadata(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "errors": [{"message": "tool sandbox denied"}],
        "permission_denials": [{"tool_name": "shell.exec", "reason": "approval_required"}],
        "subtype": "tool_permission_denied",
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert "diagnostics without assistant text" in response.assistant_text
    assert "tool sandbox denied" in response.assistant_text
    assert "approval_required" in response.assistant_text
    assert response.tool_calls == []


def test_claude_provider_is_error_uses_diagnostic_metadata(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": True,
        "errors": [{"message": "rate limit exceeded"}],
    }

    _patch_claude_popen(monkeypatch, stdout=json.dumps(payload), returncode=0)

    provider = ClaudeCLIProvider(binary="claude")
    with pytest.raises(ProviderError) as exc:
        provider.generate(_request())
    assert "rate limit exceeded" in str(exc.value)


def test_claude_provider_enables_bypass_permissions_and_default_tools(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "ok",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }
    captured = {}
    _patch_claude_popen(
        monkeypatch,
        stdout=json.dumps(payload),
        returncode=0,
        capture=captured,
    )

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert response.assistant_text == "ok"
    command = captured.get("command") or []
    assert "--permission-mode" in command
    assert "bypassPermissions" in command
    assert "--tools" in command
    assert "default" in command


def test_claude_provider_timeout_error_mentions_threshold(monkeypatch: pytest.MonkeyPatch):
    _patch_claude_popen(
        monkeypatch,
        stdout="",
        returncode=0,
        plan=[
            ("timeout", "", ""),
            ("return", "", ""),
        ],
    )
    provider = ClaudeCLIProvider(binary="claude", timeout_sec=123)
    with pytest.raises(ProviderError) as exc:
        provider.generate(_request())
    assert "timed out after 123s" in str(exc.value)


def test_claude_provider_allows_long_reasoning_when_activity_present(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "after long reasoning",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }
    _patch_claude_popen(
        monkeypatch,
        stdout=json.dumps(payload),
        returncode=0,
        plan=[
            ("timeout", "thinking...", ""),
            ("return", json.dumps(payload), ""),
        ],
    )
    provider = ClaudeCLIProvider(binary="claude", timeout_sec=10)
    response = provider.generate(_request())
    assert response.assistant_text == "after long reasoning"


def test_claude_provider_returns_diagnostics_without_plaintext_retry(monkeypatch: pytest.MonkeyPatch):
    structured_failure_payload = {
        "type": "result",
        "is_error": False,
        "errors": [{"message": "Failed to provide valid structured output after 5 attempts"}],
        "subtype": "error_max_structured_output_retries",
        "usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 0,
            "output_tokens": 1,
        },
    }
    captured = {"commands": []}

    def _popen(command, **kwargs):
        captured["commands"].append(list(command))
        return DummyPopen(command, stdout=json.dumps(structured_failure_payload), returncode=0)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())
    assert "diagnostics without assistant text" in response.assistant_text
    assert "Failed to provide valid structured output after 5 attempts" in response.assistant_text
    assert len(captured["commands"]) == 1
    first = captured["commands"][0]
    assert "--output-format" in first and "json" in first
