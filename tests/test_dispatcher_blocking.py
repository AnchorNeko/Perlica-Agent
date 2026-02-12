from __future__ import annotations

from pathlib import Path

from perlica.kernel.dispatcher import Dispatcher
from perlica.kernel.policy_engine import (
    APPROVAL_ALWAYS_ALLOW,
    ApprovalAction,
    ApprovalStore,
    PolicyEngine,
)
from perlica.kernel.registry import Registry
from perlica.kernel.types import ToolCall
from perlica.tools.shell_tool import ShellTool


class DummyRuntime:
    def __init__(self, workspace_dir: Path, approval_store: ApprovalStore) -> None:
        self.workspace_dir = workspace_dir
        self.approval_store = approval_store
        self.events = []

    def emit(self, event_type: str, payload: dict, **_kwargs) -> str:
        self.events.append((event_type, payload))
        return "evt"


def test_dispatcher_blocks_high_risk_commands(tmp_path: Path):
    registry = Registry()
    registry.register_tool(ShellTool())

    approval_store = ApprovalStore(tmp_path / "approvals.db")
    dispatcher = Dispatcher(registry, PolicyEngine(approval_store))
    runtime = DummyRuntime(tmp_path, approval_store)

    call = ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "rm -rf /"}, risk_tier="low")
    result = dispatcher.dispatch(call, runtime, assume_yes=True)

    assert result.blocked is True
    assert result.result.ok is False
    assert result.result.error == "blocked_high_risk_pattern"


def test_shell_tool_direct_execution_is_forbidden(tmp_path: Path):
    tool = ShellTool()
    approval_store = ApprovalStore(tmp_path / "approvals.db")
    runtime = DummyRuntime(tmp_path, approval_store)

    result = tool.execute(
        ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "echo hi"}, risk_tier="low"),
        runtime,
    )

    assert result.ok is False
    assert result.error == "direct_execution_forbidden"


def test_dispatcher_respects_persisted_allow_policy(tmp_path: Path):
    registry = Registry()
    registry.register_tool(ShellTool())

    approval_store = ApprovalStore(tmp_path / "approvals.db")
    approval_store.set_policy("shell.exec", "low", APPROVAL_ALWAYS_ALLOW)
    dispatcher = Dispatcher(registry, PolicyEngine(approval_store))
    runtime = DummyRuntime(tmp_path, approval_store)

    call = ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "echo hi"}, risk_tier="low")
    result = dispatcher.dispatch(call, runtime)

    assert result.blocked is False
    assert result.result.ok is True
    assert "hi" in result.result.output.get("stdout", "")


def test_dispatcher_non_tty_path_returns_approval_required(tmp_path: Path):
    registry = Registry()
    registry.register_tool(ShellTool())

    approval_store = ApprovalStore(tmp_path / "approvals.db")
    dispatcher = Dispatcher(registry, PolicyEngine(approval_store))
    runtime = DummyRuntime(tmp_path, approval_store)

    call = ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "echo hi"}, risk_tier="low")
    result = dispatcher.dispatch(call, runtime, assume_yes=False, approval_resolver=None)

    assert result.blocked is True
    assert result.result.error == "approval_required"


def test_dispatcher_persists_deny_policy(tmp_path: Path):
    registry = Registry()
    registry.register_tool(ShellTool())

    approval_store = ApprovalStore(tmp_path / "approvals.db")
    dispatcher = Dispatcher(registry, PolicyEngine(approval_store))
    runtime = DummyRuntime(tmp_path, approval_store)

    call = ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": "echo hi"}, risk_tier="low")
    result = dispatcher.dispatch(
        call,
        runtime,
        assume_yes=False,
        approval_resolver=lambda _call, _risk: ApprovalAction(
            allow=False,
            persist_policy="always_deny",
            reason="test_deny",
        ),
    )

    assert result.blocked is True
    assert approval_store.get_policy("shell.exec", "low") == "always_deny"
