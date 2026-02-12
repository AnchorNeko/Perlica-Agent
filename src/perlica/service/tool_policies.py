"""Shared service helpers for tool policy inspection and updates."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from perlica.kernel.policy_engine import (
    APPROVAL_ALWAYS_ALLOW,
    APPROVAL_ALWAYS_DENY,
)

RISK_TIERS: Sequence[str] = ("low", "medium", "high")


def list_tool_policy_lines(runtime: object) -> List[str]:
    tool_ids = _tool_ids(runtime)
    if not tool_ids:
        return ["当前未注册可调用工具。"]

    lines: List[str] = [
        "工具权限状态 (Tool Access):",
        "说明：allow=始终允许，ask=每次确认，deny=始终拒绝。",
    ]
    for tool_name in tool_ids:
        states = {
            risk: _policy_state(runtime, tool_name, risk)
            for risk in RISK_TIERS
        }
        suffix = ""
        if tool_name == "shell.exec":
            suffix = " note=high-risk patterns are hard blocked"
        lines.append(
            "{0} low={1} medium={2} high={3}{4}".format(
                tool_name,
                states["low"],
                states["medium"],
                states["high"],
                suffix,
            )
        )
    return lines


def apply_tool_policy(
    runtime: object,
    *,
    allow: bool,
    tool_name: Optional[str],
    apply_all: bool,
    risk: Optional[str] = None,
) -> Dict[str, object]:
    risks = _resolve_risks(risk)
    tools = _resolve_tools(runtime, tool_name=tool_name, apply_all=apply_all)

    policy = APPROVAL_ALWAYS_ALLOW if allow else APPROVAL_ALWAYS_DENY
    approval_store = getattr(runtime, "approval_store")
    for name in tools:
        for tier in risks:
            approval_store.set_policy(name, tier, policy)

    return {
        "policy": policy,
        "tools": tools,
        "risks": list(risks),
        "updated": len(tools) * len(risks),
    }


def _resolve_risks(risk: Optional[str]) -> List[str]:
    if risk is None:
        return list(RISK_TIERS)
    normalized = str(risk).strip().lower()
    if normalized not in RISK_TIERS:
        raise ValueError(
            "risk 必须是 low|medium|high (risk must be low|medium|high), got: {0}".format(
                risk
            )
        )
    return [normalized]


def _resolve_tools(runtime: object, *, tool_name: Optional[str], apply_all: bool) -> List[str]:
    tools = _tool_ids(runtime)
    if apply_all:
        return tools
    normalized = str(tool_name or "").strip()
    if not normalized:
        raise ValueError("请提供工具名，或使用 --all。")
    if normalized not in tools:
        candidates = [name for name in tools if name.startswith(normalized)]
        suffix = ""
        if candidates:
            suffix = " candidates: {0}".format(", ".join(candidates[:8]))
        raise ValueError("未知工具：{0}.{1}".format(normalized, suffix))
    return [normalized]


def _tool_ids(runtime: object) -> List[str]:
    registry = getattr(runtime, "registry", None)
    if registry is None:
        return []
    list_ids = getattr(registry, "list_tool_ids", None)
    if not callable(list_ids):
        return []
    ids = [str(name) for name in list_ids()]
    return sorted(ids)


def _policy_state(runtime: object, tool_name: str, risk_tier: str) -> str:
    approval_store = getattr(runtime, "approval_store", None)
    if approval_store is None:
        return "ask"
    get_policy = getattr(approval_store, "get_policy", None)
    if not callable(get_policy):
        return "ask"
    policy = get_policy(tool_name, risk_tier)
    if policy == APPROVAL_ALWAYS_ALLOW:
        return "allow"
    if policy == APPROVAL_ALWAYS_DENY:
        return "deny"
    return "ask"
