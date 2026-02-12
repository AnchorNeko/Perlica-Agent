from __future__ import annotations

from perlica.service.presentation import map_service_event_to_view
from perlica.service.types import ServiceEvent


def test_service_event_presentation_for_inbound_ack_reply():
    inbound = map_service_event_to_view(
        ServiceEvent(
            kind="inbound",
            text="hello",
            channel="imessage",
        )
    )
    assert "inbound" in inbound.title.lower()
    assert "Remote" in inbound.phase

    ack = map_service_event_to_view(
        ServiceEvent(
            kind="ack",
            text="已收到🫡",
            channel="imessage",
        )
    )
    assert "ACK" in ack.title

    reply = map_service_event_to_view(
        ServiceEvent(
            kind="reply",
            text="最终回复",
            channel="imessage",
        )
    )
    assert "Reply" in reply.title


def test_service_event_presentation_for_telemetry():
    telemetry = map_service_event_to_view(
        ServiceEvent(
            kind="telemetry",
            text="line",
            channel="imessage",
            meta={"event_type": "listener.raw_line", "direction": "inbound"},
        )
    )
    assert "telemetry" in telemetry.title
    assert "listener.raw_line" in telemetry.text


def test_service_event_presentation_hides_polled_and_ignored_noise():
    polled = map_service_event_to_view(
        ServiceEvent(
            kind="telemetry",
            text="poll 捕获到 8 条消息。",
            channel="imessage",
            meta={
                "event_type": "inbound.polled",
                "direction": "inbound",
            },
        )
    )
    ignored = map_service_event_to_view(
        ServiceEvent(
            kind="telemetry",
            text="忽略重复事件。",
            channel="imessage",
            meta={
                "event_type": "inbound.ignored",
                "direction": "inbound",
                "reason": "contact_mismatch",
                "bound_contact": "a@example.com",
                "inbound_contact": "b@example.com",
            },
        )
    )
    assert polled is None
    assert ignored is None
