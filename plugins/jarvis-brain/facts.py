"""Мост к встроенному провайдеру памяти Hermes «Holographic» (plugins/memory/holographic в репозитории Hermes).

Принцип «сначала готовое»: долговременные ФАКТЫ хранит официальный провайдер Hermes — локальный SQLite
`$HERMES_HOME/memory_store.db` с FTS5, trust-оценками и инструментами `fact_store` / `fact_feedback`.
Он же сам подмешивает релевантные факты в каждый ход (prefetch) и виден из Desktop, dashboard и любого чата.

jarvis-brain оставляет себе то, чего у провайдера нет: карточки-сущности с типами и связями, дневник дней,
историю изменений факта, журнал сбоев, ночную ревизию и хранилище файлов. Чтобы два слоя не расходились,
каждая запись `brain_remember` зеркалится сюда (см. `add`), а `brain_recall` ищет и там, и там.

Реализация: если мы работаем внутри процесса Hermes — используем его собственный класс `MemoryStore`
(общее соединение с провайдером, одна блокировка записи). Вне Hermes (миграция, тесты) — `LocalFactStore`,
минимальная совместимая запись в ту же таблицу по той же схеме.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# jarvis kind → категория провайдера (enum fact_store: user_pref | project | tool | general)
KIND_TO_CATEGORY = {
    "preference": "user_pref", "habit": "user_pref",
    "project": "project", "decision": "project", "goal": "project",
    "howto": "tool", "device": "tool",
}

# Схема таблицы фактов провайдера Holographic (копия из hermes-agent/plugins/memory/holographic/store.py).
# CREATE … IF NOT EXISTS — Hermes при первом запуске просто дополнит недостающее (entities, banks).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hrr_vector      BLOB
);
CREATE INDEX IF NOT EXISTS idx_facts_trust    ON facts(trust_score DESC);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(content, tags, content=facts, content_rowid=fact_id);
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags) VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags) VALUES (new.fact_id, new.content, new.tags);
END;
"""


def hermes_home() -> Path:
    return Path(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"))


def hermes_memory_config() -> tuple[str, dict]:
    """(memory.provider, plugins.hermes-memory-store) из конфига Hermes; вне Hermes — ('', {})."""
    try:
        from hermes_cli.config import cfg_get, load_config_readonly  # type: ignore

        cfg = load_config_readonly()
        return str(cfg_get(cfg, "memory", "provider", default="") or ""), dict(cfg_get(cfg, "plugins", "hermes-memory-store", default={}) or {})
    except Exception:
        return "", {}


def db_path() -> Path:
    """Путь к memory_store.db: JARVIS_FACTS_DB → plugins.hermes-memory-store.db_path → $HERMES_HOME/memory_store.db."""
    env = os.environ.get("JARVIS_FACTS_DB")
    if env:
        return Path(env).expanduser()
    _, store_cfg = hermes_memory_config()
    p = str(store_cfg.get("db_path") or "")
    home = str(hermes_home())
    p = p.replace("$HERMES_HOME", home).replace("${HERMES_HOME}", home)
    return Path(p).expanduser() if p else hermes_home() / "memory_store.db"


def active() -> bool:
    """Провайдер Holographic включён (или путь задан явно через JARVIS_FACTS_DB)."""
    if os.environ.get("JARVIS_FACTS_DB"):
        return True
    provider, _ = hermes_memory_config()
    return provider == "holographic"


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w\-]+", (text or "").lower()) if len(t) > 1][:12]


