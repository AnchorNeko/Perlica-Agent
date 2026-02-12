from __future__ import annotations

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runtime import Runtime
from perlica.ui.render import render_doctor_text


def test_doctor_report_includes_debug_log_status(tmp_path):
    initialize_project_config(workspace_dir=tmp_path)
    runtime = Runtime(load_settings(context_id="doctor-logs", workspace_dir=tmp_path))
    try:
        runtime.emit(
            "inbound.message.received",
            {"text": "doctor logs"},
            conversation_id="session.doctor",
        )
        report = runtime.doctor(verbose=True)
    finally:
        runtime.close()

    assert "logs_enabled" in report
    assert "logs_dir" in report
    assert "logs_active_file" in report
    assert "logs_active_size_bytes" in report
    assert "logs_max_file_bytes" in report
    assert "logs_max_files" in report
    assert "logs_total_size_bytes" in report
    assert "logs_rotated_files" in report
    assert "logs_write_errors" in report

    rendered = render_doctor_text(report)
    assert "调试日志 (Debug Logs)" in rendered
    assert "logs_enabled=" in rendered
    assert "logs_write_errors=" in rendered
