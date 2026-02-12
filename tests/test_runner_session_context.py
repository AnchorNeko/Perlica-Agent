from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from perlica.config import initialize_project_config, project_config_exists, set_default_provider
from perlica.config import load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse
from perlica.providers.base import ProviderError


class SequencedProvider:
    def __init__(self, provider_id: str, responses):
        self.provider_id = provider_id
        self.responses = list(responses)
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(
            assistant_text="ok",
            tool_calls=[],
            finish_reason="stop",
            usage={
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "context_window": 200000,
                "raw_usage": {},
            },
        )


def _runtime(tmp_path: Path, provider: Optional[str] = "codex") -> Runtime:
    if not project_config_exists(workspace_dir=tmp_path):
        initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="test", provider=provider, workspace_dir=tmp_path)
    return Runtime(settings)


def test_runner_session_provider_lock_enforced(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        session = runtime.session_store.create_session(context_id=runtime.context_id, name="locked", provider_locked="codex")
        runtime.session_store.set_current_session(runtime.context_id, session.session_id)

        runner = Runner(runtime=runtime, provider_id="claude", max_tool_calls=2)
        with pytest.raises(ProviderError):
            runner.run_text("hello", session_ref=session.session_id, assume_yes=True)
    finally:
        runtime.close()


def test_runner_session_context_isolated(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path, provider="fake")
    try:
        provider = SequencedProvider(
            "fake",
            [
                LLMResponse(assistant_text="a1", tool_calls=[], finish_reason="stop"),
                LLMResponse(assistant_text="a2", tool_calls=[], finish_reason="stop"),
                LLMResponse(assistant_text="b1", tool_calls=[], finish_reason="stop"),
            ],
        )
        runtime.register_provider(provider)

        session_a = runtime.session_store.create_session(context_id=runtime.context_id, name="A", provider_locked="fake")
        session_b = runtime.session_store.create_session(context_id=runtime.context_id, name="B", provider_locked="fake")

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        runner.run_text("first in A", session_ref=session_a.session_id, assume_yes=True)
        runner.run_text("second in A", session_ref=session_a.session_id, assume_yes=True)
        runner.run_text("first in B", session_ref=session_b.session_id, assume_yes=True)

        second_a_messages = provider.requests[1].messages
        third_b_messages = provider.requests[2].messages

        assert any("first in A" in str(item.get("content")) for item in second_a_messages)
        assert not any("first in A" in str(item.get("content")) for item in third_b_messages)
    finally:
        runtime.close()


def test_runner_over_budget_truncates_without_summary_call(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path, provider="fake")
    try:
        runtime.settings.provider_context_windows["fake"] = 60

        provider = SequencedProvider(
            "fake",
            [
                LLMResponse(assistant_text="final response", tool_calls=[], finish_reason="stop"),
            ],
        )
        runtime.register_provider(provider)

        session = runtime.session_store.create_session(context_id=runtime.context_id, name="S", provider_locked="fake")

        for idx in range(8):
            runtime.session_store.append_message(
                session.session_id,
                "user" if idx % 2 == 0 else "assistant",
                {"text": "very long history item {0} ".format(idx) * 20},
                run_id="seed",
            )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        result = runner.run_text("new question", session_ref=session.session_id, assume_yes=True)

        latest_summary = runtime.session_store.get_latest_summary(session.session_id)
        assert latest_summary is None
        assert result.assistant_text == "final response"
        assert len(provider.requests) == 1

        events = runtime.event_log.list_events(limit=300)
        truncated = [evt for evt in events if evt.event_type == "context.truncated"]
        assert truncated
        assert truncated[-1].payload.get("reason") == "single_call_mode_no_summary_call"
    finally:
        runtime.close()


def test_runner_requires_explicit_provider_for_unlocked_session(isolated_env, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    set_default_provider("claude", workspace_dir=tmp_path)
    runtime = _runtime(tmp_path, provider=None)
    try:
        session = runtime.session_store.create_session(context_id=runtime.context_id, name="unlocked")
        runner = Runner(runtime=runtime, provider_id=None, max_tool_calls=2)
        with pytest.raises(ProviderError) as exc_info:
            runner.run_text("hello", session_ref=session.session_id, assume_yes=True)
        assert "please specify --provider claude" in str(exc_info.value)
        unlocked = runtime.session_store.get_session(session.session_id)
        assert unlocked is not None
        assert unlocked.provider_locked is None
    finally:
        runtime.close()


def test_runner_keeps_locked_provider_after_default_change(isolated_env, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    set_default_provider("claude", workspace_dir=tmp_path)
    runtime = _runtime(tmp_path, provider=None)
    try:
        codex_provider = SequencedProvider(
            "codex",
            [LLMResponse(assistant_text="locked codex", tool_calls=[], finish_reason="stop")],
        )
        runtime.register_provider(codex_provider)

        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="locked-codex",
            provider_locked="codex",
        )
        runner = Runner(runtime=runtime, provider_id=None, max_tool_calls=2)
        result = runner.run_text("hello", session_ref=session.session_id, assume_yes=True)

        assert result.provider_id == "codex"
        assert codex_provider.requests
        locked = runtime.session_store.get_session(session.session_id)
        assert locked is not None
        assert locked.provider_locked == "codex"
    finally:
        runtime.close()
