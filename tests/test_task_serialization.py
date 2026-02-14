from __future__ import annotations

import pytest

from perlica.config import load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.providers.base import ProviderError
from perlica.task.coordinator import TaskCoordinator
from perlica.task.types import TaskState


def test_task_coordinator_enforces_single_active_task():
    events = []
    coordinator = TaskCoordinator(event_sink=lambda event_type, payload: events.append((event_type, payload)))

    assert coordinator.start_task(
        run_id="run_1",
        conversation_id="session.s1",
        session_id="sess_1",
    )
    assert coordinator.snapshot().state == TaskState.RUNNING
    assert not coordinator.start_task(
        run_id="run_2",
        conversation_id="session.s2",
        session_id="sess_2",
    )

    coordinator.mark_waiting_interaction(interaction_id="int_1", run_id="run_1")
    assert coordinator.snapshot().state == TaskState.AWAITING_INTERACTION
    assert "等待确认" in str(coordinator.reject_new_command_if_busy() or "")

    assert coordinator.submit_interaction_answer(interaction_id="int_1")
    assert coordinator.snapshot().state == TaskState.RUNNING

    coordinator.finish_task(run_id="run_1", failed=False)
    assert coordinator.snapshot().state == TaskState.IDLE
    assert coordinator.reject_new_command_if_busy() is None

    event_types = [event_type for event_type, _ in events]
    assert "task.started" in event_types
    assert "task.state.changed" in event_types


def test_runner_rejects_new_task_when_another_task_is_running(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    try:
        assert runtime.task_coordinator.start_task(
            run_id="busy_run",
            conversation_id="session.busy",
            session_id="sess_busy",
        )
        runner = Runner(
            runtime=runtime,
            provider_id="claude",
            max_tool_calls=runtime.settings.max_tool_calls,
        )
        with pytest.raises(ProviderError) as exc:
            runner.run_text("你好", assume_yes=True)
        assert "执行中" in str(exc.value)
    finally:
        runtime.close()

