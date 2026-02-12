from __future__ import annotations

from pathlib import Path

import pytest

from perlica.config import initialize_project_config, resolve_project_config_root


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(workspace)
    initialize_project_config(workspace_dir=workspace)

    return {
        "workspace": workspace,
        "config_root": resolve_project_config_root(workspace),
    }