class LocalFactStore:
    """Минимальный совместимый доступ к таблице facts, когда класс Hermes недоступен (миграция вне процесса, тесты)."""

    def __init__(self, path: Path, default_trust: float = 0.5):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_trust = default_trust
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def add_fact(self, content: str, category: str = "general", tags: str = "") -> int:
        content = content.strip()
        if not content:
            raise ValueError("content must not be empty")
        with self._lock:
            try:
                return int(self._conn.execute("INSERT INTO facts(content, category, tags, trust_score) VALUES (?,?,?,?)",
                                              (content, category, tags, self.default_trust)).lastrowid)
            except sqlite3.IntegrityError:
                return int(self._conn.execute("SELECT fact_id FROM facts WHERE content=?", (content,)).fetchone()["fact_id"])

    def update_fact(self, fact_id: int, content: str | None = None, trust_delta: float | None = None,
                    tags: str | None = None, category: str | None = None) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT trust_score FROM facts WHERE fact_id=?", (fact_id,)).fetchone()
            if not row:
                return False
            sets, vals = ["updated_at=CURRENT_TIMESTAMP"], []
            for col, val in (("content", content), ("tags", tags), ("category", category)):
                if val is not None:
                    sets.append(f"{col}=?"); vals.append(val)
            if trust_delta is not None:
                sets.append("trust_score=?"); vals.append(max(0.0, min(1.0, row["trust_score"] + trust_delta)))
            self._conn.execute(f"UPDATE facts SET {', '.join(sets)} WHERE fact_id=?", [*vals, fact_id])
            return True

    def remove_fact(self, fact_id: int) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM facts WHERE fact_id=?", (fact_id,)).rowcount > 0

    def list_facts(self, category: str | None = None, min_trust: float = 0.0, limit: int = 50) -> list[dict]:
        with self._lock:
            sql = "SELECT fact_id, content, category, tags, trust_score, created_at, updated_at FROM facts WHERE trust_score>=?"
            params: list = [min_trust]
            if category:
                sql += " AND category=?"; params.append(category)
            return [dict(r) for r in self._conn.execute(sql + " ORDER BY trust_score DESC, updated_at DESC LIMIT ?", [*params, limit])]

    def search(self, query: str, limit: int = 10, min_trust: float = 0.0) -> list[dict]:
        toks = _tokens(query)
        if not toks:
            return []
        match = " OR ".join(f'"{t}"*' for t in toks)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """SELECT f.fact_id, f.content, f.category, f.tags, f.trust_score, f.updated_at,
                              -bm25(facts_fts) * f.trust_score AS score
                       FROM facts_fts JOIN facts f ON f.fact_id = facts_fts.rowid
                       WHERE facts_fts MATCH ? AND f.trust_score >= ? ORDER BY score DESC LIMIT ?""", (match, min_trust, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            return [dict(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])

    def close(self) -> None:
        self._conn.close()


_store = None
_store_lock = threading.Lock()


def _hermes_store(path: Path, default_trust: float):
    """Класс провайдера Hermes — общий пул соединений с самим провайдером (без «database is locked»)."""
    try:
        from plugins.memory.holographic.store import MemoryStore  # type: ignore  # пакет из репозитория Hermes
    except Exception:
        return None
    try:
        return MemoryStore(db_path=str(path), default_trust=default_trust)
    except Exception as e:  # любой сбой провайдера не должен ронять плагин
        logger.debug("MemoryStore недоступен: %s", e)
        return None


def store():
    """Единый экземпляр хранилища фактов: класс Hermes, если доступен, иначе LocalFactStore."""
    global _store
    with _store_lock:
        if _store is None:
            _, cfg = hermes_memory_config()
            trust = float(cfg.get("default_trust") or 0.5)
            path = db_path()
            _store = _hermes_store(path, trust) or LocalFactStore(path, trust)
        return _store


def reset() -> None:
    """Сбросить кэш соединения (тесты, смена пути)."""
    global _store
    with _store_lock:
        if _store is not None and isinstance(_store, LocalFactStore):
            _store.close()
        _store = None


def fact_tags(kind: str, entity: str | None, tags: str) -> str:
    parts = [f"jarvis:{kind or 'fact'}"]
    if entity:
        parts.append(entity.strip())
    parts += [t.strip() for t in (tags or "").split(",") if t.strip()]
    return ",".join(dict.fromkeys(parts))  # без дублей, порядок сохранён


def add(content: str, kind: str = "fact", entity: str | None = None, tags: str = "", confidence: float | None = None) -> int | None:
    """Зеркалить заметку в факты Hermes. Идемпотентно (content UNIQUE). Возвращает fact_id или None при сбое."""
    try:
        s = store()
        fid = s.add_fact(content.strip(), category=KIND_TO_CATEGORY.get(kind or "", "general"), tags=fact_tags(kind, entity, tags))
        if confidence is not None:
            cur = next((f for f in s.list_facts(limit=100000) if f["fact_id"] == fid), None)  # list_facts есть у обоих классов
            if cur is not None and abs(float(cur["trust_score"]) - float(confidence)) > 0.01:
                s.update_fact(fid, trust_delta=float(confidence) - float(cur["trust_score"]))
        return int(fid)
    except Exception as e:
        logger.debug("facts.add: %s", e)
        return None


def search(query: str, limit: int = 6) -> list[dict]:
    """Поиск по фактам провайдера. У класса Hermes нет search — используем FTS напрямую через LocalFactStore на том же файле."""
    try:
        s = store()
        if isinstance(s, LocalFactStore):
            return s.search(query, limit=limit)
        return LocalFactStore(db_path()).search(query, limit=limit)
    except Exception as e:
        logger.debug("facts.search: %s", e)
        return []


def count() -> int:
    try:
        s = store()
        return s.count() if isinstance(s, LocalFactStore) else LocalFactStore(db_path()).count()
    except Exception:
        return 0
