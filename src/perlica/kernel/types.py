"""Core typed contracts used across runtime, providers, and tools."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol


def now_ms() -> int:
    return int(time.time() * 1000)


def new_id(prefix: str) -> str:
    return "{0}_{1}".format(prefix, uuid.uuid4().hex)


@dataclass
class ToolCall:
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    risk_tier: str = "low"


@dataclass
class ToolResult:
    call_id: str
    ok: bool
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    artifacts: List[str] = field(default_factory=list)

    def as_message(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
        }


@dataclass
class LLMRequest:
    conversation_id: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    assistant_text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: Dict[str, Any] = field(
        default_factory=lambda: {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "context_window": 0,
            "raw_usage": {},
        }
    )
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMCallUsage:
    call_index: int
    provider_id: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    context_window: int = 0
    raw_usage: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UsageTotals:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class MiddlewareDecision:
    allow: bool
    reason: Optional[str] = None
    requires_approval: bool = False
    mutated_call: Optional[ToolCall] = None


@dataclass
class EventEnvelope:
    event_id: str
    event_type: str
    schema_version: int
    ts_ms: int
    context_id: str
    conversation_id: str
    node_id: str
    parent_node_id: Optional[str]
    actor: str
    run_id: str
    trace_id: str
    causation_id: Optional[str]
    correlation_id: Optional[str]
    idempotency_key: Optional[str]
    payload: Dict[str, Any]
    meta: Dict[str, Any]
    prev_event_hash: Optional[str] = None
    event_hash: Optional[str] = None


class RuntimeProtocol(Protocol):
    context_id: str
    context_dir: Any
    workspace_dir: Any
    config: Dict[str, Any]

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        conversation_id: Optional[str] = None,
        parent_node_id: Optional[str] = None,
        actor: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        ...


class LLMProvider(Protocol):
    provider_id: str

    def generate(self, req: LLMRequest) -> LLMResponse:
        ...


class Tool(Protocol):
    tool_name: str

    def execute(self, call: ToolCall, runtime: RuntimeProtocol) -> ToolResult:
        ...


class Middleware(Protocol):
    middleware_id: str

    def before_tool(self, call: ToolCall, context: Dict[str, Any]) -> MiddlewareDecision:
        ...

    def after_tool(self, call: ToolCall, result: ToolResult, context: Dict[str, Any]) -> ToolResult:
        ...


class SkillPack(Protocol):
    pack_id: str

    def list_skills(self) -> List[Dict[str, Any]]:
        ...


EventHandler = Callable[[EventEnvelope], None]


def coerce_tool_calls(raw_calls: Iterable[Dict[str, Any]]) -> List[ToolCall]:
    tool_calls: List[ToolCall] = []
    for item in raw_calls:
        tool_calls.append(
            ToolCall(
                call_id=str(item.get("call_id") or new_id("call")),
                tool_name=str(item.get("tool_name") or ""),
                arguments=dict(item.get("arguments") or {}),
                risk_tier=str(item.get("risk_tier") or "low"),
            )
        )
    return tool_calls
