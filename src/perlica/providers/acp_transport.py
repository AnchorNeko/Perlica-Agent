"""ACP stdio transport with timeout/retry-friendly request API."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional

from perlica.providers.acp_types import ACPClientConfig
from perlica.providers.base import ProviderProtocolError, ProviderTransportError


class ACPTransportTimeout(TimeoutError):
    """Raised when one ACP request exceeds request timeout."""


TransportEventSink = Callable[[str, Dict[str, Any]], None]
NotificationSink = Callable[[Dict[str, Any]], None]
NotificationHandler = Callable[[Dict[str, Any]], Optional[Any]]
SideResponseSink = Callable[[Dict[str, Any]], None]


class StdioACPTransport:
    """Line-oriented JSON-RPC transport over stdio."""

    def __init__(
        self,
        config: ACPClientConfig,
        event_sink: Optional[TransportEventSink] = None,
    ) -> None:
        self._config = config
        self._event_sink = event_sink

        self._process: Optional[subprocess.Popen[str]] = None
        self._stdout_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr_lines: Deque[str] = deque(maxlen=40)
        self._io_lock = threading.Lock()
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._closed = False
        self._consumed_response_ids = set()

    def start(self) -> None:
        if self._closed:
            raise ProviderTransportError("acp transport already closed")
        if self._process is not None and self._process.poll() is None:
            return

        command = [self._config.command] + list(self._config.args)
        if not command[0]:
            raise ProviderTransportError("acp adapter command is empty")

        env = self._build_env()
        self._stdout_queue = queue.Queue()
        self._stderr_lines.clear()

        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ProviderTransportError(
                "acp adapter command not found: {0}".format(self._config.command)
            ) from exc
        except OSError as exc:
            raise ProviderTransportError(
                "failed to start acp adapter: {0}".format(exc)
            ) from exc

        if self._process.stdin is None or self._process.stdout is None or self._process.stderr is None:
            self.close()
            raise ProviderTransportError("acp adapter stdio streams unavailable")

        self._stdout_thread = threading.Thread(target=self._read_stdout_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._emit("acp.transport.started", {"command": command})

    def restart(self) -> None:
        self.close()
        self._closed = False
        self._consumed_response_ids = set()
        self.start()

    def request(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        notification_sink: Optional[NotificationSink] = None,
        notification_handler: Optional[NotificationHandler] = None,
        side_response_sink: Optional[SideResponseSink] = None,
    ) -> Dict[str, Any]:
        request_id = str(payload.get("id") or "")
        if not request_id:
            raise ProviderProtocolError("acp request missing id")

        with self._io_lock:
            self.start()
            self._write_payload(payload)

            method = str(payload.get("method") or "").strip()
            timeout_window = int(timeout_sec)
            if method == "session/prompt" or timeout_window <= 0:
                deadline: Optional[float] = None
            else:
                deadline = time.time() + max(1, timeout_window)
            pending_side_ids: set[str] = set()
            main_response: Optional[Dict[str, Any]] = None
            while True:
                try:
                    if deadline is None:
                        line = self._stdout_queue.get(timeout=1.0)
                    else:
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            raise ACPTransportTimeout("acp request timed out")
                        line = self._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    if deadline is None:
                        process = self._process
                        if process is not None and process.poll() is not None:
                            raise ProviderTransportError(
                                "acp adapter terminated unexpectedly during {0}: {1}".format(
                                    method or "request",
                                    self._stderr_preview(),
                                )
                            ) from exc
                        continue
                    raise ACPTransportTimeout("acp request timed out") from exc

                if line is None:
                    raise ProviderTransportError(
                        "acp adapter terminated unexpectedly: {0}".format(self._stderr_preview())
                    )

                response = self._parse_response_line(line)
                if response is None:
                    self._emit(
                        "acp.response.invalid",
                        {
                            "reason": "non_json_stdout_line",
                            "line": line[:240],
                        },
                    )
                    continue
                if self._is_notification(response):
                    if notification_sink is not None:
                        try:
                            notification_sink(response)
                        except Exception:
                            # Best-effort hook for higher layers; transport must
                            # continue processing the RPC response path.
                            pass
                    params = response.get("params")
                    params_dict = params if isinstance(params, dict) else {}
                    self._emit(
                        "acp.notification.received",
                        {
                            "method": str(response.get("method") or ""),
                            "params": {
                                "stage": str(params_dict.get("stage") or ""),
                                "elapsed_ms": int(params_dict.get("elapsed_ms") or 0),
                                "provider_id": str(params_dict.get("provider_id") or ""),
                                "session_id": str(params_dict.get("session_id") or ""),
                            },
                        },
                    )
                    if notification_handler is not None:
                        side_requests = self._normalize_side_requests(notification_handler(response))
                        for side_payload in side_requests:
                            side_id = str(side_payload.get("id") or "").strip()
                            if not side_id:
                                raise ProviderProtocolError("acp side request missing id")
                            if side_id in pending_side_ids:
                                raise ProviderProtocolError(
                                    "acp side request duplicated id: {0}".format(side_id)
                                )
                            self._write_payload(side_payload)
                            pending_side_ids.add(side_id)
                    continue
                response_id = str(response.get("id") or "")
                if not response_id:
                    self._emit("acp.response.invalid", {"reason": "missing_response_id", "line": line[:240]})
                    raise ProviderProtocolError("acp response missing id")

                if response_id in self._consumed_response_ids:
                    self._emit(
                        "acp.response.invalid",
                        {
                            "reason": "duplicate_response",
                            "request_id": response_id,
                        },
                    )
                    continue

                if response_id in pending_side_ids:
                    pending_side_ids.discard(response_id)
                    self._consumed_response_ids.add(response_id)
                    if side_response_sink is not None:
                        side_response_sink(response)
                    if main_response is not None and not pending_side_ids:
                        return main_response
                    continue

                if response_id != request_id:
                    self._emit(
                        "acp.response.invalid",
                        {
                            "reason": "unexpected_response_id",
                            "request_id": request_id,
                            "response_id": response_id,
                        },
                    )
                    continue

                self._consumed_response_ids.add(response_id)
                if pending_side_ids:
                    main_response = response
                    continue
                return response

    @staticmethod
    def _is_notification(response: Dict[str, Any]) -> bool:
        if not isinstance(response, dict):
            return False
        if "id" in response and str(response.get("id") or "").strip():
            return False
        method = response.get("method")
        return isinstance(method, str) and bool(method.strip())

    def close(self) -> None:
        self._closed = True
        process = self._process
        self._process = None
        if process is None:
            return

        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass

        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except Exception:
                process.kill()
                try:
                    process.wait(timeout=1)
                except Exception:
                    pass

        self._stdout_queue.put(None)
        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=1)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1)
        self._stdout_thread = None
        self._stderr_thread = None
        self._emit("acp.transport.closed", {})

    def _build_env(self) -> Dict[str, str]:
        # Keep base env for stable Python/module startup; allowlist can be
        # used by config governance and future strict filtering.
        env = dict(os.environ)
        # Ensure adapter subprocess can import local `perlica` package even
        # when current working directory is outside repository root.
        try:
            src_root = Path(__file__).resolve().parents[2]
            if (src_root / "perlica").is_dir():
                src_text = str(src_root)
                current = str(env.get("PYTHONPATH") or "")
                parts = [item for item in current.split(os.pathsep) if item]
                if src_text not in parts:
                    env["PYTHONPATH"] = (
                        os.pathsep.join([src_text] + parts) if parts else src_text
                    )
        except Exception:
            pass
        if self._config.env_allowlist:
            for key in self._config.env_allowlist:
                if key in os.environ:
                    env[key] = os.environ[key]
        return env

    def _read_stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                self._stdout_queue.put(line.rstrip("\n"))
        finally:
            self._stdout_queue.put(None)

    def _read_stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                text = line.rstrip("\n")
                if text:
                    self._stderr_lines.append(text)
        finally:
            return

    def _write_payload(self, payload: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise ProviderTransportError("acp adapter is not running")
        line = json.dumps(payload, ensure_ascii=True)
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        except BrokenPipeError as exc:
            raise ProviderTransportError(
                "acp adapter pipe is closed: {0}".format(self._stderr_preview())
            ) from exc
        except OSError as exc:
            raise ProviderTransportError(
                "failed to write acp request: {0}".format(exc)
            ) from exc

    @staticmethod
    def _parse_response_line(line: str) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return value

    @staticmethod
    def _normalize_side_requests(raw: Any) -> List[Dict[str, Any]]:
        if raw is None:
            return []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, tuple):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, Iterable):
            rows: List[Dict[str, Any]] = []
            for item in raw:
                if isinstance(item, dict):
                    rows.append(item)
            return rows
        return []

    def _stderr_preview(self) -> str:
        if not self._stderr_lines:
            return "no stderr"
        return " | ".join(list(self._stderr_lines)[-3:])

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        self._event_sink(event_type, payload)
