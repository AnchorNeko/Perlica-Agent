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
        messages=[{"role": "user", "content": "你来询问一下我的代码偏好"}],
        tools=[],
        context={},
    )


def test_claude_provider_permission_denial_questions_then_continue(monkeypatch):
    first_payload = {
        "type": "result",
        "is_error": False,
        "result": "请回答问题",
        "permission_denials": [
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [
                        {
                            "header": "代码风格",
                            "question": "你更喜欢哪种风格？",
                            "options": [
                                {"label": "简洁紧凑", "description": "更短"},
                                {"label": "清晰详细", "description": "更清楚"},
                            ],
                        }
                    ]
                },
            }
        ],
    }
    second_payload = {
        "type": "result",
        "is_error": False,
        "structured_output": {
            "assistant_text": "好的，后续我会按清晰详细风格输出。",
            "tool_calls": [],
            "finish_reason": "stop",
        },
    }
    sequence = [json.dumps(first_payload), json.dumps(second_payload)]
    captured_commands = []

    def _popen(command, **kwargs):
        captured_commands.append(list(command))
        stdout = sequence.pop(0)
        return DummyPopen(command, stdout=stdout, returncode=0)

    monkeypatch.setattr(subprocess, "Popen", _popen)

    requested = []

    def _handler(request: InteractionRequest) -> InteractionAnswer:
        requested.append(request)
        return InteractionAnswer(
            interaction_id=request.interaction_id,
            selected_index=2,
            selected_option_id="option_2",
            source="local",
        )

    provider = ClaudeCLIProvider(binary="claude", interaction_handler=_handler)
    response = provider.generate(_request())
    assert response.assistant_text.startswith("好的")
    assert len(requested) == 1
    assert len(captured_commands) == 2
    assert "User answered your previous questions:" in captured_commands[1][-1]

