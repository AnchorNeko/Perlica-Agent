"""Claude CLI provider adapter."""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional

from perlica.interaction.types import InteractionAnswer, InteractionOption, InteractionRequest
from perlica.kernel.types import LLMRequest, LLMResponse, ToolCall, coerce_tool_calls
from perlica.providers.base import BaseProvider, ProviderContractError, ProviderError
from perlica.providers.base import ProviderInteractionHandler


ProviderEventSink = Callable[[str, Dict[str, Any]], None]


class ClaudeCLIProvider(BaseProvider):
    provider_id = "claude"

    def __init__(
        self,
        binary: str = "claude",
        timeout_sec: int = 300,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
        event_sink: Optional[ProviderEventSink] = None,
    ) -> None:
        self._binary = binary
        self._timeout_sec = timeout_sec
        self._interaction_handler = interaction_handler
        self._interaction_resolver = interaction_resolver
        self._event_sink = event_sink

    def generate(self, req: LLMRequest) -> LLMResponse:
        return self.generate_with_interaction(req=req)

    def generate_with_interaction(
        self,
        *,
        req: LLMRequest,
        interaction_handler: Optional[ProviderInteractionHandler] = None,
        interaction_resolver: Optional[Callable[[str], None]] = None,
    ) -> LLMResponse:
        handler = interaction_handler if interaction_handler is not None else self._interaction_handler
        resolver = interaction_resolver if interaction_resolver is not None else self._interaction_resolver
        followup_answers: List[str] = []
        max_rounds = 6
        round_index = 0
        payload: Dict[str, Any] = {}

        while round_index < max_rounds:
            round_index += 1
            prompt = self._build_prompt(req, answered=followup_answers)
            self._emit(
                "claude.stream.started",
                {
                    "round": round_index,
                    "conversation_id": req.conversation_id,
                },
            )
            command = self._build_command(prompt)
            completed = self._run_with_activity_timeout(command)
            if completed.returncode != 0:
                self._emit(
                    "claude.stream.failed",
                    {
                        "phase": "stream",
                        "round": round_index,
                        "returncode": completed.returncode,
                    },
                )
                raise ProviderError(
                    "claude CLI failed with code {0}: {1}".format(
                        completed.returncode,
                        (completed.stderr or completed.stdout).strip(),
                    )
                )

            payload = self._parse_output_payload(completed.stdout)
            self._emit(
                "claude.stream.event",
                {
                    "round": round_index,
                    "is_error": bool(payload.get("is_error")),
                    "has_permission_denials": isinstance(payload.get("permission_denials"), list),
                },
            )

            questions = self._extract_permission_questions(payload)
            if not questions:
                response = self._normalize_payload(payload)
                self._emit(
                    "claude.stream.completed",
                    {
                        "round": round_index,
                        "finish_reason": response.finish_reason,
                    },
                )
                return response

            if handler is None:
                # Preserve prior fallback behavior when interactive answer path is not wired.
                response = self._normalize_payload(payload)
                self._emit(
                    "claude.stream.completed",
                    {
                        "round": round_index,
                        "finish_reason": response.finish_reason,
                        "degraded": True,
                    },
                )
                return response

            answers = self._ask_user_questions(
                questions=questions,
                req=req,
                round_index=round_index,
                handler=handler,
                resolver=resolver,
            )
            if not answers:
                response = self._normalize_payload(payload)
                return response
            followup_answers.extend(answers)

        self._emit(
            "claude.stream.failed",
            {
                "phase": "finalize",
                "reason": "error_max_turns",
                "max_rounds": max_rounds,
            },
        )
        raise ProviderError("claude interaction exceeded max follow-up rounds (error_max_turns)")

    def _build_command(self, prompt: str) -> List[str]:
        command = [
            self._binary,
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--tools",
            "default",
            "--output-format",
            "json",
            "--max-turns",
            "15",
            prompt,
        ]
        return command

    def _run_with_activity_timeout(self, command: List[str]) -> subprocess.CompletedProcess[str]:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ProviderError("claude CLI not found") from exc

        last_stdout_len = 0
        last_stderr_len = 0
        stdout = ""
        stderr = ""
        while True:
            try:
                stdout, stderr = process.communicate(timeout=self._timeout_sec)
                break
            except subprocess.TimeoutExpired as exc:
                partial_stdout: Any = exc.stdout or ""
                partial_stderr: Any = exc.stderr or ""
                if isinstance(partial_stdout, bytes):
                    partial_stdout = partial_stdout.decode("utf-8", errors="replace")
                if isinstance(partial_stderr, bytes):
                    partial_stderr = partial_stderr.decode("utf-8", errors="replace")
                out_len = len(str(partial_stdout))
                err_len = len(str(partial_stderr))
                if out_len > last_stdout_len or err_len > last_stderr_len:
                    # Claude is still producing output; continue waiting.
                    last_stdout_len = out_len
                    last_stderr_len = err_len
                    continue

                process.kill()
                stdout, stderr = process.communicate()
                raise ProviderError(
                    "claude CLI timed out after {0}s of inactivity (possible long-running reasoning without final output)".format(
                        self._timeout_sec
                    )
                ) from exc

        return subprocess.CompletedProcess(
            args=command,
            returncode=int(process.returncode or 0),
            stdout=str(stdout or ""),
            stderr=str(stderr or ""),
        )

    def _parse_output_payload(self, stdout: str) -> Dict[str, Any]:
        stripped = stdout.strip()
        if not stripped:
            raise ProviderContractError("claude provider returned empty output")

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ProviderContractError("claude provider returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise ProviderContractError("claude provider returned non-object JSON payload")
        return payload

    def _normalize_payload(self, payload: Dict[str, Any]) -> LLMResponse:

        if bool(payload.get("is_error")):
            diagnostic_text = self._extract_diagnostic_text(payload)
            error_text = str(payload.get("result") or diagnostic_text or "claude provider error").strip()
            raise ProviderError(error_text)

        usage_payload = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        context_window = self._extract_context_window(payload)
        normalized_usage: Dict[str, Any] = {
            "input_tokens": int(usage_payload.get("input_tokens") or 0),
            "cached_input_tokens": int(usage_payload.get("cache_read_input_tokens") or 0),
            "output_tokens": int(usage_payload.get("output_tokens") or 0),
            "context_window": int(context_window),
            "raw_usage": dict(usage_payload),
        }

        if isinstance(payload.get("structured_output"), dict):
            structured = payload["structured_output"]
        elif isinstance(payload.get("result"), str):
            structured = self._try_parse_object(payload["result"])
        else:
            structured = None

        if structured is None:
            # Graceful fallback when schema validation is bypassed by user configuration.
            result_text = self._extract_fallback_text(payload)
            if not result_text.strip():
                diagnostic_text = self._extract_diagnostic_text(payload)
                if diagnostic_text.strip():
                    result_text = diagnostic_text
            if not result_text.strip():
                raise ProviderContractError(
                    "claude provider returned no assistant text in structured_output/result; shape={0}".format(
                        self._summarize_payload_shape(payload)
                    )
                )
            return LLMResponse(
                assistant_text=result_text,
                tool_calls=[],
                finish_reason="stop",
                usage=normalized_usage,
                raw=payload,
            )

        return self._normalize_structured(structured, payload, normalized_usage)

    def _ask_user_questions(
        self,
        *,
        questions: List[Dict[str, Any]],
        req: LLMRequest,
        round_index: int,
        handler: ProviderInteractionHandler,
        resolver: Optional[Callable[[str], None]],
    ) -> List[str]:
        resolved: List[str] = []
        for question_index, question in enumerate(questions, start=1):
            prompt_text = str(question.get("question") or "").strip()
            header = str(question.get("header") or "").strip()
            if header and prompt_text:
                prompt_text = "{0}: {1}".format(header, prompt_text)
            if not prompt_text:
                prompt_text = "请确认你的偏好选项。"
            options = self._normalize_question_options(question)
            interaction_id = "claude_q_{0}_{1}".format(round_index, question_index)
            request = InteractionRequest(
                interaction_id=interaction_id,
                question=prompt_text,
                options=options,
                allow_custom_input=True,
                source_method="claude.permission_denials.AskUserQuestion",
                conversation_id=req.conversation_id,
                run_id=str((req.context or {}).get("run_id") or ""),
                trace_id=str((req.context or {}).get("trace_id") or ""),
                provider_id=self.provider_id,
                raw={"question": dict(question)},
            )
            self._emit(
                "interaction.requested",
                {
                    "interaction_id": interaction_id,
                    "question": prompt_text,
                    "options_count": len(options),
                    "round": round_index,
                },
            )
            answer = handler(request)
            resolved_text = self._resolve_answer_text(answer=answer, options=options)
            resolved.append("{0} -> {1}".format(prompt_text, resolved_text))
            self._emit(
                "interaction.answered",
                {
                    "interaction_id": interaction_id,
                    "round": round_index,
                    "source": answer.source or "unknown",
                    "selected_index": answer.selected_index,
                },
            )
            if resolver is not None:
                resolver(interaction_id)
            self._emit(
                "interaction.resolved",
                {
                    "interaction_id": interaction_id,
                    "round": round_index,
                },
            )
        return resolved

    @staticmethod
    def _normalize_question_options(question: Dict[str, Any]) -> List[InteractionOption]:
        raw_options = question.get("options")
        if not isinstance(raw_options, list):
            return []
        options: List[InteractionOption] = []
        for index, item in enumerate(raw_options, start=1):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                label = "选项{0}".format(index)
            description = str(item.get("description") or "").strip()
            options.append(
                InteractionOption(
                    index=index,
                    option_id="option_{0}".format(index),
                    label=label,
                    description=description,
                    meta=dict(item),
                )
            )
        return options

    @staticmethod
    def _resolve_answer_text(answer: InteractionAnswer, options: List[InteractionOption]) -> str:
        if answer.custom_text.strip():
            return answer.custom_text.strip()
        if answer.selected_index is not None:
            for option in options:
                if option.index == int(answer.selected_index):
                    return option.label
        if answer.selected_option_id:
            for option in options:
                if option.option_id == answer.selected_option_id:
                    return option.label
        return "已确认"

    @staticmethod
    def _extract_permission_questions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        denials = payload.get("permission_denials")
        if not isinstance(denials, list):
            return []
        for denial in denials:
            if not isinstance(denial, dict):
                continue
            if str(denial.get("tool_name") or "").strip() != "AskUserQuestion":
                continue
            tool_input = denial.get("tool_input")
            if not isinstance(tool_input, dict):
                continue
            questions = tool_input.get("questions")
            if isinstance(questions, list):
                return [item for item in questions if isinstance(item, dict)]
        return []

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(str(event_type), dict(payload))
        except Exception:
            # Logging sink should not break provider call path.
            return

    @staticmethod
    def _try_parse_object(text: str) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return value

    @staticmethod
    def _normalize_structured(
        structured: Dict[str, Any], raw_payload: Dict[str, Any], usage: Dict[str, Any]
    ) -> LLMResponse:
        if "assistant_text" not in structured or "finish_reason" not in structured:
            raise ProviderContractError("claude structured_output missing required keys")

        raw_calls = structured.get("tool_calls", [])
        if raw_calls is None:
            raw_calls = []
        if not isinstance(raw_calls, list):
            raise ProviderContractError("claude structured_output tool_calls must be an array")

        tool_calls = coerce_tool_calls([item for item in raw_calls if isinstance(item, dict)])
        normalized_calls: List[ToolCall] = [call for call in tool_calls if call.tool_name]

        assistant_text = str(structured.get("assistant_text") or "")
        if not assistant_text.strip():
            fallback = ClaudeCLIProvider._extract_fallback_text(raw_payload)
            if fallback.strip():
                assistant_text = fallback
        if not assistant_text.strip():
            diagnostic_text = ClaudeCLIProvider._extract_diagnostic_text(raw_payload)
            if diagnostic_text.strip():
                assistant_text = diagnostic_text

        if not assistant_text.strip() and not normalized_calls:
            raise ProviderContractError(
                "claude structured_output returned empty assistant_text without tool_calls; shape={0}".format(
                    ClaudeCLIProvider._summarize_payload_shape(raw_payload)
                )
            )

        finish_reason = str(structured.get("finish_reason") or "stop").strip()
        if not finish_reason:
            finish_reason = "tool_calls" if normalized_calls else "stop"

        return LLMResponse(
            assistant_text=assistant_text,
            tool_calls=normalized_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw=raw_payload,
        )

    @staticmethod
    def _extract_fallback_text(payload: Dict[str, Any]) -> str:
        for key in ("result", "message", "output_text", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = ClaudeCLIProvider._extract_text_from_value(value)
                if nested:
                    return nested

        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            merged = "\n".join(parts).strip()
            if merged:
                return merged

        for key in ("structured_output",):
            value = payload.get(key)
            if isinstance(value, dict):
                nested = ClaudeCLIProvider._extract_text_from_value(value)
                if nested:
                    return nested

        nested_any = ClaudeCLIProvider._extract_text_from_value(payload)
        if nested_any:
            return nested_any
        return ""

    @staticmethod
    def _extract_diagnostic_text(payload: Dict[str, Any]) -> str:
        messages: List[str] = []

        error_text = ClaudeCLIProvider._collect_diagnostic_messages(payload.get("errors"))
        if error_text:
            messages.append("errors: {0}".format(error_text))

        denial_text = ClaudeCLIProvider._collect_diagnostic_messages(payload.get("permission_denials"))
        if denial_text:
            messages.append("permission_denials: {0}".format(denial_text))

        subtype = str(payload.get("subtype") or "").strip()
        if subtype:
            messages.append("subtype: {0}".format(subtype))

        if not messages:
            return ""
        return "Claude returned diagnostics without assistant text: {0}".format("; ".join(messages))

    @staticmethod
    def _collect_diagnostic_messages(value: Any, depth: int = 0) -> str:
        if depth > 4:
            return ""

        if isinstance(value, str):
            return value.strip()

        if isinstance(value, dict):
            chunks: List[str] = []
            for key in ("message", "error", "reason", "detail", "tool_name", "code", "type"):
                if key not in value:
                    continue
                fragment = ClaudeCLIProvider._collect_diagnostic_messages(value.get(key), depth + 1)
                if fragment:
                    chunks.append(fragment)
            for key in ("errors", "permission_denials"):
                if key in value:
                    nested = ClaudeCLIProvider._collect_diagnostic_messages(value.get(key), depth + 1)
                    if nested:
                        chunks.append(nested)
            deduped = []
            seen = set()
            for item in chunks:
                norm = item.strip()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                deduped.append(norm)
            return "; ".join(deduped)

        if isinstance(value, list):
            chunks = []
            for item in value:
                fragment = ClaudeCLIProvider._collect_diagnostic_messages(item, depth + 1)
                if fragment:
                    chunks.append(fragment)
            deduped = []
            seen = set()
            for item in chunks:
                norm = item.strip()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                deduped.append(norm)
            return "; ".join(deduped)

        return ""

    @staticmethod
    def _extract_text_from_value(value: Any, depth: int = 0) -> str:
        if depth > 4:
            return ""
        if isinstance(value, str):
            stripped = value.strip()
            return stripped
        if isinstance(value, dict):
            for key in ("text", "output_text", "message", "result", "content"):
                if key in value:
                    found = ClaudeCLIProvider._extract_text_from_value(value.get(key), depth + 1)
                    if found:
                        return found
            return ""
        if isinstance(value, list):
            chunks: List[str] = []
            for item in value:
                found = ClaudeCLIProvider._extract_text_from_value(item, depth + 1)
                if found:
                    chunks.append(found)
            return "\n".join(chunks).strip()
        return ""

    @staticmethod
    def _summarize_payload_shape(payload: Dict[str, Any]) -> Dict[str, Any]:
        keys = sorted(str(k) for k in payload.keys())
        summary: Dict[str, Any] = {"keys": keys[:24], "size": len(keys)}
        for key in ("result", "content", "message", "structured_output"):
            if key not in payload:
                continue
            value = payload.get(key)
            summary["{0}_type".format(key)] = type(value).__name__
            if isinstance(value, str):
                summary["{0}_len".format(key)] = len(value)
            elif isinstance(value, list):
                summary["{0}_len".format(key)] = len(value)
            elif isinstance(value, dict):
                summary["{0}_keys".format(key)] = sorted(str(k) for k in value.keys())[:16]
        return summary

    @staticmethod
    def _extract_context_window(payload: Dict[str, Any]) -> int:
        model_usage = payload.get("modelUsage")
        if not isinstance(model_usage, dict):
            return 200000
        for model_stats in model_usage.values():
            if isinstance(model_stats, dict) and model_stats.get("contextWindow") is not None:
                try:
                    return int(model_stats.get("contextWindow"))
                except (TypeError, ValueError):
                    return 200000
        return 200000

    @staticmethod
    def _build_prompt(req: LLMRequest, answered: Optional[List[str]] = None) -> str:
        lines: List[str] = [
            "You are Perlica, a macOS control agent.",
            "If you need user preferences before acting, ask concise questions and options.",
            "When user answers are provided below, continue execution directly.",
            "",
            "Conversation:",
        ]

        for item in req.messages[-24:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().lower() or "user"
            content = ClaudeCLIProvider._content_to_text(item.get("content"))
            if not content:
                continue
            lines.append("{0}: {1}".format(role, content))

        if req.tools:
            lines.append("")
            lines.append("Available Perlica tools:")
            for raw in req.tools[:32]:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("tool_name") or raw.get("name") or "").strip()
                if not name:
                    continue
                desc = str(raw.get("description") or "").strip()
                if desc:
                    lines.append("- {0}: {1}".format(name, desc))
                else:
                    lines.append("- {0}".format(name))

        if answered:
            lines.append("")
            lines.append("User answered your previous questions:")
            for item in answered:
                lines.append("- {0}".format(item))

        return "\n".join(lines)

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                return text.strip()
            return ""
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        parts.append(text)
                    continue
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                if item_type == "text" and isinstance(item.get("text"), str):
                    text = str(item.get("text")).strip()
                    if text:
                        parts.append(text)
            return "\n".join(parts).strip()
        return ""
