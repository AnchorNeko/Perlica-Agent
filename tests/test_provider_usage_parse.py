from __future__ import annotations

import json
import subprocess

import pytest

from perlica.kernel.types import LLMRequest
from perlica.providers.claude_cli import ClaudeCLIProvider
from perlica.providers.codex_cli import CodexCLIProvider


class DummyCompleted(subprocess.CompletedProcess):
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        super().__init__(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


class DummyPopen:
    def __init__(self, command, *, stdout: str, stderr: str = "", returncode: int = 0):
        self.command = list(command)
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self):
        return


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        context={},
    )


def test_codex_usage_parsed(monkeypatch: pytest.MonkeyPatch):
    jsonl = "\n".join(
        [
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 11,
                        "cached_input_tokens": 5,
                        "output_tokens": 7,
                    },
                }
            ),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: DummyCompleted(stdout=jsonl))

    provider = CodexCLIProvider(binary="codex")
    response = provider.generate(_request())

    assert response.usage["input_tokens"] == 11
    assert response.usage["cached_input_tokens"] == 5
    assert response.usage["output_tokens"] == 7


def test_claude_usage_parsed(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "type": "result",
        "is_error": False,
        "usage": {
            "input_tokens": 101,
            "cache_read_input_tokens": 12,
            "output_tokens": 33,
        },
        "modelUsage": {
            "claude-opus": {
                "contextWindow": 200000,
            }
        },
        "structured_output": {
            "assistant_text": "ok",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: DummyPopen(command, stdout=json.dumps(payload)),
    )

    provider = ClaudeCLIProvider(binary="claude")
    response = provider.generate(_request())

    assert response.usage["input_tokens"] == 101
    assert response.usage["cached_input_tokens"] == 12
    assert response.usage["output_tokens"] == 33
    assert response.usage["context_window"] == 200000
