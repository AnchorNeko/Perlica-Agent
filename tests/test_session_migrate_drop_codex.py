from __future__ import annotations

from perlica.config import load_settings
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_migration import drop_sessions_by_provider


def test_drop_codex_sessions_removes_data_and_fixes_current_pointer(isolated_env):
    settings = load_settings(context_id="default")
    runtime = Runtime(settings)
    try:
        codex_session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="legacy-codex",
            provider_locked="codex",
            is_ephemeral=False,
        )
        claude_session = runtime.session_store.create_session(
            context_id=runtime.context_id,
            name="stable-claude",
            provider_locked="claude",
            is_ephemeral=False,
        )
        runtime.session_store.set_current_session(runtime.context_id, codex_session.session_id)

        report = drop_sessions_by_provider(runtime.session_store, "codex")

        assert report.deleted_sessions >= 1
        assert runtime.session_store.get_session(codex_session.session_id) is None

        current = runtime.session_store.get_current_session(runtime.context_id)
        assert current is not None
        assert current.session_id == claude_session.session_id
    finally:
        runtime.close()
