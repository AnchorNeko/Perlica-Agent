from __future__ import annotations

from pathlib import Path

import pytest

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse
from perlica.prompt.system_prompt import PromptLoadError


class _CaptureProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        return LLMResponse(assistant_text="ok", tool_calls=[], finish_reason="stop")


def test_runtime_fails_when_system_prompt_missing(tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    prompt_file = tmp_path / ".perlica_config" / "prompts" / "system.md"
    prompt_file.unlink()

    settings = load_settings(context_id="default", workspace_dir=tmp_path)
    with pytest.raises(PromptLoadError):
        Runtime(settings)


def test_runner_injects_external_system_prompt(tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        provider = _CaptureProvider()
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        result = runner.run_text("hello", assume_yes=True, session_ref=session.session_id)
        assert result.assistant_text == "ok"
        assert provider.requests
        first = provider.requests[0]
        assert first.messages
        assert first.messages[0]["role"] == "system"
        assert "macOS control agent" in str(first.messages[0]["content"])
    finally:
        runtime.close()
