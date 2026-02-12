from __future__ import annotations

import json
import subprocess

from perlica.interaction.types import InteractionAnswer, InteractionRequest
from perlica.kernel.types import LLMRequest
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


def test_claude_provider_handles_multiple_questions_in_one_round(monkeypatch):
    first_payload = {
        "type": "result",
        "is_error": False,
        "permission_denials": [
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {"question": "问题1", "options": [{"label": "A"}, {"label": "B"}]},
                        {"question": "问题2", "options": [{"label": "C"}, {"label": "D"}]},
                    ]
                },
            }
        ],
    }
    second_payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "已完成偏好确认。",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }
    sequence = [json.dumps(first_payload), json.dumps(second_payload)]

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda command, **kwargs: DummyPopen(command, stdout=sequence.pop(0), returncode=0),
    )

    seen = []

    def _handler(request: InteractionRequest) -> InteractionAnswer:
        seen.append(request.question)
        return InteractionAnswer(
            interaction_id=request.interaction_id,
            custom_text="我的回答",
            source="local",
        )

    provider = ClaudeCLIProvider(binary="claude", interaction_handler=_handler)
    response = provider.generate(_request())
    assert response.assistant_text == "已完成偏好确认。"
    assert seen == ["问题1", "问题2"]

