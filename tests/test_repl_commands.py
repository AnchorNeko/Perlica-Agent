from __future__ import annotations

from io import StringIO

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.repl_commands import (
    InteractionCommandHooks,
    ReplState,
    ServiceCommandHooks,
    dispatch_slash_command,
)


def _state() -> ReplState:
    return ReplState(
        context_id="default",
        provider=None,
        yes=True,
        session_ref=None,
        session_name=None,
        session_is_ephemeral=False,
    )


def test_model_command_removed_direct_path(isolated_env):
    stream = StringIO()
    state = _state()

    result = dispatch_slash_command("/model set claude", state=state, stream=stream)
    assert result.handled is True
    output = stream.getvalue()
    assert "命令 `/model` 已移除" in output
    assert "默认 provider 由配置决定" in output


def test_model_command_removed_menu_path(isolated_env):
    stream = StringIO()
    state = _state()

    result = dispatch_slash_command("/model", state=state, stream=stream)
    assert result.handled is True
    assert "命令 `/model` 已移除" in stream.getvalue()


def test_session_command_updates_state(isolated_env):
    stream = StringIO()
    state = _state()

    created = dispatch_slash_command("/session new --name repl-demo --provider claude", state=state, stream=stream)
    assert created.handled is True
    assert state.session_ref is not None
    assert state.session_name == "repl-demo"

    current = dispatch_slash_command("/session current", state=state, stream=stream)
    assert current.handled is True
    assert "当前会话" in stream.getvalue()


def test_save_and_discard_ephemeral_session(isolated_env):
    stream = StringIO()
    state = _state()

    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    try:
        session = runtime.session_store.create_session(context_id=runtime.context_id, is_ephemeral=True)
        runtime.session_store.set_current_session(runtime.context_id, session.session_id)
        state.session_ref = session.session_id
        state.session_is_ephemeral = True
    finally:
        runtime.close()

    saved = dispatch_slash_command("/save demo-chat", state=state, stream=stream)
    assert saved.handled is True
    assert "会话已保存" in stream.getvalue()

    discarded = dispatch_slash_command("/discard", state=state, stream=stream)
    assert discarded.handled is True
    assert "不是未保存临时会话" in stream.getvalue()


def test_policy_approvals_list_supported(isolated_env):
    stream = StringIO()
    state = _state()

    result = dispatch_slash_command("/policy approvals list", state=state, stream=stream)
    assert result.handled is True


def test_unknown_slash_command_falls_back_to_text(isolated_env):
    stream = StringIO()
    state = _state()
    result = dispatch_slash_command("/foo bar", state=state, stream=stream)
    assert result.handled is False


def test_help_command_lists_service_group(isolated_env):
    stream = StringIO()
    state = _state()
    result = dispatch_slash_command("/help", state=state, stream=stream)
    assert result.handled is True
    output = stream.getvalue()
    assert "/clear" in output
    assert "/pending" in output
    assert "/choose" in output
    assert "/mcp" in output
    assert "/service" in output
    assert "/model" not in output
    assert "/session [list [--all]|new [--name NAME]" in output


def test_pending_and_choose_commands_supported(isolated_env):
    stream = StringIO()
    state = _state()
    state.interaction_hooks = InteractionCommandHooks(
        pending=lambda: "pending-text",
        choose=lambda raw, source: "choose:{0}:{1}".format(raw, source),
        has_pending=lambda: True,
        choice_suggestions=lambda: ["1", "2"],
    )

    result = dispatch_slash_command("/pending", state=state, stream=stream)
    assert result.handled is True
    assert "pending-text" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/choose 2", state=state, stream=stream)
    assert result.handled is True
    assert "choose:2:local" in stream.getvalue()


def test_service_command_group_supported(isolated_env):
    stream = StringIO()
    state = _state()
    state.service_hooks = ServiceCommandHooks(
        status=lambda: "service-status",
        rebind=lambda: "service-rebind",
        unpair=lambda: "service-unpair",
        channel_list=lambda: "service-channel-list",
        channel_use=lambda channel_id: "service-channel-use:{0}".format(channel_id),
        channel_current=lambda: "service-channel-current",
        tools_list=lambda: "service-tools-list",
        tools_allow=lambda tool_name, apply_all, risk: "service-tools-allow:{0}:{1}:{2}".format(
            tool_name,
            apply_all,
            risk,
        ),
        tools_deny=lambda tool_name, apply_all, risk: "service-tools-deny:{0}:{1}:{2}".format(
            tool_name,
            apply_all,
            risk,
        ),
    )

    result = dispatch_slash_command("/service status", state=state, stream=stream)
    assert result.handled is True
    assert "service-status" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/service channel list", state=state, stream=stream)
    assert result.handled is True
    assert "service-channel-list" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/service channel use imessage", state=state, stream=stream)
    assert result.handled is True
    assert "service-channel-use:imessage" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/service tools list", state=state, stream=stream)
    assert result.handled is True
    assert "service-tools-list" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/service tools allow shell.exec --risk low", state=state, stream=stream)
    assert result.handled is True
    assert "service-tools-allow:shell.exec:False:low" in stream.getvalue()


def test_mcp_commands_supported(isolated_env):
    stream = StringIO()
    state = _state()

    result = dispatch_slash_command("/mcp status", state=state, stream=stream)
    assert result.handled is True
    assert "mcp loaded_servers" in stream.getvalue()

    stream = StringIO()
    result = dispatch_slash_command("/mcp list", state=state, stream=stream)
    assert result.handled is True
