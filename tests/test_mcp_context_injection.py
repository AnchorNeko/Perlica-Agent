from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse
from perlica.mcp.types import MCPToolSpec
from perlica.skills.engine import SkillSelection
from perlica.skills.schema import SkillSpec


class _CaptureProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.requests = []

    def generate(self, req):
        self.requests.append(req)
        return LLMResponse(assistant_text="ok", tool_calls=[], finish_reason="stop")


def test_runner_no_longer_injects_mcp_or_skills_into_provider_config(monkeypatch, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        provider = _CaptureProvider()
        runtime.register_provider(provider)

        monkeypatch.setattr(
            runtime.mcp_manager,
            "list_tool_specs",
            lambda: [MCPToolSpec(server_id="demo", tool_name="echo")],
        )
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        runner.run_text("test mcp", assume_yes=True, session_ref=session.session_id)

        req = provider.requests[0]
        contents = [str(item.get("content") or "") for item in req.messages]
        assert not any("MCP Resources" in item for item in contents)
        assert "macOS computer steward" in contents[0]
        assert req.tools == []
        provider_config = req.context.get("provider_config") if isinstance(req.context, dict) else {}
        assert isinstance(provider_config, dict)
        assert "mcp_servers" not in provider_config
        assert "skills" not in provider_config
        assert provider_config.get("tool_execution_mode") == "provider_managed"
        assert provider_config.get("injection_failure_policy") == "degrade"

        events = runtime.event_log.list_events(limit=200)
        llm_requested = [item for item in events if item.event_type == "llm.requested"]
        assert llm_requested
        payload = llm_requested[-1].payload
        assert int(payload.get("mcp_tools_count") or 0) >= 1
        assert int(payload.get("mcp_context_blocks_count") or 0) == 0
        assert int(payload.get("mcp_provider_config_count") or 0) == 0
    finally:
        runtime.close()


def test_runner_provider_config_retains_runtime_policy_fields_only(tmp_path: Path):
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
        runner.run_text("test empty mcp", assume_yes=True, session_ref=session.session_id)

        req = provider.requests[0]
        provider_config = req.context.get("provider_config") if isinstance(req.context, dict) else {}
        assert isinstance(provider_config, dict)
        assert set(provider_config.keys()) == {"tool_execution_mode", "injection_failure_policy"}
        assert provider_config.get("tool_execution_mode") == "provider_managed"
        assert provider_config.get("injection_failure_policy") == "degrade"
    finally:
        runtime.close()


def test_runner_provider_capability_flags_no_longer_change_mcp_skill_injection(monkeypatch, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    settings.provider_profile = replace(
        settings.provider_profile,
        supports_mcp_config=False,
        supports_skill_config=False,
    )
    runtime = Runtime(settings)
    try:
        provider = _CaptureProvider()
        runtime.register_provider(provider)

        monkeypatch.setattr(
            runtime.skill_engine,
            "select",
            lambda text: SkillSelection(
                selected=[
                    SkillSpec(
                        skill_id="demo-skill",
                        name="Demo",
                        description="demo desc",
                        triggers=["demo"],
                        priority=10,
                        system_prompt="demo prompt",
                        source_path="demo.skill.json",
                    )
                ],
                skipped={},
            ),
        )
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        runner.run_text("demo", assume_yes=True, session_ref=session.session_id)

        req = provider.requests[0]
        provider_config = req.context.get("provider_config") if isinstance(req.context, dict) else {}
        assert isinstance(provider_config, dict)
        assert "mcp_servers" not in provider_config
        assert "skills" not in provider_config
        assert provider_config.get("tool_execution_mode") == "provider_managed"
    finally:
        runtime.close()


def test_runner_loaded_skills_are_counted_but_not_injected(monkeypatch, tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        provider = _CaptureProvider()
        runtime.register_provider(provider)

        skill = SkillSpec(
            skill_id="demo-skill",
            name="Demo",
            description="demo desc",
            triggers=["demo"],
            priority=10,
            system_prompt="demo prompt",
            source_path="demo.skill.json",
        )
        monkeypatch.setattr(
            runtime.skill_engine,
            "list_skills",
            lambda: [skill, skill],
        )
        monkeypatch.setattr(
            runtime.skill_engine,
            "select",
            lambda text: SkillSelection(selected=[], skipped={"demo-skill": "trigger_not_matched"}),
        )
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)
        runner.run_text("demo", assume_yes=True, session_ref=session.session_id)

        req = provider.requests[0]
        provider_config = req.context.get("provider_config") if isinstance(req.context, dict) else {}
        assert isinstance(provider_config, dict)
        assert "skills" not in provider_config
        assert "mcp_servers" not in provider_config
        assert req.tools == []

        events = runtime.event_log.list_events(limit=200)
        llm_requested = [item for item in events if item.event_type == "llm.requested"]
        assert llm_requested
        payload = llm_requested[-1].payload
        assert int(payload.get("skills_selected_count") or 0) == 2
    finally:
        runtime.close()
