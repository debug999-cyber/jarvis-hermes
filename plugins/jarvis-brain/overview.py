"""Снимок базы знаний и памяти Hermes — только чтение. Общий код для HUD (`/api/brain`) и вкладки «База знаний»
в web-панели Hermes (`dashboard/plugin_api.py`). Открывает SQLite в режиме `mode=ro`, поэтому безопасен из любого процесса."""

from __future__ import annotations

import os
import sqlite3


def brain_overview(path: str, query: str = "", limit: int = 12) -> dict:
    """Снимок базы знаний. Только чтение; при отсутствии файла — пустой ответ."""
    if not os.path.exists(path):
        return {"ok": False, "reason": "no database yet"}
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        c.row_factory = sqlite3.Row
        one = lambda sql: c.execute(sql).fetchone()[0]
        out = {
            "ok": True,
            "notes": one("SELECT COUNT(*) FROM notes WHERE status='active'"),
            "entities": one("SELECT COUNT(*) FROM entities WHERE status='active'"),
            "episodes": one("SELECT COUNT(*) FROM episodes"),
            "pending_turns": one("SELECT COUNT(*) FROM turns WHERE digested=0"),
            "last_review": c.execute("SELECT finished_at, report FROM reviews ORDER BY id DESC LIMIT 1").fetchone(),
            "kinds": [dict(r) for r in c.execute(
                "SELECT kind, COUNT(*) AS n FROM notes WHERE status='active' GROUP BY kind ORDER BY n DESC")],
            "top_entities": [dict(r) for r in c.execute(
                """SELECT e.name, e.kind, COUNT(n.id) AS notes FROM entities e LEFT JOIN notes n ON n.entity_id=e.id AND n.status='active'
                   WHERE e.status='active' GROUP BY e.id ORDER BY notes DESC, e.updated_at DESC LIMIT ?""", (limit,))],
            "recent": [dict(r) for r in c.execute(
                """SELECT n.id, n.kind, n.content, n.importance, e.name AS entity FROM notes n LEFT JOIN entities e ON e.id=n.entity_id
                   WHERE n.status='active' ORDER BY n.updated_at DESC LIMIT ?""", (limit,))],
            "diary": [dict(r) for r in c.execute("SELECT day, summary FROM episodes ORDER BY day DESC LIMIT 5")],
        }
        # схема v2 (журнал сбоев, история фактов) — опционально, старые базы без этих колонок тоже работают
        try:
            out["failures"] = [dict(r) for r in c.execute(
                "SELECT tool, count, message FROM failures WHERE resolved=0 ORDER BY count DESC, last_seen DESC LIMIT 5")]
            out["history"] = [dict(r) for r in c.execute(
                """SELECT n.content, substr(n.valid_from,1,10) AS valid_from, substr(n.valid_until,1,10) AS valid_until FROM notes n
                   WHERE n.status='superseded' ORDER BY n.valid_until DESC LIMIT 5""")]
        except sqlite3.Error:
            out["failures"], out["history"] = [], []
        try:  # хранилище файлов
            out["vault"] = {"files": one("SELECT COUNT(*) FROM files WHERE status='ok'"),
                            "sources": [dict(r) for r in c.execute("SELECT name, path FROM vault_sources ORDER BY name")],
                            "recent": [dict(r) for r in c.execute("SELECT rel, indexed_at FROM files WHERE status='ok' ORDER BY mtime DESC LIMIT 5")]}
        except sqlite3.Error:
            out["vault"] = None
        if out["last_review"]:
            out["last_review"] = dict(out["last_review"])
        if query.strip():
            like = f"%{query.strip()}%"
            out["search"] = [dict(r) for r in c.execute(
                """SELECT n.id, n.kind, n.content, e.name AS entity FROM notes n LEFT JOIN entities e ON e.id=n.entity_id
                   WHERE n.status='active' AND (n.content LIKE ? OR n.tags LIKE ?) ORDER BY n.importance DESC LIMIT ?""",
                (like, like, limit))]
        c.close()
        return out
    except sqlite3.Error as e:
        return {"ok": False, "reason": str(e)[:120]}


def facts_overview(path: str, query: str = "", limit: int = 12) -> dict:
    """Факты провайдера Hermes Holographic (memory_store.db): счётчик, категории, свежие и поиск."""
    if not path or not os.path.exists(path):
        return {"ok": False, "reason": "no memory_store yet"}
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
        c.row_factory = sqlite3.Row
        out = {
            "ok": True,
            "facts": c.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
            "categories": [dict(r) for r in c.execute("SELECT category, COUNT(*) AS n FROM facts GROUP BY category ORDER BY n DESC")],
            "recent": [dict(r) for r in c.execute(
                "SELECT fact_id, content, category, tags, round(trust_score,2) AS trust FROM facts ORDER BY updated_at DESC LIMIT ?", (limit,))],
        }
        if query.strip():
            like = f"%{query.strip()}%"
            out["search"] = [dict(r) for r in c.execute(
                "SELECT fact_id, content, category, tags, round(trust_score,2) AS trust FROM facts WHERE content LIKE ? OR tags LIKE ? "
                "ORDER BY trust_score DESC LIMIT ?", (like, like, limit))]
        c.close()
        return out
    except sqlite3.Error as e:
        return {"ok": False, "reason": str(e)[:120]}
