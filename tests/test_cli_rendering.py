from __future__ import annotations

from io import StringIO

from perlica.kernel.runner import RunnerResult
from perlica.kernel.types import LLMCallUsage, UsageTotals
from perlica.ui.render import preview_rendered_run_meta, render_assistant_panel


def _result() -> RunnerResult:
    return RunnerResult(
        assistant_text="你好\nHello",
        run_id="run_1",
        trace_id="trace_1",
        conversation_id="conv_1",
        session_id="sess_1",
        session_name="demo",
        provider_id="claude",
        context_usage={
            "history_messages_included": 3,
            "summary_versions_used": 1,
            "estimated_context_tokens": 123,
        },
        llm_call_usages=[
            LLMCallUsage(
                call_index=1,
                provider_id="claude",
                input_tokens=10,
                cached_input_tokens=2,
                output_tokens=4,
            )
        ],
        total_usage=UsageTotals(input_tokens=10, cached_input_tokens=2, output_tokens=4),
    )


def test_assistant_panel_non_tty_uses_box():
    stream = StringIO()
    render_assistant_panel("第一行\n第二行", stream=stream, is_tty=False)
    text = stream.getvalue()

    assert "助手回复 (Assistant)" in text
    assert "+-" in text
    assert "| 第一行" in text
    assert "| 第二行" in text


def test_run_meta_sections_present():
    rendered = preview_rendered_run_meta(_result())
    assert "会话信息 (Session)" in rendered
    assert "上下文使用 (Context Usage)" in rendered
    assert "Token 总计 (Token Usage Total)" in rendered
    assert "Token 分调用 (Token Usage By Call)" in rendered
    assert "provider=claude" in rendered
