"""Codex CLI provider adapter."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

from perlica.kernel.types import LLMRequest, LLMResponse, ToolCall, coerce_tool_calls
from perlica.providers.base import BaseProvider, ProviderContractError, ProviderError


class CodexCLIProvider(BaseProvider):
    provider_id = "codex"

    def __init__(self, binary: str = "codex", timeout_sec: int = 90) -> None:
        self._binary = binary
        self._timeout_sec = timeout_sec

    def generate(self, req: LLMRequest) -> LLMResponse:
        prompt = self._build_prompt(req)
        command = [
            self._binary,
            "exec",
            "-s",
            "read-only",
            "--json",
            "--skip-git-repo-check",
            prompt,
        ]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_sec,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderError("codex CLI not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("codex CLI timed out") from exc

        if completed.returncode != 0:
            raise ProviderError(
                "codex CLI failed with code {0}: {1}".format(
                    completed.returncode,
                    (completed.stderr or completed.stdout).strip(),
                )
            )

        return self._parse_jsonl_stdout(completed.stdout)

    def _parse_jsonl_stdout(self, stdout: str) -> LLMResponse:
        last_agent_message: Optional[str] = None
        usage_payload: Dict[str, Any] = {}
        normalized_usage: Dict[str, Any] = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "context_window": 200000,
            "raw_usage": usage_payload,
        }

        for line in [item.strip() for item in stdout.splitlines() if item.strip()]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = str(event.get("type") or "")
            item = event.get("item") or {}
            item_type = str(item.get("type") or "")

            # Parse token usage from the terminal turn completion event.
            if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
                usage_payload = dict(event.get("usage") or {})
                normalized_usage = {
                    "input_tokens": int(usage_payload.get("input_tokens") or 0),
                    "cached_input_tokens": int(usage_payload.get("cached_input_tokens") or 0),
                    "output_tokens": int(usage_payload.get("output_tokens") or 0),
                    "context_window": 200000,
                    "raw_usage": usage_payload,
                }

            if item_type == "command_execution":
                raise ProviderContractError(
                    "codex provider attempted command_execution, which is disallowed"
                )

            if event_type == "error":
                raise ProviderError(str(event.get("message") or "codex provider error"))

            if event_type == "item.completed" and item_type == "agent_message":
                last_agent_message = str(item.get("text") or "")

        if last_agent_message is None:
            raise ProviderContractError("codex provider did not emit agent_message")

        payload = self._try_parse_json(last_agent_message)
        if payload is None:
            return LLMResponse(
                assistant_text=last_agent_message,
                tool_calls=[],
                finish_reason="stop",
                usage=normalized_usage,
            )

        return self._normalize_payload(
            payload,
            fallback_text=last_agent_message,
            usage=normalized_usage,
        )

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
        stripped = text.strip()

        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Fallback for wrapped JSON like markdown code fences.
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            snippet = stripped[start : end + 1]
            try:
                parsed = json.loads(snippet)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _normalize_payload(payload: Dict[str, Any], fallback_text: str, usage: Dict[str, Any]) -> LLMResponse:
        assistant_text = str(payload.get("assistant_text") or fallback_text)
        finish_reason = str(payload.get("finish_reason") or "stop")

        raw_calls = payload.get("tool_calls")
        if isinstance(raw_calls, list):
            tool_calls = coerce_tool_calls([item for item in raw_calls if isinstance(item, dict)])
        else:
            tool_calls = []

        normalized_calls: List[ToolCall] = []
        for call in tool_calls:
            if not call.tool_name:
                continue
            normalized_calls.append(call)

        return LLMResponse(
            assistant_text=assistant_text,
            tool_calls=normalized_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw=payload,
        )

    @staticmethod
    def _build_prompt(req: LLMRequest) -> str:
        messages_json = json.dumps(req.messages, ensure_ascii=True)
        context = req.context if isinstance(req.context, dict) else {}
        provider_config = context.get("provider_config") if isinstance(context.get("provider_config"), dict) else {}
        provider_config_json = json.dumps(provider_config, ensure_ascii=True)

        return (
            "You are Perlica, a macOS control agent. "
            "You may use shell tools, AppleScript workflows, skill context, and MCP servers when available. "
            "You are the Perlica provider adapter. "
            "Return exactly one JSON object with keys assistant_text (string), "
            "finish_reason (string), and optional tool_calls (array). "
            "No markdown, no extra text. "
            "Provider config: {provider_config}. Messages: {messages}."
        ).format(provider_config=provider_config_json, messages=messages_json)
