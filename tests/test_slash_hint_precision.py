from __future__ import annotations

from types import SimpleNamespace

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.repl_commands import InteractionCommandHooks, ReplState, build_slash_hint


def _state() -> ReplState:
    return ReplState(
        context_id="default",
        provider=None,
        yes=True,
        session_ref=None,
        session_name=None,
        session_is_ephemeral=False,
    )


def test_root_prefix_resolves_mcp_only(isolated_env):
    hint = build_slash_hint("/m", state=_state())
    assert "/mcp" in hint.text
    assert "/model" not in hint.text


def test_removed_model_hint_and_session_provider_values(isolated_env):
    hint = build_slash_hint("/model s", state=_state())
    assert hint.fallback_to_text is True
    assert "未识别命令" in hint.text

    hint = build_slash_hint("/session new --provider ", state=_state())
    assert "claude" in hint.text
    assert "opencode" in hint.text


def test_session_prefix_and_new_option_hints(isolated_env):
    hint = build_slash_hint("/sess", state=_state())
    assert "/session" in hint.text
    assert "list" in hint.text
    assert "new" in hint.text

    hint = build_slash_hint("/session new --", state=_state())
    assert "--name" in hint.text
    assert "--provider" in hint.text


def test_session_use_shows_dynamic_candidates(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    try:
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="demo-hint",
            is_ephemeral=False,
        )
    finally:
        runtime.close()

    hint = build_slash_hint("/session use ", state=_state())
    assert "demo-hint" in hint.text
    assert session.session_id[:16] in hint.text


def test_policy_reset_hints_options_and_risk_values(isolated_env):
    hint = build_slash_hint("/policy approvals reset --", state=_state())
    assert "--all" in hint.text
    assert "--tool" in hint.text
    assert "--risk" in hint.text

    hint = build_slash_hint("/policy approvals reset --risk ", state=_state())
    assert "low" in hint.text
    assert "medium" in hint.text
    assert "high" in hint.text


def test_unknown_slash_hint_marks_fallback(isolated_env):
    hint = build_slash_hint("/sesion", state=_state())
    assert hint.fallback_to_text is True
    assert "普通消息" in hint.text


def test_service_hint_precision(isolated_env):
    hint = build_slash_hint("/ser", state=_state())
    assert "/service" in hint.text

    hint = build_slash_hint("/service r", state=_state())
    assert "rebind" in hint.text

    hint = build_slash_hint("/service c", state=_state())
    assert "channel" in hint.text

    hint = build_slash_hint("/service channel u", state=_state())
    assert "use" in hint.text

    hint = build_slash_hint("/service channel use ", state=_state())
    assert "imessage" in hint.text

    hint = build_slash_hint("/service t", state=_state())
    assert "tools" in hint.text

    hint = build_slash_hint("/service tools ", state=_state())
    assert "list" in hint.text
    assert "allow" in hint.text
    assert "deny" in hint.text

    hint = build_slash_hint("/service tools allow ", state=_state())
    assert "--all" in hint.text
    assert "--risk" in hint.text

    hint = build_slash_hint("/service tools allow --risk ", state=_state())
    assert "low" in hint.text
    assert "medium" in hint.text
    assert "high" in hint.text


def test_service_channel_hint_uses_channel_registry_values(isolated_env, monkeypatch):
    monkeypatch.setattr(
        "perlica.service.channels.list_channel_registrations",
        lambda: [
            SimpleNamespace(channel_id="qq"),
            SimpleNamespace(channel_id="sms"),
        ],
    )

    hint = build_slash_hint("/service channel use ", state=_state())
    assert "qq" in hint.text
    assert "sms" in hint.text


def test_mcp_hint_precision(isolated_env):
    hint = build_slash_hint("/mcp", state=_state())
    assert "list" in hint.text
    assert "reload" in hint.text
    assert "status" in hint.text


def test_pending_and_choose_hints(isolated_env):
    state = _state()
    state.interaction_hooks = InteractionCommandHooks(
        pending=lambda: "pending",
        choose=lambda raw, source: "{0}:{1}".format(raw, source),
        has_pending=lambda: True,
        choice_suggestions=lambda: ["1", "2", "<自定义文本>"],
    )

    hint = build_slash_hint("/pending", state=state)
    assert "待确认交互" in hint.text

    hint = build_slash_hint("/choose ", state=state)
    assert "1" in hint.text
    assert "<自定义文本>" in hint.text
