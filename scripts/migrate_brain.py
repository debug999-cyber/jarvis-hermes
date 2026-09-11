#!/usr/bin/env python3
"""Перенос заметок JARVIS (brain.db) в память Hermes «Holographic» (memory_store.db) — без потерь.

    python3 migrate_brain.py [--brain PATH] [--facts PATH] [--dry-run] [--json]

Что делает:
  • берёт все АКТИВНЫЕ заметки из brain.db (kind, карточка, теги, уверенность);
  • кладёт каждую в таблицу facts провайдера Holographic: category по типу заметки, теги вида
    `jarvis:<kind>,<карточка>,<теги>`, trust = уверенность заметки. Повторный запуск ничего не дублирует
    (content UNIQUE) — можно запускать сколько угодно;
  • brain.db не трогает: карточки, дневник, история и связи остаются там и продолжают работать.

Запуск: `jarvis brain migrate` (или install.sh делает это сам при обновлении, если brain.db уже есть).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HERMES_HOME = Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))


def _load_facts_module():
    """plugins/jarvis-brain/facts.py — из репозитория (рядом со scripts/) или из установленного плагина."""
    import importlib.util

    for cand in (HERE.parent / "plugins" / "jarvis-brain" / "facts.py", HERMES_HOME / "plugins" / "jarvis-brain" / "facts.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("jarvis_facts", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
    raise SystemExit("не найден plugins/jarvis-brain/facts.py — выполните jarvis update")


def default_brain_db() -> Path:
    base = os.environ.get("JARVIS_BRAIN_DIR") or os.environ.get("JARVIS_STATE_DIR")
    return Path(base) / "brain.db" if base else HERMES_HOME / "plugin-data" / "jarvis-brain" / "brain.db"


def read_notes(brain_db: Path) -> list[dict]:
    c = sqlite3.connect(f"file:{brain_db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            """SELECT n.id, n.content, n.kind, n.tags, n.confidence, n.importance, e.name AS entity
               FROM notes n LEFT JOIN entities e ON e.id = n.entity_id
               WHERE n.status = 'active' ORDER BY n.id"""
        ).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def migrate(brain_db: Path, facts_db: Path, dry_run: bool = False) -> dict:
    facts = _load_facts_module()
    notes = read_notes(brain_db) if brain_db.exists() else []
    report = {"brain_db": str(brain_db), "facts_db": str(facts_db), "notes": len(notes), "added": 0, "existing": 0, "skipped": 0, "dry_run": dry_run}
    if not notes:
        return report
    store = facts.LocalFactStore(facts_db)
    try:
        before = store.count()
        max_id_before = store._conn.execute("SELECT COALESCE(MAX(fact_id), 0) FROM facts").fetchone()[0]
        seen_ids = set()
        for n in notes:
            content = " ".join((n["content"] or "").split())
            if len(content) < 3:
                report["skipped"] += 1
                continue
            if dry_run:
                continue
            fid = store.add_fact(content, category=facts.KIND_TO_CATEGORY.get(n["kind"] or "", "general"),
                                 tags=facts.fact_tags(n["kind"], n["entity"], n["tags"] or ""))
            if fid in seen_ids:
                report["skipped"] += 1
                continue
            seen_ids.add(fid)
            if fid > max_id_before:  # только что созданный факт: trust 0.5 → уверенность заметки. Существующие не трогаем (их уже оценивал Hermes)
                conf = float(n["confidence"] if n["confidence"] is not None else 0.8)
                store.update_fact(fid, trust_delta=conf - 0.5)
        report["added"] = max(0, store.count() - before)
        report["existing"] = len(notes) - report["added"] - report["skipped"]
        if not dry_run:
            report["total_facts"] = store.count()
    finally:
        store.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--brain", type=Path, default=default_brain_db())
    ap.add_argument("--facts", type=Path, default=None, help="memory_store.db (по умолчанию из конфига Hermes / $HERMES_HOME)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    facts = _load_facts_module()
    facts_db = a.facts or facts.db_path()
    if not a.brain.exists():
        print(json.dumps({"ok": True, "notes": 0, "reason": "brain.db ещё нет — переносить нечего"}, ensure_ascii=False) if a.json
              else "База знаний ещё не создана — переносить нечего.")
        return 0
    rep = migrate(a.brain, facts_db, dry_run=a.dry_run)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False))
    else:
        print(f"Заметок в brain.db: {rep['notes']}  →  в память Hermes: добавлено {rep['added']}, уже было {rep['existing']}, пропущено {rep['skipped']}"
              + (" (пробный прогон)" if a.dry_run else f"\nВсего фактов: {rep.get('total_facts', '?')}  ·  файл: {facts_db}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
