"""Runtime container wiring core services and built-in plugins."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from perlica.config import ALLOWED_PROVIDERS, Settings
from perlica.interaction.coordinator import InteractionCoordinator
from perlica.interaction.types import InteractionRequest
from perlica.kernel.debug_log import DebugLogWriter
from perlica.kernel.dispatcher import Dispatcher
from perlica.kernel.eventbus import EventBus
from perlica.kernel.eventlog import EventLog
from perlica.kernel.plugin_manager import PluginLoadReport, PluginManager
from perlica.kernel.policy_engine import ApprovalStore, PolicyEngine
from perlica.kernel.registry import Registry
from perlica.kernel.session_migration import SessionMigrationReport, drop_sessions_by_provider
from perlica.kernel.session_store import SessionRecord, SessionStore
from perlica.kernel.types import EventEnvelope, Tool, now_ms
from perlica.mcp.manager import MCPManager
from perlica.mcp.types import MCPReloadReport
from perlica.prompt.system_prompt import load_system_prompt
from perlica.providers.factory import ProviderFactory
from perlica.security.permission_probe import run_startup_permission_checks
from perlica.skills.engine import SkillEngine
from perlica.skills.loader import SkillLoader
from perlica.task.coordinator import TaskCoordinator
from perlica.tools.mcp_tool import MCPTool
from perlica.tools.shell_tool import ShellTool


class Runtime:
    core_version = "2.0.0"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.context_id = settings.context_id
        self.context_dir = settings.context_dir
        self.workspace_dir = settings.workspace_dir
        self.config: Dict[str, Any] = {
            "provider": settings.provider,
            "provider_backend": settings.provider_backend,
            "provider_adapter_command": settings.provider_adapter_command,
            "provider_adapter_args": list(settings.provider_adapter_args),
            "provider_adapter_env_allowlist": list(settings.provider_adapter_env_allowlist),
            "provider_acp_connect_timeout": settings.provider_acp_connect_timeout,
            "provider_acp_request_timeout": settings.provider_acp_request_timeout,
            "provider_acp_max_retries": settings.provider_acp_max_retries,
            "provider_acp_backoff": settings.provider_acp_backoff,
            "provider_acp_circuit_breaker_enabled": settings.provider_acp_circuit_breaker_enabled,
            "max_tool_calls": settings.max_tool_calls,
            "provider_context_windows": dict(settings.provider_context_windows),
            "context_budget_ratio": settings.context_budget_ratio,
            "max_summary_attempts": settings.max_summary_attempts,
            "logs": {
                "enabled": settings.logs_enabled,
                "format": settings.logs_format,
                "max_file_bytes": settings.logs_max_file_bytes,
                "max_files": settings.logs_max_files,
                "redaction": settings.logs_redaction,
            },
        }
        self.system_prompt = load_system_prompt(settings)
        self.system_prompt_error: Optional[str] = None
        self._acp_session_errors = 0
        self._acp_adapter_status = self._detect_acp_adapter_status()
        self._session_migration_report = SessionMigrationReport()
        self._acp_activity_lock = threading.Lock()
        self._acp_activity: Dict[str, Any] = {
            "provider_id": "",
            "method": "",
            "stage": "",
            "session_id": "",
            "run_id": "",
            "attempt": 0,
            "elapsed_ms": 0,
            "updated_at_ms": 0,
        }

        self.context_dir.mkdir(parents=True, exist_ok=True)
        self._logs_dir = self.context_dir / "logs"
        self._logs_dir.mkdir(parents=True, exist_ok=True)

        self.event_log = EventLog(self.context_dir / "eventlog.db", context_id=self.context_id)
        self.event_bus = EventBus()
        self.registry = Registry()
        self.interaction_coordinator = InteractionCoordinator(event_sink=self._emit_interaction_event)
        self.task_coordinator = TaskCoordinator(event_sink=self._emit_task_event)

        self.approval_store = ApprovalStore(self.context_dir / "approvals.db")
        self.session_store = SessionStore(self.context_dir / "sessions.db")
        self._session_migration_report = drop_sessions_by_provider(self.session_store, "codex")
        self.policy_engine = PolicyEngine(self.approval_store)
        self.dispatcher = Dispatcher(self.registry, self.policy_engine)

        self.plugin_manager = PluginManager(settings.plugin_dirs)
        self.plugin_report = self.plugin_manager.load()

        self.skill_engine = SkillEngine(SkillLoader(settings.skill_dirs))

        self.permission_report = run_startup_permission_checks(
            workspace_dir=self.workspace_dir,
            trigger_applescript=False,
        )
        self.mcp_manager = MCPManager(settings.mcp_servers_file)
        self.mcp_report = self.mcp_manager.load()
        self.debug_log = DebugLogWriter(
            logs_dir=self._logs_dir,
            enabled=settings.logs_enabled,
            log_format=settings.logs_format,
            max_file_bytes=settings.logs_max_file_bytes,
            max_files=settings.logs_max_files,
            redaction=settings.logs_redaction,
        )
        self.subscribe("*", self.debug_log.write_event)

        self._register_builtins()
        self._emit_plugin_load_events(self.plugin_report)
        self._emit_mcp_load_events()

    def _register_builtins(self) -> None:
        if not self.settings.provider_profile.enabled:
            raise RuntimeError(
                "active provider profile is disabled: {0}".format(self.settings.provider_profile.provider_id)
            )
        provider_factory = ProviderFactory(
            event_emitter=self._emit_provider_event,
            interaction_handler=self._resolve_provider_interaction_request,
            interaction_resolver=self.interaction_coordinator.resolve,
        )
        self.register_provider(provider_factory.build(self.settings.provider_profile))
        self.register_tool(ShellTool())
        self._register_mcp_tools()

    def _resolve_provider_interaction_request(self, request: InteractionRequest):
        self.task_coordinator.mark_waiting_interaction(
            interaction_id=request.interaction_id,
            run_id=request.run_id or None,
        )
        self.log_diagnostic(
            level="info",
            component="interaction",
            kind="request",
            event_type="interaction.requested",
            conversation_id=request.conversation_id or None,
            run_id=request.run_id or None,
            trace_id=request.trace_id or None,
            message="provider interaction request received",
            data={
                "interaction_id": request.interaction_id,
                "question": request.question,
                "option_count": len(request.options),
                "allow_custom_input": request.allow_custom_input,
                "provider_id": request.provider_id,
                "session_id": request.session_id,
            },
        )
        self.interaction_coordinator.publish(request)
        answer = self.interaction_coordinator.wait_for_answer(request.interaction_id)
        self.task_coordinator.submit_interaction_answer(interaction_id=request.interaction_id)
        return answer

    def _emit_provider_event(self, event_type: str, payload: Dict[str, Any], context: Dict[str, Any]) -> None:
        conversation_id = str(context.get("conversation_id") or "cli.{0}".format(self.context_id))
        run_id = str(context.get("run_id") or "")
        trace_id = str(context.get("trace_id") or "")
        self._update_acp_activity(event_type=event_type, payload=payload, run_id=run_id)
        self.emit(
            event_type,
            dict(payload),
            conversation_id=conversation_id,
            actor="provider",
            run_id=run_id if run_id else None,
            trace_id=trace_id if trace_id else None,
        )
        if event_type == "acp.session.failed":
            self._acp_session_errors += 1

    def _emit_interaction_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        conversation_id = str(payload.get("conversation_id") or "cli.{0}".format(self.context_id))
        run_id = str(payload.get("run_id") or "").strip()
        trace_id = str(payload.get("trace_id") or "").strip()
        self.emit(
            event_type,
            dict(payload),
            conversation_id=conversation_id,
            actor="interaction",
            run_id=run_id if run_id else None,
            trace_id=trace_id if trace_id else None,
        )
        self.log_diagnostic(
            level="info",
            component="interaction",
            kind="lifecycle",
            event_type=event_type,
            conversation_id=conversation_id,
            run_id=run_id if run_id else None,
            trace_id=trace_id if trace_id else None,
            message=event_type,
            data=dict(payload),
        )

    def _emit_task_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        conversation_id = str(payload.get("conversation_id") or "cli.{0}".format(self.context_id))
        run_id = str(payload.get("run_id") or "").strip()
        self.emit(
            event_type,
            dict(payload),
            conversation_id=conversation_id,
            actor="task",
            run_id=run_id if run_id else None,
        )
        self.log_diagnostic(
            level="info",
            component="task",
            kind="lifecycle",
            event_type=event_type,
            conversation_id=conversation_id,
            run_id=run_id if run_id else None,
            message=event_type,
            data=dict(payload),
        )

    def _update_acp_activity(self, event_type: str, payload: Dict[str, Any], run_id: str) -> None:
        if not event_type.startswith("acp."):
            return
        with self._acp_activity_lock:
            if event_type == "acp.request.sent":
                method = str(payload.get("method") or "")
                if method == "session/prompt":
                    self._acp_activity.update(
                        {
                            "provider_id": str(payload.get("provider_id") or ""),
                            "method": method,
                            "stage": method,
                            "session_id": "",
                            "run_id": run_id,
                            "attempt": int(payload.get("attempt") or 0),
                            "elapsed_ms": 0,
                            "updated_at_ms": now_ms(),
                        }
                    )
                return
            if event_type == "acp.notification.received":
                params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
                stage = str(params.get("stage") or "")
                if not stage:
                    return
                self._acp_activity.update(
                    {
                        "provider_id": str(params.get("provider_id") or self._acp_activity.get("provider_id") or ""),
                        "method": "session/prompt",
                        "stage": stage,
                        "session_id": str(params.get("session_id") or ""),
                        "run_id": run_id or str(self._acp_activity.get("run_id") or ""),
                        "elapsed_ms": int(params.get("elapsed_ms") or 0),
                        "updated_at_ms": now_ms(),
                    }
                )
                return
            if event_type in {"acp.request.timeout", "acp.session.closed", "acp.session.failed"}:
                method = str(payload.get("method") or "")
                if method and method != "session/prompt":
                    return
                self._acp_activity.update(
                    {
                        "updated_at_ms": now_ms(),
                        "elapsed_ms": int(self._acp_activity.get("elapsed_ms") or 0),
                    }
                )

    def acp_activity_snapshot(self) -> Dict[str, Any]:
        with self._acp_activity_lock:
            snapshot = dict(self._acp_activity)
        updated_at_ms = int(snapshot.get("updated_at_ms") or 0)
        snapshot["age_ms"] = max(0, now_ms() - updated_at_ms) if updated_at_ms else 10**9
        return snapshot

    def _detect_acp_adapter_status(self) -> str:
        if self.settings.provider_backend != "acp":
            return "disabled"
        command = str(self.settings.provider_adapter_command or "").strip()
        if not command:
            return "missing_command"
        path = Path(command)
        if path.is_absolute():
            return "configured" if path.exists() else "missing_command"
        from shutil import which

        return "configured" if which(command) else "missing_command"

    def _register_mcp_tools(self) -> None:
        for tool_spec in self.mcp_manager.list_tool_specs():
            self.register_tool(
                MCPTool(
                    tool_name=tool_spec.qualified_name,
                    description=tool_spec.description,
                    input_schema=tool_spec.input_schema,
                )
            )

    def _emit_plugin_load_events(self, report: PluginLoadReport) -> None:
        for plugin_id, manifest in report.loaded.items():
            self.emit(
                "plugin.loaded",
                {
                    "plugin_id": plugin_id,
                    "kind": manifest.kind,
                    "version": manifest.version,
                },
                actor="plugin_manager",
            )

        for plugin_id, reason in report.failed.items():
            self.emit(
                "plugin.failed",
                {"plugin_id": plugin_id, "reason": reason},
                actor="plugin_manager",
            )

    def _emit_mcp_load_events(self) -> None:
        for server_id, state in self.mcp_report.states.items():
            if state.error:
                self.emit(
                    "mcp.server.failed",
                    {"server_id": server_id, "reason": state.error},
                    actor="mcp_manager",
                )
                continue
            self.emit(
                "mcp.server.loaded",
                {
                    "server_id": server_id,
                    "tool_count": len(state.tools),
                    "resource_count": len(state.resources),
                    "prompt_count": len(state.prompts),
                },
                actor="mcp_manager",
            )

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        conversation_id: Optional[str] = None,
        parent_node_id: Optional[str] = None,
        actor: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        envelope = self.event_log.append(
            event_type=event_type,
            payload=payload,
            conversation_id=conversation_id or "cli.{0}".format(self.context_id),
            parent_node_id=parent_node_id,
            actor=actor or "runtime",
            meta=meta,
            run_id=run_id,
            trace_id=trace_id,
            causation_id=causation_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        self.event_bus.publish(envelope)
        return envelope.event_id

    def subscribe(self, event_type: str, handler: Callable[[EventEnvelope], None]) -> None:
        self.event_bus.subscribe(event_type, handler)

    def log_diagnostic(
        self,
        *,
        level: str,
        component: str,
        kind: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> None:
        self.debug_log.write_entry(
            level=level,
            component=component,
            kind=kind,
            context_id=self.context_id,
            conversation_id=conversation_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type=event_type,
            message=message,
            data=data,
        )

    def register_tool(self, tool: Tool) -> None:
        self.registry.register_tool(tool)

    def register_provider(self, provider: object) -> None:
        self.registry.register_provider(provider)  # type: ignore[arg-type]

    def resolve_provider(self, provider_id: str):
        return self.registry.get_provider(provider_id)

    def tool_specs(self) -> List[Dict[str, Any]]:
        specs: List[Dict[str, Any]] = []
        for tool_name in self.registry.list_tool_ids():
            if tool_name == "shell.exec":
                specs.append(
                    {
                        "tool_name": "shell.exec",
                        "description": "Execute shell commands in the workspace.",
                        "arguments": {
                            "cmd": "string",
                            "timeout_sec": "int(optional)",
                        },
                    }
                )
            elif tool_name.startswith("mcp."):
                tool_spec = self.mcp_manager.get_tool_spec(tool_name)
                if tool_spec is None:
                    specs.append({"tool_name": tool_name, "description": "mcp tool"})
                else:
                    specs.append(
                        {
                            "tool_name": tool_name,
                            "description": tool_spec.description or "mcp tool",
                            "arguments": tool_spec.input_schema or {"type": "object"},
                        }
                    )
            else:
                specs.append({"tool_name": tool_name, "description": "custom tool"})
        return specs

    def mcp_prompt_context_blocks(self) -> List[str]:
        return self.mcp_manager.build_prompt_context_blocks()

    def reload_mcp(self) -> MCPReloadReport:
        for tool_name in list(self.registry.tools.keys()):
            if tool_name.startswith("mcp."):
                self.registry.tools.pop(tool_name, None)
        self.mcp_report = self.mcp_manager.reload()
        self._register_mcp_tools()
        self._emit_mcp_load_events()
        return self.mcp_report

    def doctor(self, verbose: bool = False) -> Dict[str, Any]:
        from shutil import which

        writable_ok = True
        error = None
        try:
            probe = self.context_dir / ".doctor_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - defensive
            writable_ok = False
            error = str(exc)

        report: Dict[str, Any] = {
            "context_id": self.context_id,
            "context_dir": str(self.context_dir),
            "providers": {
                "claude": bool(which("claude")),
                "opencode": bool(which("opencode")),
            },
            "active_provider": self.settings.provider,
            "provider_profile_enabled": bool(self.settings.provider_profile.enabled),
            "provider_adapter_command": self.settings.provider_adapter_command,
            "provider_adapter_args": list(self.settings.provider_adapter_args),
            "provider_adapter_probe": self._acp_adapter_status,
            "acp_timeout_mode": "prompt_waits_for_final_response",
            "db_writable": writable_ok,
            "plugins_loaded": self.plugin_report.loaded_count,
            "plugins_failed": self.plugin_report.failed_count,
            "skills_loaded": len(self.skill_engine.list_skills()),
            "skills_errors": len(self.skill_engine.list_errors()),
            "permissions": dict(self.permission_report.get("checks") or {}),
            "system_prompt_loaded": bool(self.system_prompt),
            "skill_prompt_injection_enabled": False,
            "provider_config_injection_enabled": True,
            "provider_capabilities": {
                "supports_mcp_config": bool(self.settings.provider_profile.supports_mcp_config),
                "supports_skill_config": bool(self.settings.provider_profile.supports_skill_config),
                "tool_execution_mode": str(self.settings.provider_profile.tool_execution_mode or ""),
                "injection_failure_policy": str(
                    self.settings.provider_profile.injection_failure_policy or ""
                ),
            },
            "mcp_servers_loaded": self.mcp_report.loaded_servers,
            "mcp_tools_loaded": self.mcp_report.tool_count,
            "mcp_errors": self.mcp_manager.status().get("errors", {}),
            "provider_backend": self.settings.provider_backend,
            "acp_adapter_status": self._acp_adapter_status,
            "acp_session_errors": self._acp_session_errors,
            "session_migration": {
                "deleted_sessions": self._session_migration_report.deleted_sessions,
                "deleted_messages": self._session_migration_report.deleted_messages,
                "deleted_summaries": self._session_migration_report.deleted_summaries,
                "fixed_current_state_rows": self._session_migration_report.fixed_current_state_rows,
            },
        }
        report.update(self.debug_log.status())

        if verbose:
            report["plugin_failures"] = dict(self.plugin_report.failed)
            report["skill_errors"] = dict(self.skill_engine.list_errors())
            report["mcp_servers"] = self.mcp_manager.status().get("servers", [])
            if error:
                report["db_error"] = error
        return report

    def close(self) -> None:
        self.debug_log.close()
        self.event_log.close()
        self.approval_store.close()
        self.session_store.close()
        close_mcp = getattr(self.mcp_manager, "close", None)
        if callable(close_mcp):
            close_mcp()

    def get_or_create_current_session(self) -> SessionRecord:
        current = self.session_store.get_current_session(self.context_id)
        if current is not None:
            return current
        provider_id = str(self.settings.provider or "").strip().lower()
        if not provider_id:
            raise RuntimeError(
                "missing provider for new session, use --provider {0}".format(
                    "|".join(ALLOWED_PROVIDERS)
                )
            )
        created = self.session_store.create_session(
            context_id=self.context_id,
            provider_locked=provider_id,
        )
        self.session_store.set_current_session(self.context_id, created.session_id)
        return created

    def resolve_session_for_run(self, session_ref: Optional[str]) -> SessionRecord:
        if session_ref:
            session = self.session_store.resolve_session_ref(self.context_id, session_ref)
            self.session_store.set_current_session(self.context_id, session.session_id)
            return session
        return self.get_or_create_current_session()

    def resolve_provider_context_window(self, provider_id: str) -> int:
        configured = self.settings.provider_context_windows.get(provider_id)
        if configured is None:
            return 200000
        return max(1, int(configured))

    def storage_open_db(self, namespace: str):
        path = self.context_dir / "{0}.db".format(namespace)
        return __import__("sqlite3").connect(str(path))

    def ensure_plugin_data_dir(self, plugin_id: str) -> Path:
        path = self.context_dir / "plugins_data" / plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path
