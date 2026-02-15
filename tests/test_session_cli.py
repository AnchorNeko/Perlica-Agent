from __future__ import annotations

from typer.testing import CliRunner

import perlica.cli


def _extract_created_session_id(text: str) -> str:
    # Format: "已创建会话 (Created session): <id> name=... provider=..."
    return text.split("Created session):", 1)[1].strip().split()[0]


def test_session_new_current_use_list(isolated_env):
    runner = CliRunner()

    created = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "alpha"],
    )
    assert created.exit_code == 0
    session_id = _extract_created_session_id(created.stdout)

    current = runner.invoke(perlica.cli.app, ["session", "current"])
    assert current.exit_code == 0
    assert session_id in current.stdout
    assert "name=alpha" in current.stdout

    listed = runner.invoke(perlica.cli.app, ["session", "list"])
    assert listed.exit_code == 0
    assert session_id in listed.stdout

    created_b = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "beta", "--provider", "claude"],
    )
    assert created_b.exit_code == 0

    used = runner.invoke(perlica.cli.app, ["session", "use", "alpha"])
    assert used.exit_code == 0
    assert "name=alpha" in used.stdout


def test_session_list_all_contexts(isolated_env):
    runner = CliRunner()

    default_created = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "default_ctx"],
    )
    assert default_created.exit_code == 0

    other_created = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "other_ctx", "--context", "other", "--provider", "claude"],
    )
    assert other_created.exit_code == 0

    listed_all = runner.invoke(perlica.cli.app, ["session", "list", "--all"])
    assert listed_all.exit_code == 0
    assert "context=default" in listed_all.stdout
    assert "context=other" in listed_all.stdout


def test_session_delete_rejects_current_and_allows_other(isolated_env):
    runner = CliRunner()

    created_alpha = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "alpha"],
    )
    assert created_alpha.exit_code == 0

    created_beta = runner.invoke(
        perlica.cli.app,
        ["session", "new", "--name", "beta"],
    )
    assert created_beta.exit_code == 0

    use_alpha = runner.invoke(perlica.cli.app, ["session", "use", "alpha"])
    assert use_alpha.exit_code == 0

    reject_current = runner.invoke(perlica.cli.app, ["session", "delete", "alpha"])
    assert reject_current.exit_code == 2
    assert "禁止删除当前会话" in reject_current.stdout

    delete_other = runner.invoke(perlica.cli.app, ["session", "delete", "beta"])
    assert delete_other.exit_code == 0
    assert "会话已删除" in delete_other.stdout

    listed = runner.invoke(perlica.cli.app, ["session", "list"])
    assert listed.exit_code == 0
    assert "name=alpha" in listed.stdout
    assert "name=beta" not in listed.stdout
