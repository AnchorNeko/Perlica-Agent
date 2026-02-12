from __future__ import annotations

import json
import subprocess

import pytest

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest
from perlica.providers.base import ProviderError
from perlica.providers.claude_cli import ClaudeCLIProvider


class DummyPopen:
    def __init__(self, command, *, stdout: str, returncode: int = 0):
        self.command = list(command)
        self.returncode = returncode
        self._stdout = stdout

    def communicate(self, timeout=None):
        return self._stdout, ""

    def kill(self):
        return


def _request() -> LLMRequest:
    return LLMRequest(
        conversation_id="conv",
        messages=[{"role": "user", "content": "请问我偏好"}],
        tools=[],
        context={},
    )


def test_claude_provider_raises_error_max_turns_when_questions_never_resolve(monkeypatch):
    payload = {
        "type": "result",
        "is_error": False,
        "permission_denials": [
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {"question": "问题1", "options": [{"label": "A"}, {"label": "B"}]},
                    ]
                },
            }
        ],
    }
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: DummyPopen(command, stdout=json.dumps(payload), returncode=0),
    )

    def _handler(request: InteractionRequest) -> InteractionAnswer:
        return InteractionAnswer(interaction_id=request.interaction_id, selected_index=1)

    provider = ClaudeCLIProvider(binary="claude", interaction_handler=_handler)
    with pytest.raises(ProviderError) as exc:
        provider.generate(_request())
    assert "error_max_turns" in str(exc.value)

