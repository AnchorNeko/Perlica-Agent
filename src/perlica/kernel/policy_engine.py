"""Policy engine and approval preference persistence."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from perlica.kernel.types import ToolCall, now_ms

APPROVAL_ALWAYS_ALLOW = "always_allow"
APPROVAL_ALWAYS_DENY = "always_deny"
APPROVAL_ALWAYS_ASK = "always_ask"

HIGH_RISK_PATTERNS = [
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",
]

MEDIUM_RISK_PATTERNS = [
    r"\bsudo\b",
    r"\bchmod\b",
    r"\bchown\b",
    r">\s*/",
]


@dataclass
class PolicyResult:
    allow: bool
    risk_tier: str
    requires_approval: bool = False
    reason: Optional[str] = None


@dataclass
class ApprovalAction:
    allow: bool
    persist_policy: Optional[str] = None
    reason: Optional[str] = None


class ApprovalStore:
    """Stores persistent approval preferences by tool+risk tier."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_prefs (
                tool_name TEXT NOT NULL,
                risk_tier TEXT NOT NULL,
                policy TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (tool_name, risk_tier)
            )
            """
        )
        self._conn.commit()

    def get_policy(self, tool_name: str, risk_tier: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT policy FROM approval_prefs WHERE tool_name = ? AND risk_tier = ?",
            (tool_name, risk_tier),
        ).fetchone()
        if row is None:
            return None
        return str(row["policy"])

    def set_policy(self, tool_name: str, risk_tier: str, policy: str) -> None:
        if policy not in {APPROVAL_ALWAYS_ALLOW, APPROVAL_ALWAYS_DENY, APPROVAL_ALWAYS_ASK}:
            raise ValueError("unknown policy: {0}".format(policy))
        self._conn.execute(
            """
            INSERT INTO approval_prefs (tool_name, risk_tier, policy, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(tool_name, risk_tier)
            DO UPDATE SET policy = excluded.policy, updated_at_ms = excluded.updated_at_ms
            """,
            (tool_name, risk_tier, policy, now_ms()),
        )
        self._conn.commit()

    def list_policies(self) -> List[Dict[str, str]]:
        rows = self._conn.execute(
            "SELECT tool_name, risk_tier, policy FROM approval_prefs ORDER BY tool_name ASC, risk_tier ASC"
        ).fetchall()
        return [
            {
                "tool_name": str(row["tool_name"]),
                "risk_tier": str(row["risk_tier"]),
                "policy": str(row["policy"]),
            }
            for row in rows
        ]

    def reset(self, tool_name: str, risk_tier: str) -> int:
        cursor = self._conn.execute(
            "DELETE FROM approval_prefs WHERE tool_name = ? AND risk_tier = ?",
            (tool_name, risk_tier),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def reset_all(self) -> int:
        cursor = self._conn.execute("DELETE FROM approval_prefs")
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def close(self) -> None:
        self._conn.close()


class PolicyEngine:
    """Computes whether tool execution should proceed or require approval."""

    def __init__(self, approvals: ApprovalStore) -> None:
        self._approvals = approvals

    def infer_risk_tier(self, call: ToolCall) -> str:
        if call.tool_name != "shell.exec":
            return call.risk_tier or "medium"

        cmd = str(call.arguments.get("cmd") or "")
        for pattern in HIGH_RISK_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return "high"

        for pattern in MEDIUM_RISK_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return "medium"

        return "low"

    def evaluate(self, call: ToolCall) -> PolicyResult:
        risk_tier = self.infer_risk_tier(call)

        if risk_tier == "high":
            return PolicyResult(
                allow=False,
                risk_tier=risk_tier,
                requires_approval=False,
                reason="blocked_high_risk_pattern",
            )

        persisted = self._approvals.get_policy(call.tool_name, risk_tier)
        if persisted == APPROVAL_ALWAYS_DENY:
            return PolicyResult(
                allow=False,
                risk_tier=risk_tier,
                requires_approval=False,
                reason="blocked_by_persisted_policy",
            )

        if persisted == APPROVAL_ALWAYS_ALLOW:
            return PolicyResult(
                allow=True,
                risk_tier=risk_tier,
                requires_approval=False,
                reason="allowed_by_persisted_policy",
            )

        # Default posture: ask each time for low/medium side-effectful calls.
        return PolicyResult(
            allow=True,
            risk_tier=risk_tier,
            requires_approval=True,
            reason="approval_required",
        )
