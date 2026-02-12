from __future__ import annotations

from perlica.interaction.coordinator import InteractionCoordinator
from perlica.interaction.types import InteractionOption, InteractionRequest


def _request() -> InteractionRequest:
    return InteractionRequest(
        interaction_id="int_1",
        question="请选择",
        options=[
            InteractionOption(index=1, option_id="a", label="选项A"),
            InteractionOption(index=2, option_id="b", label="选项B"),
        ],
        allow_custom_input=True,
        conversation_id="session.s1",
        run_id="run_1",
        trace_id="trace_1",
        session_id="s1",
        provider_id="claude",
    )


def test_publish_submit_and_resolve_emits_lifecycle_events():
    events = []
    coordinator = InteractionCoordinator(event_sink=lambda et, payload: events.append((et, payload)))

    coordinator.publish(_request())
    assert coordinator.has_pending() is True

    result = coordinator.submit_answer("1", source="local")
    assert result.accepted is True
    assert result.answer is not None
    assert result.answer.selected_index == 1
    assert coordinator.has_pending() is False

    answer = coordinator.wait_for_answer("int_1")
    assert answer.selected_option_id == "a"

    coordinator.resolve("int_1")
    assert coordinator.has_pending() is False

    event_types = [name for name, _ in events]
    assert "interaction.requested" in event_types
    assert "interaction.answered" in event_types
    assert "interaction.resolved" in event_types


def test_custom_text_answer_is_allowed_when_enabled():
    coordinator = InteractionCoordinator()
    coordinator.publish(_request())

    result = coordinator.submit_answer("自定义内容", source="remote")
    assert result.accepted is True
    assert result.answer is not None
    assert result.answer.custom_text == "自定义内容"
    assert result.answer.source == "remote"


def test_invalid_index_rejected():
    coordinator = InteractionCoordinator()
    coordinator.publish(_request())

    rejected = coordinator.submit_answer("9", source="local")
    assert rejected.accepted is False
    assert "无效选项" in rejected.message


def test_choice_suggestions_reflect_pending_state():
    coordinator = InteractionCoordinator()
    assert coordinator.choice_suggestions() == []

    coordinator.publish(_request())
    suggestions = coordinator.choice_suggestions()
    assert "1" in suggestions
    assert "2" in suggestions
    assert "<自定义文本>" in suggestions
