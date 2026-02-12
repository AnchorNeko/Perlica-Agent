from __future__ import annotations

import threading
from pathlib import Path

from perlica.config import initialize_project_config, load_settings
from perlica.kernel.runner import Runner
from perlica.kernel.runtime import Runtime
from perlica.kernel.types import LLMResponse


class _FakeProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, req):
        self.calls += 1
        return LLMResponse(assistant_text="ok", tool_calls=[], finish_reason="stop")


def test_runner_can_execute_from_background_thread_without_sqlite_thread_error(tmp_path: Path):
    initialize_project_config(workspace_dir=tmp_path)
    settings = load_settings(context_id="default", provider="codex", workspace_dir=tmp_path)
    runtime = Runtime(settings)
    try:
        provider = _FakeProvider()
        runtime.register_provider(provider)
        session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            provider_locked="fake",
        )
        runner = Runner(runtime=runtime, provider_id="fake", max_tool_calls=2)

        caught = []
        result_holder = []

        def run_target() -> None:
            try:
                result_holder.append(runner.run_text("hello", assume_yes=True, session_ref=session.session_id))
            except Exception as exc:  # pragma: no cover - should stay empty
                caught.append(exc)

        t = threading.Thread(target=run_target, daemon=True)
        t.start()
        t.join(timeout=5.0)

        assert not caught
        assert result_holder
        assert result_holder[0].assistant_text == "ok"
    finally:
        runtime.close()
