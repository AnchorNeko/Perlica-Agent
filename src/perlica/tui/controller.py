"""Controller layer for Perlica Textual chat mode."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional

from perlica.config import ALLOWED_PROVIDERS, load_settings
from perlica.kernel.policy_engine import ApprovalAction
from perlica.kernel.runner import Runner, RunnerResult
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_store import SessionStore
from perlica.kernel.types import ToolCall
from perlica.providers.base import ProviderError
from perlica.repl_commands import (
    InteractionCommandHooks,
    ReplState,
    build_slash_hint,
    execute_slash_command_to_text,
)
from perlica.tui.types import ChatStatus, SlashOutcome
from perlica.ui.render import render_notice

ProgressCallback = Callable[[str, Dict[str, str]], None]


class ChatController:
    """Owns runtime/session lifecycle for one TUI process."""

    def __init__(
        self,
        provider: str,
        yes: bool,
        context_id: Optional[str],
    ) -> None:
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in ALLOWED_PROVIDERS:
            raise ValueError(
                "chat supports provider=claude only, got: {0}".format(provider)
            )

        self._settings = load_settings(context_id=context_id, provider=normalized_provider)
        self._runtime = Runtime(self._settings)
        self._provider_override = normalized_provider
        self._yes = yes
        self._phase = "就绪 (Ready)"

        self._cleanup_unsaved_ephemeral_all_contexts(self._settings.config_root)
        session = self._runtime.session_store.create_session(
            context_id=self._runtime.context_id,
            provider_locked=self._provider_override,
            is_ephemeral=True,
        )
        self._runtime.session_store.set_current_session(self._runtime.context_id, session.session_id)

        self._state = ReplState(
            context_id=self._runtime.context_id,
            provider=self._provider_override,
            yes=self._yes,
            session_ref=session.session_id,
            session_name=session.name,
            session_is_ephemeral=session.is_ephemeral,
            interaction_hooks=InteractionCommandHooks(
                pending=self._interaction_pending_text,
                choose=self._interaction_choose,
                has_pending=self.has_pending_interaction,
                choice_suggestions=self._interaction_choice_suggestions,
            ),
        )

    @property
    def state(self) -> ReplState:
        return self._state

    def close(self) -> None:
        self._runtime.close()

    def status(self) -> ChatStatus:
        session = self._current_session()
        title = "临时会话" if session and session.is_ephemeral and not session.name else (session.name if session else "临时会话")
        model = self._resolve_model_name(session)
        return ChatStatus(
            model=model,
            session_title=title or "临时会话",
            context_id=self._runtime.context_id,
            phase=self._phase,
        )

    def run_user_message(
        self,
        text: str,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> RunnerResult:
        runner = Runner(
            runtime=self._runtime,
            provider_id=self._provider_override,
            max_tool_calls=self._settings.max_tool_calls,
            approval_resolver=self._approval_resolver(),
        )
        result = runner.run_text(
            text=text,
            assume_yes=self._yes,
            session_ref=self._state.session_ref,
            progress_callback=progress_callback,
        )
        self._sync_state_with_session(result.session_id)
        self._phase = "就绪 (Ready)"
        return result

    def run_slash_command(self, raw_line: str) -> SlashOutcome:
        result, output = execute_slash_command_to_text(raw_line=raw_line, state=self._state)
        self._sync_state_with_session(self._state.session_ref)
        if result.handled:
            return SlashOutcome(
                handled=True,
                exit_requested=result.exit_requested,
                output_text=output,
            )
        return SlashOutcome(
            handled=False,
            exit_requested=False,
            output_text="",
            fallback_text=raw_line,
        )

    def has_pending_interaction(self) -> bool:
        return self._runtime.interaction_coordinator.has_pending()

    def interaction_pending_text(self) -> str:
        return self._runtime.interaction_coordinator.pending_hint_text()

    def submit_interaction_answer(self, raw_input: str, source: str) -> str:
        result = self._runtime.interaction_coordinator.submit_answer(raw_input, source=source)
        if result.accepted:
            self._runtime.log_diagnostic(
                level="info",
                component="interaction",
                kind="answer",
                event_type="interaction.answered",
                conversation_id=(
                    result.answer.conversation_id if result.answer is not None else None
                ),
                run_id=result.answer.run_id if result.answer is not None else None,
                trace_id=result.answer.trace_id if result.answer is not None else None,
                message="interaction answer accepted",
                data={
                    "source": source,
                    "interaction_id": result.answer.interaction_id if result.answer else "",
                    "selected_index": (
                        result.answer.selected_index if result.answer is not None else None
                    ),
                },
            )
            return render_notice("success", result.message, "Interaction answer submitted.")
        return render_notice("warn", result.message, "Interaction answer rejected.")

    def build_slash_hint_text(self, raw_input: str) -> str:
        return build_slash_hint(raw_input=raw_input, state=self._state).text

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def should_confirm_exit(self) -> bool:
        session = self._current_session()
        if session is None:
            return False
        return bool(session.is_ephemeral and session.saved_at_ms is None)

    def save_current_session(self, name: Optional[str] = None) -> str:
        session = self._current_session()
        if session is None:
            return render_notice("error", "当前没有会话可保存。", "No session to save.")

        saved = self._runtime.session_store.save_session(session.session_id, name=name)
        self._runtime.session_store.set_current_session(self._runtime.context_id, saved.session_id)
        self._sync_state_with_session(saved.session_id)
        return render_notice(
            "success",
            "会话已保存：{0} name={1}".format(saved.session_id, saved.name or ""),
            "Session saved.",
        )

    def discard_current_session(self) -> str:
        session = self._current_session()
        if session is None:
            return render_notice("error", "当前没有会话可丢弃。", "No session to discard.")
        if not self._runtime.session_store.is_unsaved_ephemeral(session.session_id):
            return render_notice(
                "warn",
                "当前会话不是未保存临时会话，无法丢弃。",
                "Current session is not an unsaved temporary session.",
            )

        self._runtime.session_store.discard_session(session.session_id)
        replacement = self._runtime.session_store.create_session(
            context_id=self._runtime.context_id,
            provider_locked=session.provider_locked or self._provider_override,
            is_ephemeral=True,
        )
        self._runtime.session_store.set_current_session(self._runtime.context_id, replacement.session_id)
        self._sync_state_with_session(replacement.session_id)
        return render_notice(
            "success",
            "已丢弃临时会话并新建临时会话：{0}".format(replacement.session_id),
            "Temporary session discarded and replaced.",
        )

    def _sync_state_with_session(self, session_id: Optional[str]) -> None:
        if not session_id:
            return
        session = self._runtime.session_store.get_session(session_id)
        if session is None:
            return
        self._state.session_ref = session.session_id
        self._state.session_name = session.name
        self._state.session_is_ephemeral = session.is_ephemeral
        self._runtime.session_store.set_current_session(self._runtime.context_id, session.session_id)

    def _current_session(self):
        if self._state.session_ref:
            session = self._runtime.session_store.get_session(self._state.session_ref)
            if session is not None and session.context_id == self._runtime.context_id:
                return session
        current = self._runtime.session_store.get_current_session(self._runtime.context_id)
        return current

    def _resolve_model_name(self, session) -> str:
        if session and session.provider_locked:
            return session.provider_locked
        return self._provider_override

    def _approval_resolver(self):
        if self._yes:
            return lambda _call, _risk: ApprovalAction(allow=True, reason="tui_yes")

        def resolver(call: ToolCall, risk_tier: str) -> ApprovalAction:
            return ApprovalAction(
                allow=False,
                reason="approval_required_in_tui:{0}:{1}".format(call.tool_name, risk_tier),
            )

        return resolver

    def _interaction_pending_text(self) -> str:
        return self.interaction_pending_text()

    def _interaction_choose(self, raw_choice: str, source: str) -> str:
        return self.submit_interaction_answer(raw_choice, source=source)

    def _interaction_choice_suggestions(self) -> list[str]:
        return self._runtime.interaction_coordinator.choice_suggestions()

    @staticmethod
    def _cleanup_unsaved_ephemeral_all_contexts(config_root: Path) -> None:
        contexts_root = config_root / "contexts"
        if not contexts_root.exists() or not contexts_root.is_dir():
            return

        for context_dir in contexts_root.iterdir():
            if not context_dir.is_dir():
                continue
            db_path = context_dir / "sessions.db"
            if not db_path.exists():
                continue
            store: Optional[SessionStore] = None
            try:
                store = SessionStore(db_path)
                store.cleanup_unsaved_ephemeral()
            finally:
                if store is not None:
                    store.close()


def start_tui_chat(provider: str, yes: bool, context_id: Optional[str]) -> int:
    """Start Textual TUI chat. Raises RuntimeError when Textual is unavailable."""

    try:
        from perlica.tui.app import (
            PerlicaChatApp,
            textual_available,
            textual_import_error,
        )
    except Exception as exc:  # pragma: no cover - import path is validated in integration tests
        raise RuntimeError(
            "Textual 未安装或初始化失败，请先安装依赖：`python3 -m pip install textual` "
            "(Textual is required for chat mode)."
        ) from exc

    if not textual_available():
        raise RuntimeError(
            "Textual 未安装或初始化失败：{0}。请先执行 `python3 -m pip install textual`。".format(
                textual_import_error()
            )
        )

    controller = ChatController(provider=provider, yes=yes, context_id=context_id)
    try:
        app = PerlicaChatApp(controller=controller)
        app.run()
        return 0
    finally:
        controller.close()


def format_provider_error(exc: ProviderError) -> str:
    return render_notice(
        "error",
        "模型调用失败：{0}".format(exc),
        "Provider error: {0}".format(exc),
    )
