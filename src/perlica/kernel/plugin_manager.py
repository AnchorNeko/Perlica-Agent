"""Plugin discovery and manifest validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import tomli


REQUIRED_MANIFEST_FIELDS = [
    "id",
    "name",
    "version",
    "kind",
    "entry",
    "core_api",
    "capabilities",
    "requires",
]


@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    kind: str
    entry: str
    core_api: str
    capabilities: List[str]
    requires: List[str]
    plugin_dir: Path
    raw: Dict[str, object] = field(default_factory=dict)


@dataclass
class PluginLoadReport:
    loaded: Dict[str, PluginManifest] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)

    @property
    def loaded_count(self) -> int:
        return len(self.loaded)

    @property
    def failed_count(self) -> int:
        return len(self.failed)


class PluginManager:
    """Loads plugin manifests from configured directories.

    This MVP validates manifests and dependency graph; it does not execute plugin code.
    """

    def __init__(self, plugin_roots: List[Path], core_major: int = 2) -> None:
        self._plugin_roots = plugin_roots
        self._core_major = core_major

    def load(self) -> PluginLoadReport:
        manifests, failures = self._scan_manifests()

        cycle_nodes = self._detect_cycles(manifests)
        for plugin_id in cycle_nodes:
            failures[plugin_id] = "plugin dependency cycle detected"
            manifests.pop(plugin_id, None)

        report = PluginLoadReport(loaded=manifests, failed=failures)
        return report

    def _scan_manifests(self) -> Tuple[Dict[str, PluginManifest], Dict[str, str]]:
        manifests: Dict[str, PluginManifest] = {}
        failures: Dict[str, str] = {}

        for root in self._plugin_roots:
            if not root.exists() or not root.is_dir():
                continue
            for plugin_dir in sorted(root.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                plugin_toml = plugin_dir / "plugin.toml"
                if not plugin_toml.exists():
                    continue

                try:
                    raw = tomli.loads(plugin_toml.read_text(encoding="utf-8"))
                except Exception as exc:  # pragma: no cover - defensive
                    failures[plugin_dir.name] = "manifest parse error: {0}".format(exc)
                    continue

                missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in raw]
                if missing:
                    failures[plugin_dir.name] = "missing manifest fields: {0}".format(
                        ", ".join(missing)
                    )
                    continue

                plugin_id = str(raw["id"])
                if plugin_id in manifests:
                    # Higher-priority root is loaded first, so duplicates from later roots are ignored.
                    continue

                if not self._core_api_compatible(str(raw["core_api"])):
                    failures[plugin_id] = "core_api not compatible with core major {0}".format(
                        self._core_major
                    )
                    continue

                entry_module = str(raw["entry"]).split(":", 1)[0]
                entry_file = plugin_dir / (entry_module.replace(".", "/") + ".py")
                if not entry_file.exists():
                    failures[plugin_id] = "entry target not found: {0}".format(entry_file.name)
                    continue

                manifests[plugin_id] = PluginManifest(
                    plugin_id=plugin_id,
                    name=str(raw["name"]),
                    version=str(raw["version"]),
                    kind=str(raw["kind"]),
                    entry=str(raw["entry"]),
                    core_api=str(raw["core_api"]),
                    capabilities=[str(item) for item in list(raw.get("capabilities") or [])],
                    requires=[str(item) for item in list(raw.get("requires") or [])],
                    plugin_dir=plugin_dir,
                    raw=dict(raw),
                )

        return manifests, failures

    def _core_api_compatible(self, core_api: str) -> bool:
        """MVP semantic check for ranges like >=2.0,<3.0."""

        major = self._core_major
        parts = [part.strip() for part in core_api.split(",") if part.strip()]
        lower_ok = True
        upper_ok = True

        for part in parts:
            if part.startswith(">="):
                lower_major = int(part[2:].split(".", 1)[0])
                lower_ok = major >= lower_major
            elif part.startswith("<"):
                upper_major = int(part[1:].split(".", 1)[0])
                upper_ok = major < upper_major

        return lower_ok and upper_ok

    @staticmethod
    def _detect_cycles(manifests: Dict[str, PluginManifest]) -> Set[str]:
        graph: Dict[str, List[str]] = {}
        for plugin_id, manifest in manifests.items():
            deps = [dep for dep in manifest.requires if dep in manifests]
            graph[plugin_id] = deps

        visiting: Set[str] = set()
        visited: Set[str] = set()
        cycle_nodes: Set[str] = set()

        def walk(node: str, stack: List[str]) -> None:
            if node in visited:
                return
            if node in visiting:
                if node in stack:
                    cycle_nodes.update(stack[stack.index(node) :])
                return

            visiting.add(node)
            stack.append(node)
            for dep in graph.get(node, []):
                walk(dep, stack)
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for node in list(graph.keys()):
            walk(node, [])

        return cycle_nodes
