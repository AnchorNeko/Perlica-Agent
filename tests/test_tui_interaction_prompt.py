from __future__ import annotations

from perlica.interaction.types import InteractionOption, InteractionRequest
from perlica.tui.controller import ChatController


def test_chat_controller_submits_pending_interaction_answer(isolated_env):
    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        controller._runtime.interaction_coordinator.publish(
            InteractionRequest(
                interaction_id="int_chat_1",
                question="请选择",
                options=[
                    InteractionOption(index=1, option_id="a", label="A"),
                    InteractionOption(index=2, option_id="b", label="B"),
                ],
                allow_custom_input=True,
                conversation_id="session.s1",
                run_id="run_chat",
                trace_id="trace_chat",
                session_id=str(controller.state.session_ref or ""),
                provider_id="claude",
            )
        )

        assert controller.has_pending_interaction() is True
        pending_text = controller.interaction_pending_text()
        assert "请选择" in pending_text
        assert "/choose" in pending_text

        result_text = controller.submit_interaction_answer("2", source="local")
        assert "交互回答已提交" in result_text
        assert controller.has_pending_interaction() is False
    finally:
        controller.close()


def test_chat_controller_busy_reject_message_when_task_running(isolated_env):
    controller = ChatController(provider="claude", yes=True, context_id="default")
    try:
        started = controller._runtime.task_coordinator.start_task(
            run_id="run_busy",
            conversation_id="session.busy",
            session_id=str(controller.state.session_ref or ""),
        )
        assert started is True
        text = controller.busy_reject_message()
        assert "执行中" in text or "等待确认" in text
    finally:
        controller.close()
