"""
Общие фикстуры: имитация PluginContext Hermes и изоляция состояния.

Плагины лежат в каталогах с дефисом (jarvis-core), поэтому импортируем их
через importlib как пакеты с «безопасным» именем — так же делает сам Hermes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


def load_plugin(name: str):
    """Импортировать plugins/<name> как пакет `jarvis_<name>`."""
    pkg_name = "plug_" + name.replace("-", "_")
    if pkg_name in sys.modules:
        return sys.modules[pkg_name]
    spec = importlib.util.spec_from_file_location(
        pkg_name, PLUGINS / name / "__init__.py", submodule_search_locations=[str(PLUGINS / name)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[pkg_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class FakeCtx:
    """Минимальная имитация ctx из Hermes PluginManager."""

    def __init__(self, config: dict | None = None):
        self.tools: dict[str, dict] = {}
        self.hooks: dict[str, list] = {}
        self.commands: dict[str, dict] = {}
        self.skills: dict[str, Path] = {}
        self.injected: list = []
        self._config = config or {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert name == schema["name"], f"имя инструмента {name} != schema.name {schema['name']}"
        assert callable(handler)
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_hook(self, name, fn):
        assert callable(fn)
        self.hooks.setdefault(name, []).append(fn)

    def register_command(self, name, fn, description=""):
        self.commands[name] = {"fn": fn, "description": description}

    def register_skill(self, name, path):
        self.skills[name] = Path(path)

    def inject_message(self, content, role="user", **kw):
        self.injected.append((role, content))

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def set_config(self, key, value):
        self._config[key] = value


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Состояние jarvis-core (таймеры/режим) — во временную папку."""
    monkeypatch.setenv("JARVIS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("JARVIS_CACHE_DIR", str(tmp_path / "cache"))
    yield


@pytest.fixture
def ctx():
    return FakeCtx()
