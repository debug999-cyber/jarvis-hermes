"""Маршруты вкладки «База знаний» в web-панели Hermes (`hermes dashboard`, порт 9119).

Монтируются Hermes под /api/plugins/jarvis-brain/ и защищены его же авторизацией.
Только чтение: используем общий модуль overview.py (SQLite mode=ro). Панель — официальный интерфейс Hermes,
поэтому здесь нет своего сервера, своей авторизации и своего React — только данные.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
_HERE = Path(__file__).resolve().parent
_HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))


def _overview():
    spec = importlib.util.spec_from_file_location("jarvis_brain_overview", _HERE.parent / "overview.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _brain_db() -> str:
    env = os.environ.get("JARVIS_BRAIN_DB")
    if env:
        return env
    try:
        from plugins.plugin_storage import plugin_data_dir  # type: ignore

        return str(plugin_data_dir("jarvis-brain") / "brain.db")
    except Exception:
        return str(_HOME / "plugin-data" / "jarvis-brain" / "brain.db")


def _facts_db() -> str:
    env = os.environ.get("JARVIS_FACTS_DB")
    if env:
        return env
    try:
        from hermes_cli.config import cfg_get, load_config_readonly  # type: ignore

        p = str(cfg_get(load_config_readonly(), "plugins", "hermes-memory-store", "db_path", default="") or "")
        if p:
            return p.replace("$HERMES_HOME", str(_HOME)).replace("${HERMES_HOME}", str(_HOME))
    except Exception:
        pass
    return str(_HOME / "memory_store.db")


@router.get("/overview")
async def overview(q: str = "", limit: int = 12):
    mod = _overview()
    return {"brain": mod.brain_overview(_brain_db(), q, limit), "facts": mod.facts_overview(_facts_db(), q, limit),
            "paths": {"brain_db": _brain_db(), "facts_db": _facts_db()}}
