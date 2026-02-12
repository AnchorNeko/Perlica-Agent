"""Structured debug log writer with size-based rotation and redaction."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from perlica.kernel.types import EventEnvelope, now_ms


_REDACTED = "***REDACTED***"
_SENSITIVE_KEY_RE = re.compile(
    r"(password|secret|token|authorization|cookie|api[_-]?key|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+([^\s,;]+)")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|token|secret|authorization|cookie|private[_-]?key)\b\s*[:=]\s*([^\s,;]+)"
)
_SK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{8,}\b")


class DebugLogWriter:
    """Best-effort JSONL debug log writer with rotation."""

    def __init__(
        self,
        *,
        logs_dir: Path,
        enabled: bool,
        log_format: str = "jsonl",
        max_file_bytes: int = 10 * 1024 * 1024,
        max_files: int = 5,
        redaction: str = "default",
    ) -> None:
        self._logs_dir = Path(logs_dir)
        self._enabled = bool(enabled)
        self._log_format = "jsonl" if str(log_format).strip().lower() == "jsonl" else "jsonl"
        self._max_file_bytes = max(1, int(max_file_bytes or 0))
        self._max_files = max(1, int(max_files or 0))
        self._redaction = str(redaction or "default").strip().lower()
        if self._redaction not in {"none", "default", "strict"}:
            self._redaction = "default"
        self._write_errors = 0
        self._lock = threading.Lock()
        if self._enabled:
            try:
                self._logs_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                self._write_errors += 1

    @property
    def active_log_file(self) -> Path:
        return self._logs_dir / "debug.log.jsonl"

    def write_event(self, event: EventEnvelope) -> None:
        self.write_entry(
            level="info",
            component="runtime",
            kind="event",
            context_id=event.context_id,
            conversation_id=event.conversation_id,
            run_id=event.run_id,
            trace_id=event.trace_id,
            event_type=event.event_type,
            message="event:{0}".format(event.event_type),
            data={
                "actor": event.actor,
                "node_id": event.node_id,
                "parent_node_id": event.parent_node_id,
                "idempotency_key": event.idempotency_key,
                "causation_id": event.causation_id,
                "correlation_id": event.correlation_id,
                "payload": event.payload,
                "meta": event.meta,
            },
            ts_ms=event.ts_ms,
        )

    def write_entry(
        self,
        *,
        level: str,
        component: str,
        kind: str,
        context_id: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        event_type: Optional[str] = None,
        ts_ms: Optional[int] = None,
    ) -> None:
        if not self._enabled:
            return

        record = {
            "ts_ms": int(ts_ms if ts_ms is not None else now_ms()),
            "level": str(level or "info"),
            "component": str(component or "runtime"),
            "kind": str(kind or "diagnostic"),
            "context_id": str(context_id or ""),
            "conversation_id": str(conversation_id or ""),
            "run_id": str(run_id or ""),
            "trace_id": str(trace_id or ""),
            "event_type": str(event_type or ""),
            "message": str(message or ""),
            "data": dict(data or {}),
        }

        if self._redaction != "none":
            record["message"] = self._redact_text(record["message"])
            if self._redaction == "strict":
                record["data"] = self._strict_redact(record["data"])
            else:
                record["data"] = self._redact_payload(record["data"])

        with self._lock:
            try:
                line = json.dumps(
                    record,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    default=str,
                )
                payload = (line + "\n").encode("utf-8")
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed_locked(len(payload))
                with self.active_log_file.open("ab") as fp:
                    fp.write(payload)
            except Exception:
                self._write_errors += 1

    def status(self) -> Dict[str, Any]:
        with self._lock:
            if not self._enabled:
                return {
                    "logs_enabled": False,
                    "logs_dir": str(self._logs_dir),
                    "logs_active_file": str(self.active_log_file),
                    "logs_active_size_bytes": 0,
                    "logs_max_file_bytes": self._max_file_bytes,
                    "logs_max_files": self._max_files,
                    "logs_total_size_bytes": 0,
                    "logs_rotated_files": [],
                    "logs_write_errors": self._write_errors,
                }

            active = self.active_log_file
            active_size = active.stat().st_size if active.exists() else 0
            rotated = []
            total_size = int(active_size)
            for index in range(1, self._max_files + 1):
                path = self._rotated_file(index)
                if not path.exists():
                    continue
                rotated.append(str(path))
                total_size += int(path.stat().st_size)

            return {
                "logs_enabled": True,
                "logs_dir": str(self._logs_dir),
                "logs_active_file": str(active),
                "logs_active_size_bytes": int(active_size),
                "logs_max_file_bytes": self._max_file_bytes,
                "logs_max_files": self._max_files,
                "logs_total_size_bytes": int(total_size),
                "logs_rotated_files": rotated,
                "logs_write_errors": int(self._write_errors),
            }

    def close(self) -> None:
        # No persistent file handle is kept; close is a no-op for API symmetry.
        return

    def _rotate_if_needed_locked(self, incoming_size: int) -> None:
        current_size = 0
        if self.active_log_file.exists():
            current_size = int(self.active_log_file.stat().st_size)
        if current_size + int(incoming_size) <= self._max_file_bytes:
            return
        self._rotate_locked()

    def _rotate_locked(self) -> None:
        oldest = self._rotated_file(self._max_files)
        oldest.unlink(missing_ok=True)

        for index in range(self._max_files - 1, 0, -1):
            src = self._rotated_file(index)
            dst = self._rotated_file(index + 1)
            if not src.exists():
                continue
            src.replace(dst)

        if self.active_log_file.exists():
            self.active_log_file.replace(self._rotated_file(1))

    def _rotated_file(self, index: int) -> Path:
        return Path("{0}.{1}".format(self.active_log_file, index))

    def _redact_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if _SENSITIVE_KEY_RE.search(key_text):
                    out[key] = _REDACTED
                else:
                    out[key] = self._redact_payload(item)
            return out
        if isinstance(value, list):
            return [self._redact_payload(item) for item in value]
        if isinstance(value, str):
            return self._redact_text(value)
        return value

    def _strict_redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if _SENSITIVE_KEY_RE.search(key_text):
                    out[key] = _REDACTED
                    continue
                if isinstance(item, (dict, list)):
                    out[key] = self._strict_redact(item)
                    continue
                out[key] = _REDACTED
            return out
        if isinstance(value, list):
            return [self._strict_redact(item) for item in value]
        return _REDACTED

    @staticmethod
    def _redact_text(text: str) -> str:
        if not text:
            return text
        masked = _BEARER_RE.sub("Bearer {0}".format(_REDACTED), text)
        masked = _KEY_VALUE_RE.sub(
            lambda m: "{0}={1}".format(m.group(1), _REDACTED),
            masked,
        )
        masked = _SK_KEY_RE.sub(_REDACTED, masked)
        return masked
