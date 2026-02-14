from __future__ import annotations

import subprocess

import pytest

from perlica.service.channels.imessage_adapter import IMessageChannelAdapter
from perlica.service.types import ChannelOutboundMessage


def test_parse_inbound_json_line():
    adapter = IMessageChannelAdapter()
    line = '{"text":"/help","from":"+8613800138000","chat_id":"chat-a","event_id":"evt-1","is_from_me":false}'
    messages = adapter._parse_inbound_line(line)
    assert len(messages) == 1
    message = messages[0]

    assert message is not None
    assert message.text == "/help"
    assert message.contact_id == "+8613800138000"
    assert message.chat_id == "chat-a"
    assert message.event_id == "evt-1"
    assert message.is_from_me is False


def test_parse_nested_watch_payload():
    adapter = IMessageChannelAdapter()
    line = (
        '{\"event\":\"message\",'
        '\"message\":{\"body\":\"/pair 123456\",\"from\":{\"id\":\"1023620928@qq.com\"},'
        '\"chat\":{\"rowid\":3},\"isFromMe\":0,\"rowid\":901}}'
    )
    messages = adapter._parse_inbound_line(line)
    assert len(messages) == 1
    message = messages[0]
    assert message.text == "/pair 123456"
    assert message.contact_id == "1023620928@qq.com"
    assert message.chat_id == "3"
    assert message.event_id == "901"
    assert message.is_from_me is False


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"text": "x", "from": "a@example.com", "is_from_me": False}, False),
        ({"text": "x", "from": "a@example.com", "is_from_me": 0}, False),
        ({"text": "x", "from": "a@example.com", "is_from_me": "0"}, False),
        ({"text": "x", "from": "a@example.com", "is_from_me": "false"}, False),
        ({"text": "x", "from": "a@example.com", "is_from_me": True}, True),
        ({"text": "x", "from": "a@example.com", "is_from_me": 1}, True),
        ({"text": "x", "from": "a@example.com", "is_from_me": "1"}, True),
        ({"text": "x", "from": "a@example.com", "is_from_me": "true"}, True),
        (
            {"text": "x", "from": "a@example.com", "message": {"isFromMe": 0}},
            False,
        ),
        (
            {"text": "x", "from": "a@example.com", "message": {"isFromMe": 1}},
            True,
        ),
    ],
)
def test_extract_from_me_matrix(payload, expected):
    adapter = IMessageChannelAdapter()
    message = adapter._payload_to_message(payload)
    assert message is not None
    assert message.is_from_me is expected


def test_missing_or_unknown_is_from_me_defaults_to_true_strict_mode():
    adapter = IMessageChannelAdapter()

    missing_flag = adapter._payload_to_message(
        {"text": "hello", "from": "strict@example.com"}
    )
    unknown_flag = adapter._payload_to_message(
        {"text": "hello", "from": "strict@example.com", "is_from_me": "unknown"}
    )

    assert missing_flag is not None
    assert missing_flag.is_from_me is True
    assert unknown_flag is not None
    assert unknown_flag.is_from_me is True


def test_created_at_iso_timestamp_is_parsed_to_ms():
    adapter = IMessageChannelAdapter()
    message = adapter._payload_to_message(
        {
            "text": "hello",
            "from": "time@example.com",
            "created_at": "2026-02-08T11:41:03.670Z",
            "is_from_me": False,
        }
    )
    assert message is not None
    assert message.ts_ms == 1770550863670


def test_default_listen_command_uses_watch():
    adapter = IMessageChannelAdapter()
    assert adapter._listen_args[0] == "watch"
    cmd = adapter._build_listen_command()
    assert "--chat-id" not in cmd


def test_listen_command_includes_chat_scope():
    adapter = IMessageChannelAdapter()
    adapter.set_chat_scope("3")
    cmd = adapter._build_listen_command()
    assert "--chat-id" in cmd
    assert "3" in cmd


def test_listen_command_without_scope_has_no_chat_id():
    adapter = IMessageChannelAdapter()
    cmd = adapter._build_listen_command()
    assert "--chat-id" not in cmd


def test_parse_chat_id_from_json_chats_output():
    adapter = IMessageChannelAdapter()
    chats = adapter._parse_chats_output('[{\"id\":7,\"participants\":[\"a@b.com\"]}]', max_chats=5)
    assert chats[0][0] == "7"


