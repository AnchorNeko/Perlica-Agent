from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from perlica.config import initialize_project_config
from perlica.config import load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse, ToolCall


class FakeProvider:
    provider_id = "fake"

    def __init__(self, responses):
        self.responses = list(responses)

    def generate(self, req):
        return self.responses.pop(0)


def _runtime(tmp_path: Path) -> Runtime:
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="test", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    return runtime


def _osascript(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def _notes_count_by_title(title: str) -> int:
    result = _osascript(
        'tell application "Notes"\n'
        '    return count of (every note whose name is "{0}")\n'
        "end tell\n".format(title)
    )
    if result.returncode != 0:
        raise AssertionError("failed to query Notes count: {0}".format(result.stderr.strip()))
    value = (result.stdout or "").strip()
    return int(value or "0")


def _delete_notes_by_title(title: str) -> None:
    result = _osascript(
        'tell application "Notes"\n'
        '    delete (every note whose name is "{0}")\n'
        "end tell\n".format(title)
    )
    if result.returncode != 0:
        raise AssertionError("failed to cleanup Notes: {0}".format(result.stderr.strip()))


@pytest.mark.skipif(
    sys.platform != "darwin" or os.getenv("PERLICA_REAL_NOTES_TEST") != "1",
    reason="Set PERLICA_REAL_NOTES_TEST=1 on macOS to run real Notes write integration test.",
)
def test_real_notes_write_executes_once_per_run(isolated_env, tmp_path: Path):
    title = "perlica-real-write-{0}".format(uuid.uuid4().hex[:8])
    body_first = "first-write-{0}".format(uuid.uuid4().hex[:8])
    body_second = "second-write-{0}".format(uuid.uuid4().hex[:8])

    cmd_first = (
        "osascript -e 'tell application \"Notes\" to "
        "make new note at folder \"Notes\" with properties "
        "{{name:\"{0}\", body:\"{1}\"}}'".format(title, body_first)
    )
    cmd_second = (
        "osascript -e 'tell application \"Notes\" to "
        "make new note at folder \"Notes\" with properties "
        "{{name:\"{0}\", body:\"{1}\"}}'".format(title, body_second)
    )

    start_count = _notes_count_by_title(title)

    runtime = _runtime(tmp_path)
    try:
        provider = FakeProvider(
            [
                LLMResponse(
                    assistant_text="write twice",
                    tool_calls=[
                        ToolCall(call_id="c1", tool_name="shell.exec", arguments={"cmd": cmd_first}, risk_tier="low"),
                        ToolCall(call_id="c2", tool_name="shell.exec", arguments={"cmd": cmd_second}, risk_tier="low"),
                    ],
                    finish_reason="tool_calls",
                ),
            ]
        )
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )

        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=4)
        result = runner.run_text("create note once", assume_yes=True, session_ref=session.session_id)

        assert len(result.tool_results) == 2
        assert result.tool_results[0].error == "single_call_mode_local_tool_dispatch_disabled"
        assert result.tool_results[1].error == "single_call_mode_local_tool_dispatch_disabled"

        observed_count = start_count
        deadline = time.time() + 6
        while time.time() < deadline:
            observed_count = _notes_count_by_title(title)
            if observed_count > start_count:
                break
            time.sleep(0.3)
        assert observed_count == start_count
    finally:
        try:
            _delete_notes_by_title(title)
        finally:
            runtime.close()
