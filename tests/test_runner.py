from __future__ import annotations

from pathlib import Path

from perlica.config import initialize_project_config
from perlica.config import load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse, ToolCall, ToolResult
from perlica.providers.base import ProviderError


class FakeProvider:
    provider_id = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        return self.responses.pop(0)


class FakeTool:
    tool_name = "fake.tool"

    def __init__(self):
        self.calls = []

    def execute(self, call, runtime):
        self.calls.append(call)
        return ToolResult(call_id=call.call_id, ok=True, output={"echo": call.arguments})


class FakeSideEffectTool:
    tool_name = "mcp.notes.write"

    def __init__(self):
        self.calls = []

    def execute(self, call, runtime):
        self.calls.append(call)
        return ToolResult(call_id=call.call_id, ok=True, output={"ok": True})


class ErrorProvider:
    provider_id = "fake"

    def generate(self, req):
        raise ProviderError("synthetic provider failure")


def _runtime(tmp_path: Path) -> Runtime:
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="test", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    return runtime


def test_runner_no_tool_call_path(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider([LLMResponse(assistant_text="plain response", tool_calls=[], finish_reason="stop")])
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=3)
        result = runner.run_text("hello", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "plain response"
        assert result.tool_results == []
        assert len(provider.requests) == 1
        assert provider.requests[0].tools == []
    finally:
        runtime.close()


def test_runner_tool_call_and_followup(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        fake_tool = FakeTool()
        runtime.register_tool(fake_tool)

        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="I will run a tool",
                    tool_calls=[
                        ToolCall(
                            call_id="call-1",
                            tool_name="fake.tool",
                            arguments={"value": 1},
                            risk_tier="low",
                        )
                    ],
                    finish_reason="tool_calls",
                ),
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=3)
        result = runner.run_text("run tool", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "I will run a tool"
        assert len(result.tool_results) == 1
        assert result.tool_results[0].error == "single_call_mode_local_tool_dispatch_disabled"
        assert len(fake_tool.calls) == 0
        assert len(provider.requests) == 1

        events = runtime.event_log.list_events(limit=200)
        enforced = [evt for evt in events if evt.event_type == "llm.single_call.enforced"]
        assert enforced
        assert enforced[-1].payload.get("blocked_tool_calls_count") == 1
    finally:
        runtime.close()


def test_runner_ignores_max_tool_calls_in_single_call_mode(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="too many",
                    tool_calls=[
                        ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "echo 1"}, risk_tier="low"),
                        ToolCall(call_id="c2", tool_name="shell.exec", arguments={"cmd": "echo 2"}, risk_tier="low"),
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=1)
        result = runner.run_text("too many tools", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "too many"
        assert len(result.tool_results) == 2
        assert all(item.error == "single_call_mode_local_tool_dispatch_disabled" for item in result.tool_results)
    finally:
        runtime.close()


def test_runner_rejects_empty_provider_response(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="",
                    tool_calls=[],
                    finish_reason="stop",
                    raw={"message": "non-empty raw preview"},
                )
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=2)
        try:
            runner.run_text("hello", assume_yes=True, session_ref=session.session_id)
            assert False, "expected ProviderError for empty provider response"
        except ProviderError as exc:
            assert "empty assistant_text" in str(exc)
        events = runtime.event_log.list_events(limit=200)
        invalid = [evt for evt in events if evt.event_type == "llm.invalid_response"]
        assert invalid
        payload = invalid[-1].payload
        assert payload.get("reason") == "empty_assistant_text"
        summary = payload.get("response_raw_summary")
        assert isinstance(summary, dict)
        assert "keys" in summary
    finally:
        runtime.close()


def test_runner_emits_provider_error_event(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        runtime.register_provider(ErrorProvider())
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )
        runner = Runner(runtime, provider_id="fake", max_tool_calls=2)
        try:
            runner.run_text("hello", assume_yes=True, session_ref=session.session_id)
            assert False, "expected ProviderError"
        except ProviderError as exc:
            assert "synthetic provider failure" in str(exc)

        events = runtime.event_log.list_events(limit=200)
        provider_errors = [evt for evt in events if evt.event_type == "llm.provider_error"]
        assert provider_errors
        payload = provider_errors[-1].payload
        assert payload.get("provider_id") == "fake"
        assert "synthetic provider failure" in str(payload.get("error"))
        assert payload.get("error_type") == "ProviderError"
        assert payload.get("method") == ""
        assert payload.get("request_id") == ""
    finally:
        runtime.close()


def test_runner_blocks_duplicate_side_effect_calls_within_run(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="calling write twice",
                    tool_calls=[
                        ToolCall(
                            call_id="c1",
                            tool_name="mcp.notes.write",
                            arguments={"title": "x", "body": "y"},
                            risk_tier="low",
                        ),
                        ToolCall(
                            call_id="c2",
                            tool_name="mcp.notes.write",
                            arguments={"body": "y", "title": "x"},
                            risk_tier="low",
                        ),
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=4)
        result = runner.run_text("write note", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "calling write twice"
        assert len(result.tool_results) == 2
        blocked = [
            item
            for item in result.tool_results
            if item.error == "single_call_mode_local_tool_dispatch_disabled"
        ]
        assert len(blocked) == 2

        events = runtime.event_log.list_events(limit=300)
        blocked_events = [evt for evt in events if evt.event_type == "tool.blocked"]
        assert blocked_events
        payload = blocked_events[-1].payload
        assert payload.get("reason") == "single_call_mode_local_tool_dispatch_disabled"
        assert payload.get("tool_name") == "mcp.notes.write"
        assert payload.get("run_id")
    finally:
        runtime.close()


def test_runner_blocks_non_side_effect_tool_calls_too(isolated_env, tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="calling regular tools",
                    tool_calls=[
                        ToolCall(
                            call_id="c1",
                            tool_name="fake.tool",
                            arguments={"value": 1},
                            risk_tier="low",
                        ),
                        ToolCall(
                            call_id="c2",
                            tool_name="fake.tool",
                            arguments={"value": 2},
                            risk_tier="low",
                        ),
                    ],
                    finish_reason="tool_calls",
                )
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime, provider_id="fake", max_tool_calls=4)
        result = runner.run_text("write note once", assume_yes=True, session_ref=session.session_id)

        assert result.assistant_text == "calling regular tools"
        assert len(result.tool_results) == 2
        blocked = [
            item
            for item in result.tool_results
            if item.error == "single_call_mode_local_tool_dispatch_disabled"
        ]
        assert len(blocked) == 2

        events = runtime.event_log.list_events(limit=300)
        blocked_events = [evt for evt in events if evt.event_type == "tool.blocked"]
        assert blocked_events
        payload = blocked_events[-1].payload
        assert payload.get("reason") == "single_call_mode_local_tool_dispatch_disabled"
        assert payload.get("tool_name") == "fake.tool"
    finally:
        runtime.close()
