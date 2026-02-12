from __future__ import annotations

from pathlib import Path

from perlica.kernel.plugin_manager import PluginManager


def _write_plugin(root: Path, name: str, manifest: str, include_entry: bool = True) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(manifest, encoding="utf-8")
    if include_entry:
        (plugin_dir / "main.py").write_text("def register(runtime):\n    return runtime\n", encoding="utf-8")
    return plugin_dir


def test_plugin_loading_and_failure_isolation(tmp_path: Path):
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)

    _write_plugin(
        plugins_root,
        "ok",
        """
id = "ok_plugin"
name = "OK"
version = "0.1.0"
kind = "tool"
entry = "main:register"
core_api = ">=2.0,<3.0"
capabilities = ["tool.ok"]
requires = []
""".strip(),
        include_entry=True,
    )

    _write_plugin(
        plugins_root,
        "broken",
        """
id = "broken_plugin"
name = "Broken"
version = "0.1.0"
kind = "tool"
entry = "main:register"
core_api = ">=2.0,<3.0"
capabilities = ["tool.broken"]
requires = []
""".strip(),
        include_entry=False,
    )

    report = PluginManager([plugins_root]).load()
    assert "ok_plugin" in report.loaded
    assert "broken_plugin" in report.failed


def test_plugin_cycle_detection(tmp_path: Path):
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)

    _write_plugin(
        plugins_root,
        "a",
        """
id = "a"
name = "A"
version = "0.1.0"
kind = "tool"
entry = "main:register"
core_api = ">=2.0,<3.0"
capabilities = ["tool.a"]
requires = ["b"]
""".strip(),
    )

    _write_plugin(
        plugins_root,
        "b",
        """
id = "b"
name = "B"
version = "0.1.0"
kind = "tool"
entry = "main:register"
core_api = ">=2.0,<3.0"
capabilities = ["tool.b"]
requires = ["a"]
""".strip(),
    )

    report = PluginManager([plugins_root]).load()
    assert "a" in report.failed
    assert "b" in report.failed
    assert "a" not in report.loaded
    assert "b" not in report.loaded


def test_plugin_core_api_mismatch(tmp_path: Path):
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir(parents=True, exist_ok=True)

    _write_plugin(
        plugins_root,
        "future",
        """
id = "future_plugin"
name = "Future"
version = "0.1.0"
kind = "tool"
entry = "main:register"
core_api = ">=3.0,<4.0"
capabilities = ["tool.future"]
requires = []
""".strip(),
    )

    report = PluginManager([plugins_root], core_major=2).load()
    assert "future_plugin" in report.failed