def test_poll_for_pairing_code_from_history(monkeypatch):
    adapter = IMessageChannelAdapter()

    def fake_run(cmd, capture_output, text, check):
        if cmd[:2] == ["imsg", "chats"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[3]  (1023620928@qq.com) last=...", stderr="")
        if cmd[:2] == ["imsg", "history"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='[{\"id\":20,\"text\":\"/pair 577849\",\"chat_id\":3,\"sender\":\"1023620928@qq.com\",\"is_from_me\":false}]',
                stderr="",
            )
        raise AssertionError("unexpected command: {0}".format(cmd))

    monkeypatch.setattr(subprocess, "run", fake_run)
    matched = adapter.poll_for_pairing_code("577849", max_chats=3)
    assert matched is not None
    assert matched.chat_id == "3"
    assert matched.contact_id == "1023620928@qq.com"


def test_probe_requires_binary(monkeypatch):
    adapter = IMessageChannelAdapter(binary="imsg")
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(RuntimeError):
        adapter.probe()


def test_send_message_invokes_imsg(monkeypatch):
    calls = []

    def fake_which(_name):
        return "/usr/local/bin/imsg"

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = IMessageChannelAdapter(binary="imsg")
    outbound = ChannelOutboundMessage(
        channel="imessage",
        text="hello",
        contact_id="+8613800138000",
        chat_id="chat-a",
    )
    adapter.send_message(outbound)

    assert calls
    cmd = calls[0]
    assert cmd[0] == "imsg"
    assert "--chat-id" in cmd
    assert "--to" not in cmd
    assert "--text" in cmd
    assert "hello" in cmd


def test_send_message_falls_back_to_contact_when_chat_id_send_fails(monkeypatch):
    calls = []

    def fake_which(_name):
        return "/usr/local/bin/imsg"

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)
        if "--chat-id" in cmd:
            return subprocess.CompletedProcess(cmd, 2, stdout="permissionDenied", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"status":"sent"}', stderr="")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = IMessageChannelAdapter(binary="imsg")
    outbound = ChannelOutboundMessage(
        channel="imessage",
        text="hello",
        contact_id="1023620928@qq.com",
        chat_id="3",
    )
    adapter.send_message(outbound)

    assert len(calls) == 2
    assert "--chat-id" in calls[0]
    assert "--to" in calls[1]


def test_send_message_emits_outbound_telemetry(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/imsg")
    events = []

    def fake_run(cmd, capture_output, text, check):
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = IMessageChannelAdapter(binary="imsg")
    adapter.set_telemetry_sink(lambda event: events.append(event))
    adapter.send_message(
        ChannelOutboundMessage(
            channel="imessage",
            text="hello",
            contact_id="+8613800138000",
            chat_id="chat-a",
        )
    )
    assert any(item.event_type == "outbound.sent" for item in events)


def test_stop_listener_without_active_watch_emits_no_stopped_telemetry():
    adapter = IMessageChannelAdapter(binary="imsg")
    events = []
    adapter.set_telemetry_sink(lambda event: events.append(event))
    adapter.stop_listener()
    assert not any(item.event_type == "listener.stopped" for item in events)


def test_poll_recent_messages_filters_choose_from_me_with_mismatched_sender(monkeypatch):
    adapter = IMessageChannelAdapter()

    monkeypatch.setattr(
        adapter,
        "_list_recent_chats",
        lambda max_chats=8: [("4", "bound@example.com")],
    )
    monkeypatch.setattr(
        adapter,
        "_history_messages",
        lambda chat_id, contact_hint, limit: [
            adapter._payload_to_message(
                {
                    "text": "/choose 1",
                    "sender": "self@local.invalid",
                    "chat_id": "4",
                    "id": "evt-choose",
                    "is_from_me": True,
                    "created_at": "2026-02-08T11:41:03.670Z",
                }
            ),
        ],
    )

    messages = adapter.poll_recent_messages(
        contact_id="bound@example.com",
        chat_id="4",
        since_ts_ms=None,
        max_chats=2,
        limit_per_chat=8,
    )
    assert messages == []


def test_poll_recent_messages_filters_non_command_from_me_mismatch(monkeypatch):
    adapter = IMessageChannelAdapter()

    monkeypatch.setattr(
        adapter,
        "_list_recent_chats",
        lambda max_chats=8: [("4", "bound@example.com")],
    )
    monkeypatch.setattr(
        adapter,
        "_history_messages",
        lambda chat_id, contact_hint, limit: [
            adapter._payload_to_message(
                {
                    "text": "普通文本",
                    "sender": "self@local.invalid",
                    "chat_id": "4",
                    "id": "evt-text",
                    "is_from_me": True,
                    "created_at": "2026-02-08T11:41:03.670Z",
                }
            ),
        ],
    )

    messages = adapter.poll_recent_messages(
        contact_id="bound@example.com",
        chat_id="4",
        since_ts_ms=None,
        max_chats=2,
        limit_per_chat=8,
    )
    assert messages == []
