from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime


def _live_enabled() -> bool:
    return str(os.getenv("PERLICA_LIVE_CLAUDE") or "").strip() == "1"


@pytest.mark.skipif(not _live_enabled(), reason="set PERLICA_LIVE_CLAUDE=1 to run live claude tests")
def test_claude_real_multi_round_interaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found in PATH")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace)
    initialize_project_config(workspace_dir=workspace)

    settings = load_settings(context_id="default", provider="claude")
    runtime = Runtime(settings)
    try:
        runner = Runner(
            runtime=runtime,
            provider_id="claude",
            max_tool_calls=runtime.settings.max_tool_calls,
        )
        result_holder = {}
        error_holder = {}

        def _run() -> None:
            try:
                result_holder["result"] = runner.run_text("问一下我的代码偏好", assume_yes=True)
            except Exception as exc:  # pragma: no cover - live path
                error_holder["error"] = exc

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()

        answered_count = 0
        deadline = time.time() + 300
        while worker.is_alive() and time.time() < deadline:
            snapshot = runtime.interaction_coordinator.snapshot()
            if snapshot.has_pending:
                answer_text = "1" if snapshot.options else "我偏好 Python + 简洁风格"
                submit = runtime.interaction_coordinator.submit_answer(answer_text, source="test_live")
                if submit.accepted:
                    answered_count += 1
            time.sleep(0.2)

        worker.join(timeout=5)
        assert not worker.is_alive(), "live interaction test timed out waiting for runner"
        if "error" in error_holder:
            raise error_holder["error"]

        result = result_holder.get("result")
        assert result is not None
        assert str(result.assistant_text or "").strip()
        assert answered_count >= 1
    finally:
        runtime.close()

