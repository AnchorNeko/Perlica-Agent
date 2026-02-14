from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime


def _live_enabled() -> bool:
    return str(os.getenv("PERLICA_LIVE_CLAUDE") or "").strip() == "1"


@pytest.mark.skipif(not _live_enabled(), reason="set PERLICA_LIVE_CLAUDE=1 to run live claude tests")
def test_claude_real_hello(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
        result = runner.run_text("你好", assume_yes=True)
        assert str(result.assistant_text or "").strip()
    finally:
        runtime.close()

