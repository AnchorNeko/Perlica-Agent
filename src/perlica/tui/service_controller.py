"""Controller for Perlica foreground service mode."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from perlica.config import ALLOWED_PROVIDERS, load_settings
from perlica.kernel.runtime import Runtime
from perlica.repl_commands import (
    InteractionCommandHooks,
    ReplState,
    ServiceCommandHooks,
    build_slash_hint,
    execute_slash_command_to_text,
)
from perlica.service.channel_bootstrap import bootstrap_channel
from perlica.service.channels import (
    get_channel_registration,
    list_channel_registrations,
)
from perlica.service.channels.base import ChannelAdapter
from perlica.service.orchestrator import ServiceOrchestrator
from perlica.service.store import ServiceStore
from perlica.service.tool_policies import apply_tool_policy, list_tool_policy_lines
from perlica.service.types import (
    ServiceChannelOption,
    ServiceEvent,
    ServiceStatusSnapshot,
)
from perlica.ui.render import render_notice

ServiceEventSink = Callable[[ServiceEvent], None]


class ServiceController:
    """Wires runtime + service orchestrator for TUI bridge mode."""

    def __init__(self, provider: str, yes: bool, context_id: Optional[str]) -> None:
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in ALLOWED_PROVIDERS:
            raise ValueError(
                "service supports provider in [{0}], got: {1}".format(
                    "|".join(ALLOWED_PROVIDERS),
                    provider,
                )
            )
        self._settings = load_settings(context_id=context_id, provider=normalized_provider)
        self._runtime = Runtime(self._settings)
        self._store = ServiceStore(self._service_db_path(self._settings.config_root))
        self._provider = normalized_provider
        self._yes = yes
        self._event_sink: Optional[ServiceEventSink] = None
        self._channel: Optional[ChannelAdapter] = None
        self._orchestrator: Optional[ServiceOrchestrator] = None
        self._active_channel_id: Optional[str] = None

    @staticmethod
    def _service_db_path(config_root: Path) -> Path:
        return config_root / "service" / "service_bridge.db"

    def start(self) -> None:
        """No-op: service TUI now waits for explicit channel selection."""

    def close(self) -> None:
        if self._orchestrator is not None:
            self._orchestrator.stop()
        self._orchestrator = None
        self._channel = None
        self._store.close()
        self._runtime.close()

    def set_event_sink(self, sink: Optional[ServiceEventSink]) -> None:
        self._event_sink = sink
        if self._orchestrator is not None:
            self._orchestrator.set_event_sink(self._forward_event)

    def list_channel_options(self) -> list[ServiceChannelOption]:
        options: list[ServiceChannelOption] = []
        for registration in list_channel_registrations():
            available = True
            reason = ""
            try:
                probe_adapter = registration.factory()
                probe_adapter.probe()
            except Exception as exc:
                available = False
                reason = str(exc)
            options.append(
                ServiceChannelOption(
                    channel_id=registration.channel_id,
                    display_name=registration.display_name,
                    description=registration.description,
                    available=available,
                    reason=reason,
                )
            )
        return options

    def activate_channel(self, channel_id: str) -> str:
        registration = get_channel_registration(channel_id)
        channel = registration.factory()
        bootstrap = bootstrap_channel(channel)
        if not bootstrap.ok:
            raise RuntimeError(bootstrap.message)

        if self._orchestrator is not None:
            self._orchestrator.stop()

        self._channel = channel
        self._active_channel_id = registration.channel_id
        self._orchestrator = ServiceOrchestrator(
            runtime=self._runtime,
            store=self._store,
            channel=channel,
            provider_id=self._provider,
            yes=self._yes,
        )
        self._orchestrator.set_event_sink(self._forward_event)
        self._orchestrator.start()
        return bootstrap.message

    def has_active_channel(self) -> bool:
        return self._orchestrator is not None

    def active_channel_id(self) -> str:
        return self._active_channel_id or ""

    def _default_channel_id_example(self) -> str:
        for registration in list_channel_registrations():
            channel_id = str(registration.channel_id or "").strip().lower()
            if channel_id:
                return channel_id
        return ""

    def _inactive_channel_notice(self, *, level: str, zh_prefix: str, en_prefix: str) -> str:
        command = "/service channel use <channel_id>"
        zh = "{0}请先执行 `{1}`。".format(zh_prefix, command)
        en = "{0}Run `{1}` first.".format(en_prefix, command)
        example_channel = self._default_channel_id_example()
        if example_channel:
            zh = "{0} 例如 `/service channel use {1}`。".format(zh, example_channel)
            en = "{0} Example: `/service channel use {1}`.".format(en, example_channel)
        return render_notice(level, zh, en)

    def submit_input(self, raw: str) -> str:
        text = str(raw or "").strip("\n")
        if not text.strip():
            return ""
        if text.startswith("/"):
            normalized = text.strip().lower()
            if normalized.startswith("/service channel"):
                dispatch, output = execute_slash_command_to_text(text, self._command_state())
                if dispatch.handled:
                    return output
            if self._orchestrator is None:
                dispatch, output = execute_slash_command_to_text(text, self._command_state())
                if dispatch.handled:
                    return output
                return self._inactive_channel_notice(
                    level="warn",
                    zh_prefix="尚未激活渠道，",
                    en_prefix="No active channel. ",
                )
            return self._orchestrator.execute_local_command(text)
        if self._orchestrator is None:
            return self._inactive_channel_notice(
                level="warn",
                zh_prefix="尚未激活渠道，",
                en_prefix="No active channel. ",
            )
        if self._orchestrator.has_pending_interaction():
            return self._orchestrator.submit_interaction_answer(text, source="local")
        return self._orchestrator.execute_local_text(text)

    def build_slash_hint_text(self, raw_input: str) -> str:
        return build_slash_hint(raw_input=raw_input, state=self._command_state()).text

    def status_text(self) -> str:
        if self._orchestrator is None:
            return "channel=未激活 (inactive) paired=no listen=stopped/down"
        return self._orchestrator.status_text()

    def status_snapshot(self) -> ServiceStatusSnapshot:
        if self._orchestrator is None:
            return ServiceStatusSnapshot(
                channel=self._active_channel_id or "-",
                paired=False,
                contact_id=None,
                chat_id=None,
                session_id=None,
                pairing_code=None,
            )
        return self._orchestrator.status_snapshot()

    def has_pending_interaction(self) -> bool:
        return bool(self._orchestrator is not None and self._orchestrator.has_pending_interaction())

    def pending_interaction_text(self) -> str:
        if self._orchestrator is None:
            return "当前无待确认交互。"
        return self._orchestrator.pending_interaction_text()

    def busy_reject_message(self) -> str:
        message = self._runtime.task_coordinator.reject_new_command_if_busy()
        if message:
            return message
        return "上一条指令仍在执行中，请稍后再试。"

    def emit_task_command_rejected(self, *, source: str, text: str) -> None:
        snapshot = self._runtime.task_coordinator.snapshot()
        conversation_id = snapshot.conversation_id or "cli.{0}".format(self._runtime.context_id)
        self._runtime.emit(
            "task.command.rejected",
            {
                "source": source,
                "reason": "busy_running_task",
                "state": snapshot.state.value,
                "run_id": snapshot.run_id,
                "input_preview": str(text or "")[:120],
            },
            conversation_id=conversation_id,
            actor="service_tui",
            run_id=snapshot.run_id or None,
        )

    def _forward_event(self, event: ServiceEvent) -> None:
        if self._event_sink is not None:
            self._event_sink(event)

    def _command_state(self) -> ReplState:
        if self._orchestrator is not None:
            base = self._orchestrator.state
            return ReplState(
                context_id=base.context_id,
                provider=base.provider,
                yes=base.yes,
                session_ref=base.session_ref,
                session_name=base.session_name,
                session_is_ephemeral=base.session_is_ephemeral,
                service_hooks=self._build_service_hooks(),
                interaction_hooks=self._build_interaction_hooks(),
            )
        return ReplState(
            context_id=self._settings.context_id,
            provider=self._provider,
            yes=self._yes,
            session_ref=None,
            session_name=None,
            session_is_ephemeral=False,
            service_hooks=self._build_service_hooks(),
            interaction_hooks=self._build_interaction_hooks(),
        )

    def _build_service_hooks(self) -> ServiceCommandHooks:
        return ServiceCommandHooks(
            status=self._service_status,
            rebind=self._service_rebind,
            unpair=self._service_unpair,
            channel_list=self._service_channel_list,
            channel_use=self._service_channel_use,
            channel_current=self._service_channel_current,
            tools_list=self._service_tools_list,
            tools_allow=self._service_tools_allow,
            tools_deny=self._service_tools_deny,
        )

    def _build_interaction_hooks(self) -> InteractionCommandHooks:
        return InteractionCommandHooks(
            pending=self._interaction_pending_text,
            choose=self._interaction_choose_text,
            has_pending=self._interaction_has_pending,
            choice_suggestions=self._interaction_choice_suggestions,
        )

    def _interaction_pending_text(self) -> str:
        if self._orchestrator is None:
            return "当前无待确认交互。"
        return self._orchestrator.pending_interaction_text()

    def _interaction_choose_text(self, raw_choice: str, source: str) -> str:
        if self._orchestrator is None:
            return render_notice("error", "当前无待确认交互。", "No pending interaction.")
        return self._orchestrator.submit_interaction_answer(raw_choice, source=source)

    def _interaction_has_pending(self) -> bool:
        return bool(self._orchestrator is not None and self._orchestrator.has_pending_interaction())

    def _interaction_choice_suggestions(self) -> list[str]:
        if self._orchestrator is None:
            return []
        return self._orchestrator.pending_choice_suggestions()

    def _service_status(self) -> str:
        if self._orchestrator is None:
            return self._inactive_channel_notice(
                level="info",
                zh_prefix="当前尚未激活渠道，",
                en_prefix="No active channel. ",
            )
        return self._orchestrator.status_text()

    def _service_rebind(self) -> str:
        if self._orchestrator is None:
            return self._inactive_channel_notice(
                level="warn",
                zh_prefix="当前未激活渠道，无法重绑。",
                en_prefix="No active channel. Unable to rebind. ",
            )
        return self._orchestrator.rebind()

    def _service_unpair(self) -> str:
        if self._orchestrator is None:
            return self._inactive_channel_notice(
                level="warn",
                zh_prefix="当前未激活渠道，无法解除配对。",
                en_prefix="No active channel. Unable to unpair. ",
            )
        return self._orchestrator.unpair()

    def _service_channel_list(self) -> str:
        options = self.list_channel_options()
        if not options:
            return render_notice(
                "warn",
                "未发现可用渠道。",
                "No channels registered.",
            )

        lines = ["渠道列表 (Channels):"]
        for item in options:
            status = "可用" if item.available else "不可用"
            reason = " - {0}".format(item.reason) if item.reason else ""
            marker = "* " if item.channel_id == self._active_channel_id else "  "
            lines.append(
                "{0}{1} ({2}) [{3}]{4}".format(
                    marker,
                    item.display_name,
                    item.channel_id,
                    status,
                    reason,
                )
            )
        return "\n".join(lines)

    def _service_channel_use(self, channel_id: str) -> str:
        normalized = str(channel_id or "").strip().lower()
        if not normalized:
            example = self._default_channel_id_example()
            return render_notice(
                "error",
                "请提供渠道 ID，例如 `{0}`。".format(example or "<channel_id>"),
                "Channel id is required, e.g. `{0}`.".format(example or "<channel_id>"),
            )
        try:
            message = self.activate_channel(normalized)
        except Exception as exc:
            return render_notice(
                "error",
                "激活渠道失败：{0}".format(exc),
                "Failed to activate channel: {0}".format(exc),
            )
        return render_notice(
            "success",
            "渠道已激活：{0}。{1}".format(normalized, message),
            "Channel activated: {0}. {1}".format(normalized, message),
        )

    def _service_channel_current(self) -> str:
        if not self._active_channel_id:
            return render_notice(
                "info",
                "当前没有激活渠道。",
                "No active channel.",
            )
        return "active_channel={0}".format(self._active_channel_id)

    def _service_tools_list(self) -> str:
        return "\n".join(list_tool_policy_lines(self._runtime))

    def _service_tools_allow(
        self,
        tool_name: Optional[str],
        apply_all: bool,
        risk: Optional[str],
    ) -> str:
        try:
            report = apply_tool_policy(
                self._runtime,
                allow=True,
                tool_name=tool_name,
                apply_all=apply_all,
                risk=risk,
            )
        except ValueError as exc:
            return render_notice("error", str(exc), "Invalid tool policy command.")

        target = "全部工具" if apply_all else str(tool_name or "")
        return render_notice(
            "success",
            "已允许 {0}，risk={1}，更新 {2} 条规则。".format(
                target,
                ",".join(report["risks"]),
                report["updated"],
            ),
            "Tool(s) allowed.",
        )

    def _service_tools_deny(
        self,
        tool_name: Optional[str],
        apply_all: bool,
        risk: Optional[str],
    ) -> str:
        try:
            report = apply_tool_policy(
                self._runtime,
                allow=False,
                tool_name=tool_name,
                apply_all=apply_all,
                risk=risk,
            )
        except ValueError as exc:
            return render_notice("error", str(exc), "Invalid tool policy command.")

        target = "全部工具" if apply_all else str(tool_name or "")
        return render_notice(
            "success",
            "已禁止 {0}，risk={1}，更新 {2} 条规则。".format(
                target,
                ",".join(report["risks"]),
                report["updated"],
            ),
            "Tool(s) denied.",
        )


def start_tui_service(provider: str, yes: bool, context_id: Optional[str]) -> int:
    """Start Textual TUI service mode. Raises RuntimeError when unavailable."""

    try:
        from perlica.tui.service_app import (
            PerlicaServiceApp,
            textual_available,
            textual_import_error,
        )
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Textual 未安装或初始化失败，请先安装依赖：`python3 -m pip install textual`。"
        ) from exc

    if not textual_available():
        raise RuntimeError(
            "Textual 未安装或初始化失败：{0}。请先执行 `python3 -m pip install textual`。".format(
                textual_import_error()
            )
        )

    controller = ServiceController(provider=provider, yes=yes, context_id=context_id)
    try:
        app = PerlicaServiceApp(controller=controller)
        app.run()
        return 0
    finally:
        controller.close()
