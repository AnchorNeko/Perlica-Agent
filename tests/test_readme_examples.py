from __future__ import annotations

from pathlib import Path


def test_readme_contains_interactive_first_examples():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")

    assert "perlica init" in text
    assert "perlica" in text
    assert "perlica chat" in text
    assert "perlica --service" in text
    assert "/service status" in text
    assert "/service channel use imessage" in text
    assert "/clear" in text
    assert "/pending" in text
    assert "/choose <index|text...>" in text
    assert "/mcp list" in text
    assert "/mcp reload" in text
    assert "/mcp status" in text
    assert "/model set claude" not in text
    assert "/session new --name demo" in text
    assert "/save demo" in text
    assert 'perlica "帮我总结今天待办"' in text
    assert "echo \"你好，帮我总结日志\" | perlica" in text
    assert "发送与接收" in text
    assert "开始新对话" in text
    assert "你可以通过 iMessage 联系到" in text
    assert "仅处理 `is_from_me=0`" in text
    assert ".perlica_config/prompts/system.md" in text
    assert ".perlica_config/mcp/servers.toml" in text
