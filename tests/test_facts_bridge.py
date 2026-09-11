"""Мост jarvis-brain → память Hermes Holographic (memory_store.db) и миграция старых заметок."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from conftest import load_plugin

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("JARVIS_FACTS_DB", str(tmp_path / "memory_store.db"))
    mod = load_plugin("jarvis-brain")
    mod._brain = None
    mod._vault = None
    mod.facts.reset()
    yield mod
    mod.facts.reset()
    mod._vault = None
    if mod._brain:
        mod._brain.close()
        mod._brain = None


def test_remember_mirrors_fact_and_recall_merges(brain, tmp_path):
    r = json.loads(brain.tool_brain_remember({"content": "Пользователь пьёт эспрессо без сахара", "kind": "preference", "entity": "Кофе", "tags": "food"}))
    assert r["success"] and isinstance(r["fact_id"], int)
    c = sqlite3.connect(tmp_path / "memory_store.db")
    row = c.execute("SELECT content, category, tags, trust_score FROM facts").fetchone()
    c.close()
    assert row[0] == "Пользователь пьёт эспрессо без сахара" and row[1] == "user_pref"
    assert row[2] == "jarvis:preference,Кофе,food" and abs(row[3] - 0.8) < 0.01  # trust = уверенность заметки
    # повтор не дублирует
    brain.tool_brain_remember({"content": "Пользователь пьёт эспрессо без сахара", "kind": "preference"})
    assert brain.facts.count() == 1
    # факт, который есть только у Hermes (записан через fact_store), находится через brain_recall
    brain.facts.store().add_fact("Пользователь работает по вторникам из дома", category="general")
    out = json.loads(brain.tool_brain_recall({"query": "работа из дома вторник"}))
    assert out["success"] and any("вторникам" in f["content"] for f in out["hermes_facts"])
    st = json.loads(brain.tool_brain_review({"action": "stats"}))["stats"]["hermes_memory"]
    assert st["provider"] == "holographic" and st["facts"] == 2


def test_context_defers_to_provider_when_active(brain):
    brain.tool_brain_remember({"content": "Пользователь любит тёмную тему в редакторах", "kind": "preference"})
    brain.tool_brain_remember({"content": "Анна — тимлид проекта Atlas", "kind": "person", "entity": "Анна"})
    ctx = brain.build_memory_context("какую тему любит пользователь в редакторе Атлас Анна")
    # без карточки — отдаём провайдеру (он сам подмешает факт); с карточкой — остаётся у нас
    assert "Анна" in ctx and "тёмную тему" not in ctx


def test_inactive_without_provider(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_FACTS_DB", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    mod = load_plugin("jarvis-brain")
    mod.facts.reset()
    assert mod.facts.active() is False  # вне Hermes и без явного пути — мост выключен
    assert mod.facts.db_path() == tmp_path / "memory_store.db"


def test_migration_script_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("brain_db_m", ROOT / "plugins" / "jarvis-brain" / "db.py")
    db = importlib.util.module_from_spec(spec); spec.loader.exec_module(db)
    b = db.Brain(tmp_path / "brain.db")
    b.remember("Пользователь живёт в Цюрихе", kind="place", entity="Дом", confidence=0.9)
    b.remember("Проект Atlas пишется на Go", kind="project", entity="Atlas")
    old = b.remember("Пользователь ездит на велосипеде", kind="habit")
    b.forget(old["id"], reason="test") if hasattr(b, "forget") else b._conn.execute("UPDATE notes SET status='archived' WHERE id=?", (old["id"],))
    b.close()
    spec = importlib.util.spec_from_file_location("migrate_brain", ROOT / "scripts" / "migrate_brain.py")
    mig = importlib.util.module_from_spec(spec); spec.loader.exec_module(mig)
    facts_db = tmp_path / "memory_store.db"
    rep = mig.migrate(tmp_path / "brain.db", facts_db)
    assert rep["notes"] == 2 and rep["added"] == 2  # архивная заметка не переносится
    rep2 = mig.migrate(tmp_path / "brain.db", facts_db)
    assert rep2["added"] == 0 and rep2["existing"] == 2 and rep2["total_facts"] == 2
    c = sqlite3.connect(facts_db)
    rows = {r[0]: r for r in c.execute("SELECT content, category, tags, trust_score FROM facts")}
    c.close()
    assert rows["Пользователь живёт в Цюрихе"][1:] == ("general", "jarvis:place,Дом", pytest.approx(0.9, abs=0.01))
    assert rows["Проект Atlas пишется на Go"][1] == "project"
    # CLI-вход
    assert mig.main(["--brain", str(tmp_path / "brain.db"), "--facts", str(facts_db), "--json"]) == 0
    assert mig.main(["--brain", str(tmp_path / "missing.db"), "--json"]) == 0


def test_dashboard_api_overview(tmp_path, monkeypatch):
    """plugin_api.py отдаёт brain + facts из read-only снимков (FastAPI не нужен для проверки логики — подменяем APIRouter)."""
    import sys
    import types
    fake = types.ModuleType("fastapi")

    class APIRouter:
        def __init__(self): self.routes = {}
        def get(self, path):
            def deco(fn): self.routes[path] = fn; return fn
            return deco
    fake.APIRouter = APIRouter
    monkeypatch.setitem(sys.modules, "fastapi", fake)
    monkeypatch.setenv("JARVIS_BRAIN_DB", str(tmp_path / "brain.db"))
    monkeypatch.setenv("JARVIS_FACTS_DB", str(tmp_path / "memory_store.db"))
    spec = importlib.util.spec_from_file_location("brain_db_d", ROOT / "plugins" / "jarvis-brain" / "db.py")
    db = importlib.util.module_from_spec(spec); spec.loader.exec_module(db)
    b = db.Brain(tmp_path / "brain.db"); b.remember("Пользователь любит зелёный чай", kind="preference", entity="Чай"); b.close()
    spec = importlib.util.spec_from_file_location("jarvis_facts_d", ROOT / "plugins" / "jarvis-brain" / "facts.py")
    facts = importlib.util.module_from_spec(spec); spec.loader.exec_module(facts)
    s = facts.LocalFactStore(tmp_path / "memory_store.db"); s.add_fact("Пользователь любит зелёный чай", "user_pref", "jarvis:preference,Чай"); s.close()
    spec = importlib.util.spec_from_file_location("jarvis_plugin_api", ROOT / "plugins" / "jarvis-brain" / "dashboard" / "plugin_api.py")
    api = importlib.util.module_from_spec(spec); spec.loader.exec_module(api)
    import asyncio
    out = asyncio.run(api.router.routes["/overview"](q="чай"))
    assert out["brain"]["ok"] and out["brain"]["search"][0]["entity"] == "Чай"
    assert out["facts"]["ok"] and out["facts"]["facts"] == 1 and out["facts"]["search"][0]["category"] == "user_pref"
    assert out["paths"]["facts_db"].endswith("memory_store.db")
