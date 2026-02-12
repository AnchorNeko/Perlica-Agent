"""Typed ACP protocol envelopes and config used by provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ACPClientConfig:
    command: str
    args: List[str] = field(default_factory=list)
    env_allowlist: List[str] = field(default_factory=list)
    connect_timeout_sec: int = 10
    request_timeout_sec: int = 60
    max_retries: int = 2
    backoff: str = "exponential+jitter"
    circuit_breaker_enabled: bool = True


@dataclass
class ACPRequestEnvelope:
    request_id: str
    method: str
    params: Dict[str, Any]

    def as_json(self) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class ACPResponseEnvelope:
    request_id: str
    result: Dict[str, Any]


@dataclass
class ACPErrorEnvelope:
    request_id: str
    code: int
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
