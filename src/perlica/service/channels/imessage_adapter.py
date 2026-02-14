"""iMessage channel adapter backed by external `imsg` CLI."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from perlica.kernel.types import now_ms
from perlica.service.types import (
    ChannelBootstrapResult,
    ChannelHealthSnapshot,
    ChannelInboundMessage,
    ChannelOutboundMessage,
    ChannelTelemetryEvent,
)


class IMessageChannelAdapter:
    """Bridge `imsg` subprocess events into normalized channel messages."""

    channel_name = "imessage"

    def __init__(
        self,
        binary: str = "imsg",
        listen_args: Optional[Sequence[str]] = None,
        send_args: Optional[Sequence[str]] = None,
    ) -> None:
        self._binary = binary
        self._listen_args = tuple(listen_args or ("watch", "--json"))
        self._send_args = tuple(send_args or ("send",))

        self._process: Optional[subprocess.Popen[str]] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._control_lock = threading.Lock()
        self._callback: Optional[Callable[[ChannelInboundMessage], None]] = None
        self._telemetry_sink: Optional[Callable[[ChannelTelemetryEvent], None]] = None
        self._chat_scope: Optional[str] = None

        self._health_lock = threading.Lock()
        self._health = ChannelHealthSnapshot(
            listener_state="stopped",
            listener_alive=False,
        )

    def probe(self) -> None:
        path = shutil.which(self._binary)
        if not path:
            raise RuntimeError(
                "未找到 iMessage 渠道可执行文件：{0}，请确认已安装并加入 PATH。".format(
                    self._binary
                )
            )

    def bootstrap(self) -> ChannelBootstrapResult:
        """Check CLI + permission readiness; open macOS Settings when blocked."""

        self.probe()
        completed = subprocess.run(
            [self._binary, "chats"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return ChannelBootstrapResult(
                channel=self.channel_name,
                ok=True,
                message="iMessage 渠道初始化成功。",
            )

        stderr = str(completed.stderr or "").strip()
        stdout = str(completed.stdout or "").strip()
        detail = stderr or stdout or "unknown error"
        needs_permission = _looks_like_permission_error(detail)
        opened_settings = False
        if needs_permission:
            opened_settings = _open_macos_privacy_settings()
        return ChannelBootstrapResult(
            channel=self.channel_name,
            ok=False,
            message=(
                "iMessage 权限未就绪，请在系统设置授权后重试。"
                if needs_permission
                else "iMessage 初始化失败：{0}".format(detail)
            ),
            needs_user_action=needs_permission,
            opened_system_settings=opened_settings,
        )

    def set_telemetry_sink(
        self,
        sink: Optional[Callable[[ChannelTelemetryEvent], None]],
    ) -> None:
        self._telemetry_sink = sink

    def set_chat_scope(self, chat_id: Optional[str]) -> None:
        normalized = str(chat_id).strip() if chat_id is not None and str(chat_id).strip() else None

        with self._control_lock:
            if normalized == self._chat_scope:
                return
            self._chat_scope = normalized
            callback = self._callback
            running = bool(self._listener_thread and self._listener_thread.is_alive())

        if running and callback is not None:
            self.stop_listener()
            self.start_listener(callback)

    def poll_for_pairing_code(
        self,
        pairing_code: str,
        *,
        max_chats: int = 5,
    ) -> Optional[ChannelInboundMessage]:
        code = str(pairing_code or "").strip()
        if not code:
            return None
        target = "/pair {0}".format(code)

        for chat_id, contact_hint in self._list_recent_chats(max_chats=max_chats):
            messages = self._history_messages(chat_id=chat_id, contact_hint=contact_hint, limit=8)
            for message in reversed(messages):
                if str(message.text or "").strip() == target:
                    return message
        return None

    def poll_recent_messages(
        self,
        *,
        contact_id: str,
        chat_id: Optional[str],
        since_ts_ms: Optional[int],
        max_chats: int = 8,
        limit_per_chat: int = 8,
    ) -> List[ChannelInboundMessage]:
        target_contact = self.normalize_contact_id(contact_id)
        if not target_contact:
            return []

        chats: List[Tuple[str, Optional[str]]] = []
        if chat_id:
            chats.append((str(chat_id), contact_id))
        chats.extend(self._list_recent_chats(max_chats=max_chats))

        dedupe = set()
        merged: List[ChannelInboundMessage] = []
        for candidate_chat_id, contact_hint in chats:
            key = str(candidate_chat_id or "").strip()
            if not key:
                continue
            if key in dedupe:
                continue
            dedupe.add(key)
            history = self._history_messages(
                chat_id=key,
                contact_hint=contact_hint,
                limit=max(4, int(limit_per_chat)),
            )
            for message in history:
                same_contact = self.normalize_contact_id(message.contact_id) == target_contact
                if not same_contact:
                    continue
                if since_ts_ms is not None and int(message.ts_ms or 0) < int(since_ts_ms):
                    continue
                merged.append(message)

        merged.sort(key=lambda item: (int(item.ts_ms or 0), str(item.event_id or "")))
        unique: List[ChannelInboundMessage] = []
        seen = set()
        for message in merged:
            dedupe_key = (
                str(message.event_id or ""),
                str(message.contact_id or ""),
                str(message.chat_id or ""),
                str(message.text or ""),
                "1" if bool(message.is_from_me) else "0",
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            unique.append(message)
        return unique

    def health_snapshot(self) -> ChannelHealthSnapshot:
        with self._health_lock:
            return ChannelHealthSnapshot(
                listener_state=self._health.listener_state,
                listener_alive=self._health.listener_alive,
                raw_inbound_count=self._health.raw_inbound_count,
                raw_outbound_count=self._health.raw_outbound_count,
                raw_line_count=self._health.raw_line_count,
                last_inbound_at_ms=self._health.last_inbound_at_ms,
                last_outbound_at_ms=self._health.last_outbound_at_ms,
                last_raw_line_preview=self._health.last_raw_line_preview,
                last_error=self._health.last_error,
            )

    def start_listener(self, callback: Callable[[ChannelInboundMessage], None]) -> None:
        with self._control_lock:
            if self._listener_thread and self._listener_thread.is_alive():
                self._callback = callback
                return
            self._callback = callback

        self.probe()
        self._stop_event.clear()
        self._set_health(listener_state="starting", listener_alive=True, last_error=None)
        self._emit_telemetry(
            event_type="listener.starting",
            direction="internal",
            text="starting imsg watch",
            payload={"chat_scope": self._chat_scope or ""},
        )

        cmd = self._build_listen_command()
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def read_stdout() -> None:
            process = self._process
            callback_fn = self._callback
            if process is None or process.stdout is None or callback_fn is None:
                return

            for raw_line in process.stdout:
                if self._stop_event.is_set():
                    break
                self._mark_raw_line(raw_line)
                messages = self._parse_inbound_line(raw_line)
                if not messages:
                    continue
                for message in messages:
                    self._mark_inbound_received()
                    self._emit_telemetry(
                        event_type="inbound.message",
                        direction="inbound",
                        text=message.text,
                        payload={
                            "chat_id": message.chat_id or "",
                            "contact_id": message.contact_id,
                            "is_from_me": bool(message.is_from_me),
                        },
                    )
                    callback_fn(message)

        def drain_stderr() -> None:
            process = self._process
            callback_fn = self._callback
            if process is None or process.stderr is None:
                return

            for line in process.stderr:
                if self._stop_event.is_set():
                    break
                raw = str(line or "")
                if not raw.strip():
                    continue

                # Some `imsg` builds may emit JSON events on stderr. Accept both
                # stdout/stderr as potential event sources to avoid missing inbound
                # traffic in non-interactive subprocess mode.
                messages = self._parse_inbound_line(raw)
                if messages and callback_fn is not None:
                    self._mark_raw_line(raw)
                    for message in messages:
                        self._mark_inbound_received()
                        self._emit_telemetry(
                            event_type="inbound.message",
                            direction="inbound",
                            text=message.text,
                            payload={
                                "chat_id": message.chat_id or "",
                                "contact_id": message.contact_id,
                                "is_from_me": bool(message.is_from_me),
                                "source": "stderr",
                            },
                        )
                        callback_fn(message)
                    continue

                self._set_health(last_error=raw.strip())

        def monitor_process() -> None:
            process = self._process
            if process is None:
                return

            while not self._stop_event.is_set():
                return_code = process.poll()
                if return_code is None:
                    time.sleep(0.2)
                    continue

                if not self._stop_event.is_set():
                    self._set_health(
                        listener_state="error",
                        listener_alive=False,
                        last_error="imsg watch exited with code {0}".format(return_code),
                    )
                    self._emit_telemetry(
                        event_type="listener.exited",
                        direction="internal",
                        text="imsg watch exited with code {0}".format(return_code),
                        payload={"return_code": return_code},
                    )
                return

        self._listener_thread = threading.Thread(
            target=read_stdout,
            daemon=True,
            name="perlica-imessage-listener",
        )
        self._stderr_thread = threading.Thread(
            target=drain_stderr,
            daemon=True,
            name="perlica-imessage-stderr",
        )
        self._monitor_thread = threading.Thread(
            target=monitor_process,
            daemon=True,
            name="perlica-imessage-monitor",
        )

        self._listener_thread.start()
        self._stderr_thread.start()
        self._monitor_thread.start()

        self._set_health(listener_state="running", listener_alive=True)
        self._emit_telemetry(
            event_type="listener.running",
            direction="internal",
            text="imsg watch running",
            payload={"chat_scope": self._chat_scope or ""},
        )

    def stop_listener(self) -> None:
        had_listener = bool(self._process) or any(
            thread is not None and thread.is_alive()
            for thread in (self._listener_thread, self._stderr_thread, self._monitor_thread)
        )
        self._stop_event.set()

        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

        for thread in (self._listener_thread, self._stderr_thread, self._monitor_thread):
            if thread and thread.is_alive():
                thread.join(timeout=3)

        with self._control_lock:
            self._process = None
            self._listener_thread = None
            self._stderr_thread = None
            self._monitor_thread = None

        self._set_health(listener_state="stopped", listener_alive=False)
        if had_listener:
            self._emit_telemetry(
                event_type="listener.stopped",
                direction="internal",
                text="imsg watch stopped",
                payload={},
            )

    def send_message(self, outbound: ChannelOutboundMessage) -> None:
        self.probe()
        text = str(outbound.text or "")
        variants: List[List[str]] = []
        if outbound.chat_id:
            variants.append(
                [
                    self._binary,
                    *self._send_args,
                    "--chat-id",
                    str(outbound.chat_id),
                    "--text",
                    text,
                ]
            )
        if outbound.contact_id:
            variants.append(
                [
                    self._binary,
                    *self._send_args,
                    "--to",
                    str(outbound.contact_id),
                    "--text",
                    text,
                ]
            )
        if not variants:
            raise RuntimeError("iMessage 发送失败：缺少 contact_id/chat_id")

        errors: List[str] = []
        for cmd in variants:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode == 0:
                self._mark_outbound_sent()
                self._emit_telemetry(
                    event_type="outbound.sent",
                    direction="outbound",
                    text=text,
                    payload={
                        "chat_id": str(outbound.chat_id or ""),
                        "contact_id": str(outbound.contact_id or ""),
                    },
                )
                return

            stdout = str(completed.stdout or "").strip()
            stderr = str(completed.stderr or "").strip()
            detail = stderr or stdout or "unknown error"
            errors.append(
                "cmd={0} rc={1} detail={2}".format(
                    " ".join(cmd),
                    completed.returncode,
                    detail,
                )
            )

        merged = " | ".join(errors)
        self._set_health(last_error=merged or "imsg send failed")
        self._emit_telemetry(
            event_type="outbound.error",
            direction="outbound",
            text=merged or "unknown error",
            payload={},
        )
        raise RuntimeError("iMessage 发送失败：{0}".format(merged or "unknown error"))

    def normalize_contact_id(self, raw: str) -> str:
        value = str(raw or "").strip().lower()
        if not value:
            return ""

        for prefix in ("tel:", "sms:", "imessage:", "mailto:"):
            if value.startswith(prefix):
                value = value[len(prefix) :].strip()
                break

        if "@" in value:
            return value

        cleaned = re.sub(r"[\s\-\(\)\.\u00a0]+", "", value)
        if not cleaned:
            return value

        if cleaned.startswith("+"):
            digits = re.sub(r"\D+", "", cleaned[1:])
            return "+{0}".format(digits) if digits else "+"

        digits = re.sub(r"\D+", "", cleaned)
        return digits or cleaned

    def _build_listen_command(self) -> List[str]:
        cmd: List[str] = [self._binary, *self._listen_args]
        chat_scope = self._chat_scope

        has_chat_scope = any(token in {"--chat-id", "--chat", "--chat-guid", "--chat-identifier"} for token in cmd)
        if chat_scope and not has_chat_scope:
            cmd.extend(["--chat-id", chat_scope])
        return cmd

    def _list_recent_chats(self, *, max_chats: int = 5) -> List[Tuple[str, Optional[str]]]:
        try:
            completed = subprocess.run(
                [self._binary, "chats"],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return []

        if completed.returncode != 0:
            stderr = str(completed.stderr or "").strip()
            if stderr:
                self._set_health(last_error=stderr)
            return []

        output = str(completed.stdout or "")
        return self._parse_chats_output(output, max_chats=max_chats)

    def _parse_chats_output(self, text: str, *, max_chats: int) -> List[Tuple[str, Optional[str]]]:
        stripped = str(text or "").strip()
        if not stripped:
            return []

        results: List[Tuple[str, Optional[str]]] = []

        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    candidate = self._first_string(item.get("id"), item.get("chat_id"), item.get("rowid"))
                    if candidate:
                        contact_hint = self._first_string(
                            item.get("contact"),
                            item.get("sender"),
                            item.get("participant"),
                            item.get("participants", [None])[0] if isinstance(item.get("participants"), list) else None,
                        )
                        results.append((candidate, contact_hint))
        if isinstance(parsed, dict):
            for key in ("chats", "items", "data"):
                data = parsed.get(key)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            candidate = self._first_string(item.get("id"), item.get("chat_id"), item.get("rowid"))
                            if candidate:
                                contact_hint = self._first_string(
                                    item.get("contact"),
                                    item.get("sender"),
                                    item.get("participant"),
                                )
                                results.append((candidate, contact_hint))

        if results:
            return results[: max(1, int(max_chats))]

        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            # Plain text style: [3]  (1023620928@qq.com) last=...
            match = re.match(r"^\[(\d+)\]\s+\(([^)]*)\)", line)
            if match:
                results.append((str(match.group(1)), str(match.group(2)).strip() or None))
                continue
            match = re.match(r"^\[(\d+)\]", line)
            if match:
                results.append((str(match.group(1)), None))

        return results[: max(1, int(max_chats))]

    def _history_messages(
        self,
        *,
        chat_id: str,
        contact_hint: Optional[str],
        limit: int,
    ) -> List[ChannelInboundMessage]:
        try:
            completed = subprocess.run(
                [
                    self._binary,
                    "history",
                    "--chat-id",
                    str(chat_id),
                    "--limit",
                    str(max(1, int(limit))),
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return []

        if completed.returncode != 0:
            stderr = str(completed.stderr or "").strip()
            if stderr:
                self._set_health(last_error=stderr)
            return []

        output = str(completed.stdout or "").strip()
        if not output:
            return []

        payload_dicts: List[Dict[str, Any]] = []

        try:
            parsed = json.loads(output)
            payload_dicts.extend(self._collect_dict_payloads(parsed))
        except Exception:
            for line in output.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed_line = json.loads(stripped)
                except Exception:
                    continue
                payload_dicts.extend(self._collect_dict_payloads(parsed_line))

        messages: List[ChannelInboundMessage] = []
        for payload in payload_dicts:
            candidate = self._payload_to_message(
                payload,
                fallback_contact=contact_hint,
                fallback_chat_id=str(chat_id),
            )
            if candidate is not None:
                messages.append(candidate)
        return messages

    def _collect_dict_payloads(self, value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, dict):
            results: List[Dict[str, Any]] = [value]
            for nested_key in ("messages", "items", "data", "payload", "event", "message"):
                nested = value.get(nested_key)
                results.extend(self._collect_dict_payloads(nested))
            return results
        if isinstance(value, list):
            results: List[Dict[str, Any]] = []
            for item in value:
                results.extend(self._collect_dict_payloads(item))
            return results
        return []

    def _parse_inbound_line(self, raw_line: str) -> List[ChannelInboundMessage]:
        line = str(raw_line or "").strip()
        if not line:
            return []

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return []

        candidates: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    candidates.extend(self._expand_payload_candidates(item))
        elif isinstance(payload, dict):
            candidates.extend(self._expand_payload_candidates(payload))
        else:
            return []

        messages: List[ChannelInboundMessage] = []
        seen_keys = set()
        for candidate in candidates:
            message = self._payload_to_message(candidate)
            if message is None:
                continue
            dedupe_key = (
                message.event_id or "",
                message.contact_id,
                message.chat_id or "",
                message.text,
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            messages.append(message)
        return messages

    def _expand_payload_candidates(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = [payload]

        for key in ("message", "data", "payload", "event", "item"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                merged = dict(payload)
                merged.update(nested)
                candidates.append(merged)

        for key in ("messages", "events", "items", "entries"):
            nested_list = payload.get(key)
            if not isinstance(nested_list, list):
                continue
            for item in nested_list:
                if isinstance(item, dict):
                    merged = dict(payload)
                    merged.update(item)
                    candidates.append(merged)

        return candidates

    def _payload_to_message(
        self,
        payload: Dict[str, Any],
        *,
        fallback_contact: Optional[str] = None,
        fallback_chat_id: Optional[str] = None,
    ) -> Optional[ChannelInboundMessage]:
        text = self._extract_text(payload)
        contact = self._extract_contact(payload)
        if not contact:
            contact = fallback_contact
        if not text or not contact:
            return None

        chat_id = self._extract_chat_id(payload)
        if chat_id is None:
            chat_id = fallback_chat_id
        event_id = self._extract_event_id(payload)

        return ChannelInboundMessage(
            channel=self.channel_name,
            text=text,
            contact_id=self.normalize_contact_id(contact),
            chat_id=chat_id,
            event_id=event_id,
            is_from_me=self._extract_from_me(payload),
            ts_ms=self._coerce_timestamp(payload),
            raw=dict(payload),
        )

    def _extract_text(self, payload: Dict[str, Any]) -> Optional[str]:
        value = self._value_by_paths(
            payload,
            (
                ("text",),
                ("body",),
                ("message_text",),
                ("plainText",),
                ("content", "text"),
                ("message", "text"),
                ("message", "body"),
                ("data", "text"),
                ("payload", "text"),
            ),
        )
        if value:
            return value
        return self._deep_search_first_string(payload, {"text", "body", "message_text", "plaintext"})

    def _extract_contact(self, payload: Dict[str, Any]) -> Optional[str]:
        value = self._value_by_paths(
            payload,
            (
                ("contact_id",),
                ("from",),
                ("from_handle",),
                ("sender",),
                ("handle",),
                ("address",),
                ("participant",),
                ("from", "id"),
                ("from", "address"),
                ("sender", "id"),
                ("sender", "address"),
                ("handle", "id"),
                ("handle", "address"),
                ("participant", "id"),
                ("participant", "address"),
                ("participants", 0),
                ("participants", 0, "id"),
                ("participants", 0, "address"),
                ("message", "from", "id"),
                ("message", "sender", "id"),
                ("message", "handle", "id"),
                ("data", "from", "id"),
                ("payload", "from", "id"),
            ),
        )
        if value:
            return value
        return self._deep_search_first_string(payload, {"contact_id", "handle", "address", "sender", "from"})

    def _extract_chat_id(self, payload: Dict[str, Any]) -> Optional[str]:
        return self._value_by_paths(
            payload,
            (
                ("chat_id",),
                ("chatId",),
                ("chat",),
                ("chat_rowid",),
                ("chat", "id"),
                ("chat", "rowid"),
                ("chat", "identifier"),
                ("message", "chat", "id"),
                ("message", "chat", "rowid"),
            ),
        )

    def _extract_event_id(self, payload: Dict[str, Any]) -> str:
        value = self._value_by_paths(
            payload,
            (
                ("event_id",),
                ("id",),
                ("guid",),
                ("message_id",),
                ("rowid",),
                ("message", "id"),
                ("message", "guid"),
                ("message", "rowid"),
            ),
        )
        return value or ""

    def _extract_from_me(self, payload: Dict[str, Any]) -> bool:
        values = self._values_by_paths(
            payload,
            (
                ("is_from_me",),
                ("isFromMe",),
                ("from_me",),
                ("fromMe",),
                ("message", "is_from_me"),
                ("message", "isFromMe"),
                ("message", "fromMe"),
            ),
        )
        parsed = self._coerce_optional_bool(*values)
        if parsed is None:
            # Strict mode: unknown/missing means "do not consume as remote inbound".
            return True
        return parsed

    def _value_by_paths(self, payload: Dict[str, Any], paths: Sequence[Tuple[Any, ...]]) -> Optional[str]:
        for path in paths:
            value = self._extract_path(payload, path)
            if value is None:
                continue
            if isinstance(value, dict):
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    def _values_by_paths(self, payload: Dict[str, Any], paths: Sequence[Tuple[Any, ...]]) -> List[Any]:
        values: List[Any] = []
        for path in paths:
            value = self._extract_path(payload, path)
            if value is not None:
                values.append(value)
        return values

    def _extract_path(self, payload: Any, path: Sequence[Any]) -> Any:
        current = payload
        for part in path:
            if isinstance(part, int):
                if not isinstance(current, list):
                    return None
                if part < 0 or part >= len(current):
                    return None
                current = current[part]
                continue

            if not isinstance(current, dict):
                return None
            if part not in current:
                return None
            current = current[part]
        return current

    def _deep_search_first_string(self, value: Any, key_hints: set) -> Optional[str]:
        stack: List[Tuple[Any, Optional[str]]] = [(value, None)]
        while stack:
            current, key_name = stack.pop()
            if isinstance(current, dict):
                for key, child in current.items():
                    stack.append((child, str(key).lower()))
                continue
            if isinstance(current, list):
                for child in current:
                    stack.append((child, key_name))
                continue
            if key_name is None:
                continue
            if key_name not in key_hints:
                continue
            text = str(current or "").strip()
            if text:
                return text
        return None

    def _mark_raw_line(self, raw_line: str) -> None:
        line = str(raw_line or "").strip()
        if len(line) > 120:
            line = line[:117] + "..."
        with self._health_lock:
            self._health.raw_line_count += 1
            self._health.last_raw_line_preview = line
        self._emit_telemetry(
            event_type="listener.raw_line",
            direction="inbound",
            text=line,
            payload={},
        )

    def _mark_inbound_received(self) -> None:
        with self._health_lock:
            self._health.raw_inbound_count += 1
            self._health.last_inbound_at_ms = now_ms()

    def _mark_outbound_sent(self) -> None:
        with self._health_lock:
            self._health.raw_outbound_count += 1
            self._health.last_outbound_at_ms = now_ms()

    def _set_health(
        self,
        *,
        listener_state: Optional[str] = None,
        listener_alive: Optional[bool] = None,
        last_error: Optional[str] = None,
    ) -> None:
        with self._health_lock:
            if listener_state is not None:
                self._health.listener_state = listener_state
            if listener_alive is not None:
                self._health.listener_alive = bool(listener_alive)
            if last_error is not None:
                cleaned = str(last_error or "").strip()
                self._health.last_error = cleaned or None
                if cleaned:
                    self._emit_telemetry(
                        event_type="listener.error",
                        direction="internal",
                        text=cleaned,
                        payload={},
                    )

    def _emit_telemetry(
        self,
        *,
        event_type: str,
        direction: str,
        text: str,
        payload: Dict[str, Any],
    ) -> None:
        sink = self._telemetry_sink
        if sink is None:
            return
        try:
            sink(
                ChannelTelemetryEvent(
                    channel=self.channel_name,
                    event_type=event_type,
                    direction=direction,
                    text=str(text or ""),
                    payload=dict(payload or {}),
                )
            )
        except Exception:
            return

    @staticmethod
    def _first_string(*values: object) -> Optional[str]:
        for value in values:
            if value is None:
                continue
            if isinstance(value, dict):
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _coerce_optional_bool(*values: object) -> Optional[bool]:
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return value != 0
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "y", "on"}:
                return True
            if text in {"0", "false", "no", "n", "off"}:
                return False
        return None

    @staticmethod
    def _coerce_timestamp(payload: Dict[str, Any]) -> int:
        candidates = (
            payload.get("ts_ms"),
            payload.get("timestamp_ms"),
            payload.get("timestamp"),
            payload.get("ts"),
            payload.get("date"),
            payload.get("created_at"),
            payload.get("createdAt"),
            payload.get("message", {}).get("created_at") if isinstance(payload.get("message"), dict) else None,
            payload.get("message", {}).get("createdAt") if isinstance(payload.get("message"), dict) else None,
        )
        for value in candidates:
            if value is None:
                continue
            numeric = IMessageChannelAdapter._parse_timestamp_value(value)
            if numeric is not None:
                return numeric
        return now_ms()

    @staticmethod
    def _parse_timestamp_value(value: Any) -> Optional[int]:
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None:
            if numeric <= 0:
                return None
            if numeric < 10_000_000_000:
                return numeric * 1000
            return numeric

        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        ts_ms = int(parsed.timestamp() * 1000)
        if ts_ms <= 0:
            return None
        return ts_ms


def _looks_like_permission_error(detail: str) -> bool:
    normalized = str(detail or "").lower()
    checks = (
        "permission",
        "not permitted",
        "operation not permitted",
        "full disk access",
        "access denied",
        "privacy",
    )
    return any(token in normalized for token in checks)


def _open_macos_privacy_settings() -> bool:
    uri = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
    completed = subprocess.run(
        ["open", uri],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0
