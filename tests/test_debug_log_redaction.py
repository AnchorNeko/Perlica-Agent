from __future__ import annotations

import json

from perlica.kernel.debug_log import DebugLogWriter


def test_debug_log_default_redaction_masks_sensitive_keys_and_values(tmp_path):
    writer = DebugLogWriter(
        logs_dir=tmp_path / "logs",
        enabled=True,
        log_format="jsonl",
        max_file_bytes=1024 * 1024,
        max_files=2,
        redaction="default",
    )

    writer.write_entry(
        level="info",
        component="provider",
        kind="diagnostic",
        context_id="ctx-redact",
        message="Authorization: Bearer top-secret token=abc123 sk-1234567890ABCDEF",
        data={
            "token": "abc123",
            "nested": {
                "api_key": "sk-foo",
                "normal": "ok",
                "authorization": "Bearer hidden",
            },
            "headers": {"Cookie": "session=xyz"},
        },
    )

    lines = (tmp_path / "logs" / "debug.log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])

    assert "***REDACTED***" in row["message"]
    assert "top-secret" not in row["message"]
    assert "abc123" not in row["message"]
    assert row["data"]["token"] == "***REDACTED***"
    assert row["data"]["nested"]["api_key"] == "***REDACTED***"
    assert row["data"]["nested"]["authorization"] == "***REDACTED***"
    assert row["data"]["headers"]["Cookie"] == "***REDACTED***"
    assert row["data"]["nested"]["normal"] == "ok"
