"""Thread-safe interaction coordinator for pending confirmation requests."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from perlica.interaction.types import (
    InteractionAnswer,
    InteractionOption,
    InteractionRequest,
    InteractionSubmitResult,
)

InteractionEventSink = Callable[[str, Dict[str, Any]], None]


@dataclass(frozen=True)
class InteractionSnapshot:
    """Read-only pending interaction snapshot for UI/diagnostics."""

    has_pending: bool
    interaction_id: str = ""
    question: str = ""
    options: List[InteractionOption] = field(default_factory=list)
    allow_custom_input: bool = True
    answered: bool = False


class InteractionCoordinator:
    """Single-active-request coordinator with first-answer-wins semantics."""

    def __init__(self, event_sink: Optional[InteractionEventSink] = None) -> None:
        self._event_sink = event_sink
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._request: Optional[InteractionRequest] = None
        self._answer: Optional[InteractionAnswer] = None

    def publish(self, request: InteractionRequest) -> None:
        with self._condition:
            replaced = self._request is not None and self._answer is None
            if replaced and self._request is not None:
                self._emit(
                    "interaction.answer_rejected",
                    {
                        "interaction_id": self._request.interaction_id,
                        "reason": "replaced_by_new_request",
                    },
                )
            self._request = request
            self._answer = None
            self._condition.notify_all()
        self._emit(
            "interaction.requested",
            {
                "interaction_id": request.interaction_id,
                "question": request.question,
                "option_count": len(request.options),
                "allow_custom_input": request.allow_custom_input,
                "source_method": request.source_method,
                "conversation_id": request.conversation_id,
                "run_id": request.run_id,
                "trace_id": request.trace_id,
                "session_id": request.session_id,
                "provider_id": request.provider_id,
            },
        )

    def has_pending(self) -> bool:
        with self._lock:
            return self._request is not None and self._answer is None

    def snapshot(self) -> InteractionSnapshot:
        with self._lock:
            if self._request is None:
                return InteractionSnapshot(has_pending=False, options=[])
            return InteractionSnapshot(
                has_pending=self._answer is None,
                interaction_id=self._request.interaction_id,
                question=self._request.question,
                options=list(self._request.options),
                allow_custom_input=self._request.allow_custom_input,
                answered=self._answer is not None,
            )

    def current_request(self) -> Optional[InteractionRequest]:
        with self._lock:
            if self._request is None:
                return None
            return self._request

    def pending_hint_text(self) -> str:
        snapshot = self.snapshot()
        if not snapshot.has_pending:
            return "当前无待确认交互。"

        lines = ["当前待确认交互：", snapshot.question]
        if snapshot.interaction_id:
            lines.append("交互ID: {0}".format(snapshot.interaction_id))
        for option in snapshot.options:
            detail = ""
            if option.description:
                detail = " - {0}".format(option.description)
            lines.append("{0}. {1}{2}".format(option.index, option.label, detail))
        if snapshot.allow_custom_input:
            lines.append("可直接输入自定义文本。")
        lines.append("可用命令：/choose <编号|文本>")
        return "\n".join(lines)

    def choice_suggestions(self) -> List[str]:
        snapshot = self.snapshot()
        if not snapshot.has_pending:
            return []
        suggestions = [str(item.index) for item in snapshot.options]
        if snapshot.allow_custom_input:
            suggestions.append("<自定义文本>")
        return suggestions

    def submit_answer(self, raw_input: str, source: str) -> InteractionSubmitResult:
        text = str(raw_input or "").strip()
        with self._condition:
            if self._request is None:
                return InteractionSubmitResult(accepted=False, message="当前没有待确认交互。")
            if self._answer is not None:
                self._emit(
                    "interaction.answer_rejected",
                    {
                        "interaction_id": self._request.interaction_id,
                        "reason": "already_answered",
                        "source": source,
                    },
                )
                return InteractionSubmitResult(accepted=False, message="该交互已结束，请等待下一条请求。")

            if not text:
                return InteractionSubmitResult(accepted=False, message="请输入编号或文本回答。")

            answer = self._parse_answer_locked(self._request, text=text, source=source)
            if answer is None:
                self._emit(
                    "interaction.answer_rejected",
                    {
                        "interaction_id": self._request.interaction_id,
                        "reason": "invalid_answer",
                        "input": text,
                        "source": source,
                        "conversation_id": self._request.conversation_id,
                        "run_id": self._request.run_id,
                        "trace_id": self._request.trace_id,
                        "session_id": self._request.session_id,
                    },
                )
                return InteractionSubmitResult(
                    accepted=False,
                    message="无效选项。请输入有效编号，或输入自定义文本。",
                )

            self._answer = answer
            self._condition.notify_all()

        payload: Dict[str, Any] = {
            "interaction_id": answer.interaction_id,
            "source": answer.source,
            "conversation_id": answer.conversation_id,
            "run_id": answer.run_id,
            "trace_id": answer.trace_id,
            "session_id": answer.session_id,
        }
        if answer.selected_index is not None:
            payload["selected_index"] = answer.selected_index
        if answer.selected_option_id:
            payload["selected_option_id"] = answer.selected_option_id
        if answer.custom_text:
            payload["custom_text"] = answer.custom_text
        self._emit("interaction.answered", payload)
        return InteractionSubmitResult(accepted=True, message="交互回答已提交。", answer=answer)

    def wait_for_answer(self, interaction_id: str) -> InteractionAnswer:
        normalized_id = str(interaction_id or "").strip()
        if not normalized_id:
            raise RuntimeError("interaction_id is required")

        with self._condition:
            while True:
                if self._request is None:
                    raise RuntimeError("interaction request is not active")
                if self._request.interaction_id != normalized_id:
                    raise RuntimeError("interaction request changed before answer")
                if self._answer is not None:
                    return self._answer
                self._condition.wait(timeout=0.2)

    def resolve(self, interaction_id: str) -> None:
        normalized_id = str(interaction_id or "").strip()
        if not normalized_id:
            return

        with self._condition:
            request = self._request
            answer = self._answer
            if request is None or request.interaction_id != normalized_id:
                return
            self._request = None
            self._answer = None
            self._condition.notify_all()

        self._emit(
            "interaction.resolved",
            {
                "interaction_id": normalized_id,
                "had_answer": answer is not None,
                "conversation_id": request.conversation_id if request is not None else "",
                "run_id": request.run_id if request is not None else "",
                "trace_id": request.trace_id if request is not None else "",
                "session_id": request.session_id if request is not None else "",
            },
        )

    def _parse_answer_locked(
        self,
        request: InteractionRequest,
        *,
        text: str,
        source: str,
    ) -> Optional[InteractionAnswer]:
        if text.isdigit() and request.options:
            index = int(text)
            for option in request.options:
                if option.index == index:
                    return InteractionAnswer(
                        interaction_id=request.interaction_id,
                        selected_index=option.index,
                        selected_option_id=option.option_id,
                        custom_text="",
                        source=source,
                        conversation_id=request.conversation_id,
                        run_id=request.run_id,
                        trace_id=request.trace_id,
                        session_id=request.session_id,
                    )
            return None

        if not request.allow_custom_input:
            return None

        return InteractionAnswer(
            interaction_id=request.interaction_id,
            custom_text=text,
            source=source,
            conversation_id=request.conversation_id,
            run_id=request.run_id,
            trace_id=request.trace_id,
            session_id=request.session_id,
        )

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event_type, dict(payload))
        except Exception:
            return
