from __future__ import annotations

import pytest

from perlica.tui import app as tui_app
from perlica.tui import widgets as tui_widgets


def test_tui_has_enter_submit_binding():
    if not tui_app.textual_available():
        pytest.skip("textual not available")

    bindings = list(getattr(tui_app.PerlicaChatApp, "BINDINGS", []))
    normalized = {(getattr(x, "key", ""), getattr(x, "action", ""), bool(getattr(x, "priority", False))) for x in bindings}

    assert ("ctrl+c", "cancel_generation", False) in normalized
    assert ("ctrl+d", "request_exit", False) in normalized
    assert ("ctrl+l", "clear_chat", False) in normalized
    assert hasattr(tui_app.PerlicaChatApp, "on_text_area_changed")
    assert "on_key" not in getattr(tui_app.PerlicaChatApp, "__dict__", {})


def test_input_key_classification_in_chat_input():
    classify = tui_widgets.classify_chat_input_key

    assert classify("enter") == "submit"
    assert classify("ctrl+s") == "submit"
    assert classify("ctrl+j") == "newline"
    assert classify("shift+enter") == "newline"
    assert classify("x") == ""
