from __future__ import annotations

from perlica.service.store import ServiceStore


def _store(isolated_env) -> ServiceStore:
    return ServiceStore(isolated_env["config_root"] / "service" / "service_bridge.db")


def test_pairing_code_roundtrip(isolated_env):
    store = _store(isolated_env)
    try:
        code = store.create_pairing_code("imessage", ttl_seconds=120)
        assert len(code) == 6
        assert store.get_active_pairing_code("imessage") == code
        assert store.consume_pairing_code("imessage", code) is True
        assert store.consume_pairing_code("imessage", code) is False
    finally:
        store.close()


def test_binding_and_cursor_persistence(isolated_env):
    store = _store(isolated_env)
    try:
        binding = store.set_binding(
            "imessage",
            contact_id="+8613800138000",
            chat_id="chat-1",
            session_id="sess_demo",
        )
        assert binding.paired is True
        assert binding.contact_id == "+8613800138000"
        assert binding.chat_id == "chat-1"
        assert binding.session_id == "sess_demo"

        store.set_cursor("imessage", "evt_123")
        assert store.get_cursor("imessage") == "evt_123"

        cleared = store.clear_binding("imessage")
        assert cleared.paired is False
        assert cleared.contact_id is None
        assert cleared.chat_id is None
        assert cleared.session_id is None
    finally:
        store.close()
