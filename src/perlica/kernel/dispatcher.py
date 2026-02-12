"""Central tool dispatcher with policy and approval enforcement."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Callable, Optional

from perlica.kernel.policy_engine import ApprovalAction, PolicyEngine
from perlica.kernel.registry import Registry
from perlica.kernel.types import ToolCall, ToolResult

DISPATCH_ACTIVE = contextvars.ContextVar("perlica_dispatch_active", default=False)

ApprovalResolver = Callable[[ToolCall, str], ApprovalAction]


@dataclass
class DispatchResult:
    result: ToolResult
    blocked: bool = False


class Dispatcher:
    """Enforces policy -> approval -> tool execution path."""

    def __init__(self, registry: Registry, policy_engine: PolicyEngine) -> None:
        self._registry = registry
        self._policy_engine = policy_engine

    def dispatch(
        self,
        call: ToolCall,
        runtime: object,
        assume_yes: bool = False,
        approval_resolver: Optional[ApprovalResolver] = None,
    ) -> DispatchResult:
        tool = self._registry.get_tool(call.tool_name)
        if tool is None:
            return DispatchResult(
                result=ToolResult(
                    call_id=call.call_id,
                    ok=False,
                    error="unknown_tool",
                    output={"tool_name": call.tool_name},
                ),
                blocked=True,
            )

        policy_result = self._policy_engine.evaluate(call)
        if not policy_result.allow:
            self._emit_if_possible(runtime, "tool.blocked", {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "reason": policy_result.reason,
                "risk_tier": policy_result.risk_tier,
            })
            return DispatchResult(
                result=ToolResult(
                    call_id=call.call_id,
                    ok=False,
                    error=policy_result.reason,
                    output={"risk_tier": policy_result.risk_tier},
                ),
                blocked=True,
            )

        if policy_result.requires_approval and not assume_yes:
            self._emit_if_possible(runtime, "approval.requested", {
                "call_id": call.call_id,
                "tool_name": call.tool_name,
                "risk_tier": policy_result.risk_tier,
            })

            if approval_resolver is None:
                self._emit_if_possible(runtime, "approval.denied", {
                    "call_id": call.call_id,
                    "reason": "approval_required_non_interactive",
                })
                return DispatchResult(
                    result=ToolResult(
                        call_id=call.call_id,
                        ok=False,
                        error="approval_required",
                        output={"risk_tier": policy_result.risk_tier},
                    ),
                    blocked=True,
                )

            decision = approval_resolver(call, policy_result.risk_tier)
            if decision.persist_policy:
                runtime.approval_store.set_policy(
                    call.tool_name,
                    policy_result.risk_tier,
                    decision.persist_policy,
                )

            if not decision.allow:
                self._emit_if_possible(runtime, "approval.denied", {
                    "call_id": call.call_id,
                    "reason": decision.reason or "user_denied",
                })
                self._emit_if_possible(runtime, "tool.blocked", {
                    "call_id": call.call_id,
                    "tool_name": call.tool_name,
                    "reason": decision.reason or "user_denied",
                    "risk_tier": policy_result.risk_tier,
                })
                return DispatchResult(
                    result=ToolResult(
                        call_id=call.call_id,
                        ok=False,
                        error=decision.reason or "approval_denied",
                        output={"risk_tier": policy_result.risk_tier},
                    ),
                    blocked=True,
                )

            self._emit_if_possible(runtime, "approval.granted", {
                "call_id": call.call_id,
                "reason": decision.reason or "user_granted",
            })

        token = DISPATCH_ACTIVE.set(True)
        try:
            result = tool.execute(call, runtime)
        finally:
            DISPATCH_ACTIVE.reset(token)

        return DispatchResult(result=result, blocked=False)

    @staticmethod
    def _emit_if_possible(runtime: object, event_type: str, payload: dict) -> None:
        emit = getattr(runtime, "emit", None)
        if callable(emit):
            emit(event_type, payload)
