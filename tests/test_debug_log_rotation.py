from __future__ import annotations

from perlica.kernel.debug_log import DebugLogWriter


def test_debug_log_rotation_respects_size_and_max_files(tmp_path):
    writer = DebugLogWriter(
        logs_dir=tmp_path / "logs",
        enabled=True,
        log_format="jsonl",
        max_file_bytes=256,
        max_files=2,
        redaction="none",
    )

    for idx in range(40):
        writer.write_entry(
            level="info",
            component="runtime",
            kind="diagnostic",
            context_id="ctx-rotation",
            message="rotation-{0}".format(idx),
            data={"blob": "x" * 80, "idx": idx},
        )

    status = writer.status()
    assert status["logs_enabled"] is True
    assert status["logs_active_size_bytes"] > 0
    assert status["logs_max_file_bytes"] == 256
    assert status["logs_max_files"] == 2
    assert len(status["logs_rotated_files"]) <= 2
    assert not (tmp_path / "logs" / "debug.log.jsonl.3").exists()


def test_debug_log_fail_open_tracks_write_errors(tmp_path):
    blocked_path = tmp_path / "not-a-dir"
    blocked_path.write_text("file", encoding="utf-8")
    writer = DebugLogWriter(
        logs_dir=blocked_path,
        enabled=True,
        log_format="jsonl",
        max_file_bytes=1024,
        max_files=2,
        redaction="default",
    )

    writer.write_entry(
        level="info",
        component="runtime",
        kind="diagnostic",
        context_id="ctx-fail-open",
        message="should not raise",
        data={"token": "secret"},
    )

    status = writer.status()
    assert status["logs_write_errors"] >= 1
