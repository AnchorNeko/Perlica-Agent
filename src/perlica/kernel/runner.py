"""Core runner orchestration: session context + provider calls + tool dispatch."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from perlica.config import ALLOWED_PROVIDERS
from perlica.kernel.policy_engine import ApprovalAction
from perlica.kernel.session_store import (
    SessionMessageRecord,
    SessionRecord,
    SessionSummaryRecord,
    estimate_tokens_from_text,
)
from perlica.kernel.types import (
    LLMCallUsage,
    LLMRequest,
    LLMResponse,
    ToolCall,
    ToolResult,
    UsageTotals,
    new_id,
)
from perlica.providers.base import ProviderError
from perlica.skills.engine import SkillSelection

ApprovalResolver = Callable[[ToolCall, str], ApprovalAction]
ProgressCallback = Callable[[str, Dict[str, str]], None]


@dataclass
class RunnerResult:
    assistant_text: str
    run_id: str
    trace_id: str
    conversation_id: str
    session_id: str
    session_name: Optional[str]
    provider_id: str
    context_usage: Dict[str, int] = field(default_factory=dict)
    llm_call_usages: List[LLMCallUsage] = field(default_factory=list)
    total_usage: UsageTotals = field(default_factory=UsageTotals)
    tool_results: List[ToolResult] = field(default_factory=list)


class Runner:
    """Runs one text request through Perlica execution pipeline."""

    def __init__(
        self,
        runtime: object,
        provider_id: Optional[str],
        max_tool_calls: int,
        approval_resolver: Optional[ApprovalResolver] = None,
    ) -> None:
        self._runtime = runtime
        self._requested_provider_id = provider_id
        self._max_tool_calls = max(1, max_tool_calls)
        self._approval_resolver = approval_resolver

        self._llm_call_index = 0
        self._llm_call_usages: List[LLMCallUsage] = []
        self._progress_callback: Optional[ProgressCallback] = None
        self._current_skill_count = 0
        self._current_mcp_tools_count = 0
        self._current_mcp_context_count = 0

    def run_text(
        self,
        text: str,
        assume_yes: bool = False,
        session_ref: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> RunnerResult:
        self._llm_call_index = 0
        self._llm_call_usages = []
        self._progress_callback = progress_callback
        self._current_skill_count = 0
        self._current_mcp_tools_count = 0
        self._current_mcp_context_count = 0

        self._update_progress("resolve-session", detail="select")
        session = self._runtime.resolve_session_for_run(session_ref=session_ref)
        provider_id = self._resolve_provider_for_session(session)
        session = self._runtime.session_store.lock_provider(session.session_id, provider_id)
        self._runtime.session_store.set_current_session(self._runtime.context_id, session.session_id)

        self._update_progress(
            "resolve-session",
            session_id=session.session_id,
            provider_id=provider_id,
            detail="locked",
        )

        provider = self._runtime.resolve_provider(provider_id)
        if provider is None:
            raise ProviderError(
                "provider '{0}' unavailable, try --provider {1}".format(
                    provider_id,
                    "|".join(ALLOWED_PROVIDERS),
                )
            )

        conversation_id = "session.{0}".format(session.session_id)
        run_id = new_id("run")
        trace_id = new_id("trace")
        started_task = self._runtime.task_coordinator.start_task(
            run_id=run_id,
            conversation_id=conversation_id,
            session_id=session.session_id,
            metadata={"provider_id": provider_id},
        )
        if not started_task:
            busy_text = self._runtime.task_coordinator.reject_new_command_if_busy()
            raise ProviderError(busy_text or "another task is still running")

        try:
            self._runtime.emit(
                "inbound.message.received",
                {"text": text, "session_id": session.session_id},
                conversation_id=conversation_id,
                actor="cli",
                run_id=run_id,
                trace_id=trace_id,
            )

            skill_selection = self._runtime.skill_engine.select(text)
            self._emit_skill_events(skill_selection, conversation_id, run_id, trace_id)
            self._current_skill_count = len(self._runtime.skill_engine.list_skills())
            self._current_mcp_tools_count = len(self._runtime.mcp_manager.list_tool_specs())
            provider_config = self._build_provider_config(provider_id)
            self._current_mcp_context_count = 0

            self._update_progress(
                "load-context",
                session_id=session.session_id,
                provider_id=provider_id,
            )

            system_prompt = str(getattr(self._runtime, "system_prompt", "") or "")

            history_messages, history_records, summary_used, estimated_tokens = self._load_context_messages(
                session=session,
                provider_id=provider_id,
                user_text=text,
                system_prompt=system_prompt,
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
            )

            messages: List[Dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(history_messages)
            messages.append({"role": "user", "content": text})

            persist_entries: List[Dict[str, object]] = [{"role": "user", "content": {"text": text}}]

            response = self._call_provider(
                provider=provider,
                provider_id=provider_id,
                messages=messages,
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                provider_config=provider_config,
            )

            persist_entries.append(
                {
                    "role": "assistant",
                    "content": {
                        "text": response.assistant_text,
                        "finish_reason": response.finish_reason,
                    },
                }
            )

            tool_results: List[ToolResult] = []
            assistant_text = response.assistant_text

            if response.tool_calls:
                for call in response.tool_calls:
                    self._runtime.emit(
                        "tool.blocked",
                        {
                            "reason": "single_call_mode_local_tool_dispatch_disabled",
                            "tool_name": call.tool_name,
                            "call_id": call.call_id,
                            "run_id": run_id,
                        },
                        conversation_id=conversation_id,
                        run_id=run_id,
                        trace_id=trace_id,
                        actor="runner",
                    )
                    blocked_result = ToolResult(
                        call_id=call.call_id,
                        ok=False,
                        output={},
                        error="single_call_mode_local_tool_dispatch_disabled",
                    )
                    tool_results.append(blocked_result)
                    self._runtime.emit(
                        "tool.result",
                        {
                            "call_id": blocked_result.call_id,
                            "ok": blocked_result.ok,
                            "error": blocked_result.error,
                            "output": blocked_result.output,
                        },
                        conversation_id=conversation_id,
                        run_id=run_id,
                        trace_id=trace_id,
                        actor="runner",
                    )
                    persist_entries.append(
                        {
                            "role": "tool",
                            "content": {
                                "name": call.tool_name,
                                "call_id": call.call_id,
                                "result": blocked_result.as_message(),
                            },
                        }
                    )

            self._runtime.emit(
                "llm.single_call.enforced",
                {
                    "provider_id": provider_id,
                    "blocked_tool_calls_count": len(response.tool_calls),
                    "run_id": run_id,
                },
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="runner",
            )

            self._update_progress(
                "finalize",
                session_id=session.session_id,
                provider_id=provider_id,
            )

            for entry in persist_entries:
                self._runtime.session_store.append_message(
                    session_id=session.session_id,
                    role=str(entry["role"]),
                    content=dict(entry["content"]),
                    run_id=run_id,
                )

            self._runtime.session_store.touch_session(session.session_id)
            totals = self._totals_from_call_usages(self._llm_call_usages)

            return RunnerResult(
                assistant_text=assistant_text,
                run_id=run_id,
                trace_id=trace_id,
                conversation_id=conversation_id,
                session_id=session.session_id,
                session_name=session.name,
                provider_id=provider_id,
                context_usage={
                    "history_messages_included": len(history_records),
                    "summary_versions_used": 1 if summary_used is not None else 0,
                    "estimated_context_tokens": estimated_tokens,
                },
                llm_call_usages=list(self._llm_call_usages),
                total_usage=totals,
                tool_results=tool_results,
            )
        except Exception:
            self._runtime.task_coordinator.finish_task(run_id=run_id, failed=True)
            raise
        finally:
            # Ensure task state is released for the next command.
            snapshot = self._runtime.task_coordinator.snapshot()
            if snapshot.run_id == run_id and snapshot.has_active_task:
                self._runtime.task_coordinator.finish_task(run_id=run_id, failed=False)

    def _resolve_provider_for_session(self, session: SessionRecord) -> str:
        if session.provider_locked:
            if self._requested_provider_id and self._requested_provider_id != session.provider_locked:
                raise ProviderError(
                    "session '{0}' is locked to provider '{1}', cannot switch to '{2}'".format(
                        session.session_id,
                        session.provider_locked,
                        self._requested_provider_id,
                    )
                )
            return session.provider_locked
        if self._requested_provider_id:
            return self._requested_provider_id
        raise ProviderError(
            "session '{0}' has no locked provider, please specify --provider {1}".format(
                session.session_id,
                "|".join(ALLOWED_PROVIDERS),
            )
        )

    def _load_context_messages(
        self,
        session: SessionRecord,
        provider_id: str,
        user_text: str,
        system_prompt: str,
        conversation_id: str,
        run_id: str,
        trace_id: str,
    ) -> tuple[List[Dict[str, str]], List[SessionMessageRecord], Optional[SessionSummaryRecord], int]:
        context_window = self._runtime.resolve_provider_context_window(provider_id)
        budget = int(context_window * float(self._runtime.settings.context_budget_ratio))

        summary = self._runtime.session_store.get_latest_summary(session.session_id)
        covered_upto = summary.covered_upto_seq if summary else 0
        history_records = self._runtime.session_store.list_messages(session.session_id, after_seq=covered_upto)

        candidate_messages = self._build_history_messages(summary, history_records)
        estimated = self._estimate_context_tokens(
            history_messages=candidate_messages,
            user_text=user_text,
            system_prompt=system_prompt,
        )

        dropped_messages = 0
        while history_records and estimated > budget:
            history_records = history_records[1:]
            dropped_messages += 1
            candidate_messages = self._build_history_messages(summary, history_records)
            estimated = self._estimate_context_tokens(
                history_messages=candidate_messages,
                user_text=user_text,
                system_prompt=system_prompt,
            )

        if estimated > budget:
            self._runtime.emit(
                "context.truncated",
                {
                    "session_id": session.session_id,
                    "estimated_tokens": estimated,
                    "budget_tokens": budget,
                    "dropped_messages": dropped_messages,
                    "reason": "single_call_mode_no_summary_call",
                },
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="runner",
            )

        return candidate_messages, history_records, summary, estimated

    def _build_history_messages(
        self,
        summary: Optional[SessionSummaryRecord],
        records: Sequence[SessionMessageRecord],
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if summary is not None:
            messages.append(
                {
                    "role": "system",
                    "content": "Session memory summary (v{0}):\n{1}".format(
                        summary.version,
                        summary.summary_text,
                    ),
                }
            )

        for record in records:
            if record.role == "user":
                messages.append({"role": "user", "content": str(record.content.get("text") or "")})
            elif record.role == "assistant":
                messages.append({"role": "assistant", "content": str(record.content.get("text") or "")})
            elif record.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "name": str(record.content.get("name") or "tool"),
                        "content": json.dumps(record.content, ensure_ascii=True),
                    }
                )

        return messages

    def _call_provider(
        self,
        provider: object,
        provider_id: str,
        messages: List[dict],
        conversation_id: str,
        run_id: str,
        trace_id: str,
        provider_config: Dict[str, Any],
    ) -> LLMResponse:
        self._llm_call_index += 1
        self._update_progress(
            "llm-call-{0}".format(self._llm_call_index),
            provider_id=provider_id,
        )

        req = LLMRequest(
            conversation_id=conversation_id,
            messages=list(messages),
            context={
                "context_id": self._runtime.context_id,
                "conversation_id": conversation_id,
                "run_id": run_id,
                "trace_id": trace_id,
                "request_id": new_id("req"),
                "llm_call_index": self._llm_call_index,
                "cwd": str(self._runtime.workspace_dir),
                "provider_config": dict(provider_config),
            },
            tools=[],
        )

        self._runtime.emit(
            "llm.requested",
            {
                "provider_id": provider_id,
                "message_count": len(messages),
                "llm_call_index": self._llm_call_index,
                "skills_selected_count": self._current_skill_count,
                "mcp_tools_count": self._current_mcp_tools_count,
                "mcp_context_blocks_count": self._current_mcp_context_count,
                "mcp_provider_config_count": self._current_mcp_context_count,
            },
            conversation_id=conversation_id,
            run_id=run_id,
            trace_id=trace_id,
            actor="runner",
        )

        try:
            response = provider.generate(req)
        except ProviderError as exc:
            error_payload = self._build_provider_error_payload(
                provider_id=provider_id,
                exc=exc,
            )
            self._runtime.emit(
                "llm.provider_error",
                error_payload,
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="runner",
            )
            raise

        assistant_text = str(response.assistant_text or "")
        finish_reason = str(response.finish_reason or "stop").strip().lower() or "stop"
        if not assistant_text.strip() and not response.tool_calls and finish_reason == "stop":
            self._runtime.emit(
                "llm.invalid_response",
                {
                    "provider_id": provider_id,
                    "reason": "empty_assistant_text",
                    "llm_call_index": self._llm_call_index,
                    "finish_reason": finish_reason,
                    "response_raw_summary": self._summarize_response_raw(response.raw),
                },
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="runner",
            )
            raise ProviderError(
                "provider '{0}' returned empty assistant_text without tool_calls".format(
                    provider_id
                )
            )

        usage = response.usage if isinstance(response.usage, dict) else {}
        self._llm_call_usages.append(
            LLMCallUsage(
                call_index=self._llm_call_index,
                provider_id=provider_id,
                input_tokens=int(usage.get("input_tokens") or 0),
                cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                context_window=int(usage.get("context_window") or 0),
                raw_usage=dict(usage.get("raw_usage") or {}),
            )
        )

        self._runtime.emit(
            "llm.responded",
            {
                "provider_id": provider_id,
                "assistant_text": response.assistant_text,
                "tool_call_count": len(response.tool_calls),
                "finish_reason": response.finish_reason,
                "llm_call_index": self._llm_call_index,
                "usage": usage,
            },
            conversation_id=conversation_id,
            run_id=run_id,
            trace_id=trace_id,
            actor="runner",
        )

        return response

    def _emit_skill_events(
        self,
        selection: SkillSelection,
        conversation_id: str,
        run_id: str,
        trace_id: str,
    ) -> None:
        for skill in selection.selected:
            self._runtime.emit(
                "skill.selected",
                {
                    "skill_id": skill.skill_id,
                    "priority": skill.priority,
                    "source_path": skill.source_path,
                },
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="skill_engine",
            )

        for skill_id, reason in selection.skipped.items():
            self._runtime.emit(
                "skill.skipped",
                {"skill_id": skill_id, "reason": reason},
                conversation_id=conversation_id,
                run_id=run_id,
                trace_id=trace_id,
                actor="skill_engine",
            )

    def _estimate_context_tokens(
        self,
        history_messages: Sequence[Dict[str, str]],
        user_text: str,
        system_prompt: str,
    ) -> int:
        total = 0
        if system_prompt:
            total += estimate_tokens_from_text(system_prompt)
        for message in history_messages:
            total += estimate_tokens_from_text(str(message.get("content") or ""))
        total += estimate_tokens_from_text(user_text)
        return total

    def _build_provider_config(
        self,
        provider_id: str,
    ) -> Dict[str, Any]:
        profile = self._resolve_provider_profile(provider_id)
        provider_config: Dict[str, Any] = {}
        if profile is None:
            return provider_config

        tool_execution_mode = str(getattr(profile, "tool_execution_mode", "") or "").strip()
        if tool_execution_mode:
            provider_config["tool_execution_mode"] = tool_execution_mode
        injection_failure_policy = str(
            getattr(profile, "injection_failure_policy", "") or ""
        ).strip()
        if injection_failure_policy:
            provider_config["injection_failure_policy"] = injection_failure_policy

        return provider_config

    def _resolve_provider_profile(self, provider_id: str) -> Optional[Any]:
        settings = getattr(self._runtime, "settings", None)
        profiles = getattr(settings, "provider_profiles", {})
        if isinstance(profiles, dict):
            profile = profiles.get(provider_id)
            if profile is not None:
                return profile
        active_profile = getattr(settings, "provider_profile", None)
        if active_profile is not None:
            return active_profile
        return None

    @staticmethod
    def _totals_from_call_usages(usages: Sequence[LLMCallUsage]) -> UsageTotals:
        totals = UsageTotals()
        for usage in usages:
            totals.input_tokens += usage.input_tokens
            totals.cached_input_tokens += usage.cached_input_tokens
            totals.output_tokens += usage.output_tokens
        return totals

    def _update_progress(
        self,
        stage: str,
        session_id: str = "",
        provider_id: str = "",
        detail: str = "",
    ) -> None:
        if self._progress_callback is None:
            return
        self._progress_callback(
            stage,
            {
                "context_id": self._runtime.context_id,
                "session_id": session_id,
                "provider_id": provider_id,
                "detail": detail,
            },
        )

    @staticmethod
    def _summarize_response_raw(raw: Dict[str, object]) -> Dict[str, object]:
        if not isinstance(raw, dict) or not raw:
            return {}
        keys = sorted(str(k) for k in raw.keys())
        summary: Dict[str, object] = {
            "keys": keys[:24],
            "size": len(keys),
        }
        preview_fields: Dict[str, str] = {}
        for key in ("result", "message", "output_text", "text"):
            value = raw.get(key)
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped:
                continue
            preview_fields[key] = stripped[:240]
        if preview_fields:
            summary["preview"] = preview_fields
        content = raw.get("content")
        if isinstance(content, list):
            summary["content_items"] = len(content)
        elif isinstance(content, str) and content.strip():
            summary["content_preview"] = content.strip()[:240]
        return summary

    def _build_provider_error_payload(self, provider_id: str, exc: ProviderError) -> Dict[str, Any]:
        details = exc.details if hasattr(exc, "details") else {}
        payload: Dict[str, Any] = {
            "provider_id": str(details.get("provider_id") or provider_id),
            "error": str(exc),
            "llm_call_index": self._llm_call_index,
            "error_type": exc.__class__.__name__,
            "method": str(details.get("method") or ""),
            "code": details.get("code"),
            "subtype": str(details.get("subtype") or ""),
            "request_id": str(details.get("request_id") or ""),
            "raw_shape": details.get("raw_shape") if isinstance(details.get("raw_shape"), dict) else {},
        }
        return payload
