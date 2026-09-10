"""
Хранилище «мозга» JARVIS — SQLite + FTS5.

Почему SQLite: один файл, транзакции, полнотекстовый поиск из коробки, ноль зависимостей,
легко бэкапить и смотреть любым просмотрщиком. Файл: $HERMES_HOME/plugin-data/jarvis-brain/brain.db

Модель данных (см. SCHEMA):
  kinds      — таксономия: типы знаний (fact, preference, person, project…). Агент может её менять.
  entities   — «карточки»: люди, проекты, устройства, места… к которым привязываются заметки.
  notes      — атомарные знания. Имеют важность, уверенность, теги, источник, срок годности,
               счётчик обращений; не удаляются, а архивируются / заменяются (superseded_by).
  relations  — связи между карточками (works_at, part_of, owns…).
  turns      — сырой журнал ходов диалога (обрезанный), ждёт ночной «переварки».
  episodes   — дневник: по одному резюме на день, получается из turns ночью.
  changelog  — кто/когда/что изменил (agent | nightly | user) — полный аудит.
  reviews    — отчёты ночных ревизий.
  notes_fts  — FTS5-индекс по заметкам (внешний контент, синхронизируется триггерами).

Все публичные функции потокобезопасны (один RLock на процесс) и не бросают наружу
ничего, кроме BrainError с человекочитаемым текстом.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 2

DEFAULT_KINDS = {
    "fact": "Устойчивый факт о пользователе или мире, не подходящий под другие типы",
    "preference": "Предпочтение, вкус, привычный выбор («любит тёмную тему», «кофе без сахара»)",
    "person": "Сведения о человеке из окружения пользователя",
    "project": "Рабочий или личный проект, его состояние и договорённости",
    "place": "Место: дом, офис, любимые заведения, маршруты",
    "habit": "Регулярное поведение, распорядок дня",
    "decision": "Принятое решение и его причина",
    "howto": "Как пользователь любит, чтобы что-то делалось; проверенные рецепты/команды",
    "goal": "Цель или намерение на будущее",
    "idea": "Идея, которую пользователь хотел бы не потерять",
    "event": "Значимое событие с датой (прошедшее или будущее)",
    "device": "Устройства, аккаунты, окружение (без секретов!)",
    "insight": "Вывод более высокого уровня, полученный ночной рефлексией из нескольких эпизодов/фактов (паттерн, а не факт)",
}

# Что никогда не должно попасть в базу — редактируется при записи и в журнале ходов
_SECRET_PATTERNS = [
    re.compile(r"\b(sk|rk|pk|ghp|gho|xox[abp]|AKIA|AIza)[-_A-Za-z0-9]{16,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"(?i)\b(password|passwd|пароль|token|токен|api[_ -]?key|secret)\b\s*[:=—-]\s*\S+"),
    re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),  # карты
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----"),
]


def redact(text: str) -> tuple[str, bool]:
    """Заменить похожее на секреты на «[скрыто]». Возвращает (текст, были_ли_замены)."""
    out, hit = text or "", False
    for pat in _SECRET_PATTERNS:
        out, n = pat.subn("[скрыто]", out)
        hit = hit or n > 0
    return out, hit

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS kinds(
    name TEXT PRIMARY KEY, description TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS entities(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, kind TEXT NOT NULL DEFAULT 'topic',
    summary TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'active', merged_into INTEGER,
    created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY, content TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', entity_id INTEGER,
    tags TEXT DEFAULT '', importance INTEGER DEFAULT 3, confidence REAL DEFAULT 0.8, source TEXT DEFAULT 'agent',
    status TEXT DEFAULT 'active', superseded_by INTEGER, access_count INTEGER DEFAULT 0, last_accessed TEXT,
    valid_from TEXT, valid_until TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS notes_status_kind ON notes(status, kind);
CREATE INDEX IF NOT EXISTS notes_entity ON notes(entity_id);
CREATE TABLE IF NOT EXISTS relations(
    id INTEGER PRIMARY KEY, src INTEGER NOT NULL, dst INTEGER NOT NULL, type TEXT NOT NULL, note TEXT DEFAULT '',
    created_at TEXT, UNIQUE(src, dst, type));
CREATE TABLE IF NOT EXISTS turns(
    id INTEGER PRIMARY KEY, session_id TEXT, platform TEXT, user_text TEXT, assistant_text TEXT,
    created_at TEXT, digested INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS turns_digested ON turns(digested, created_at);
CREATE TABLE IF NOT EXISTS episodes(
    id INTEGER PRIMARY KEY, day TEXT UNIQUE, summary TEXT, highlights TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS changelog(
    id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, op TEXT, table_name TEXT, row_id INTEGER, before TEXT, after TEXT);
CREATE TABLE IF NOT EXISTS failures(
    id INTEGER PRIMARY KEY, tool TEXT, error_type TEXT, message TEXT, args TEXT, count INTEGER DEFAULT 1,
    first_seen TEXT, last_seen TEXT, resolved INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS failures_key ON failures(tool, error_type, resolved);
CREATE TABLE IF NOT EXISTS reviews(
    id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, actor TEXT, report TEXT, stats TEXT);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    content, tags, content='notes', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags); END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, tags) VALUES('delete', old.id, old.content, old.tags); END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, content, tags) VALUES('delete', old.id, old.content, old.tags);
    INSERT INTO notes_fts(rowid, content, tags) VALUES (new.id, new.content, new.tags); END;
"""

# Стоп-слова для построения поисковых запросов (ru + en, короткий прагматичный список)
_STOP = set(
    """и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
    меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам
    ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего
    раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы
    нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас
    про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им
    более всегда конечно всю между это эта этих который которые какие мои моих наш ваш свой
    пожалуйста скажи покажи давай нужно хочу хотел сделай открой включи выключи запомни напомни расскажи найди
    the a an and or of to in is it that this for on with as at by be are was were i you he she we they my your me
    his her our their what which who whom how when where why not no yes do does did have has had please can could
    would should will just like want need make open show tell find remember""".split()
)

_WORD = re.compile(r"[^\W\d_]{3,}|\d{2,}", re.UNICODE)


class BrainError(Exception):
    """Ошибка хранилища с понятным текстом."""


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def tokens(text: str) -> list[str]:
    """Значимые слова для поиска/сравнения (нижний регистр, без стоп-слов)."""
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOP]


def fts_query(text: str, max_terms: int = 12) -> str:
    """Собрать FTS5-запрос: длинные слова — по префиксу (грубый стемминг для русского)."""
    terms = []
    for t in tokens(text)[:max_terms]:
        if len(t) >= 6:
            terms.append(f'"{t[: max(5, len(t) - 2)]}"*')
        else:
            terms.append(f'"{t}"')
    return " OR ".join(terms)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def norm_tags(tags) -> str:
    if not tags:
        return ""
    if isinstance(tags, str):
        parts = re.split(r"[,\s]+", tags)
    else:
        parts = list(tags)
    seen, out = set(), []
    for p in parts:
        p = p.strip().lower().lstrip("#")
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ",".join(out)


class Brain:
    """Обёртка над одним файлом SQLite. Один экземпляр на процесс."""

    def __init__(self, path: str | os.PathLike | None = None):
        self._lock = threading.RLock()
        self.path = Path(path) if path else self._default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.has_fts = True
        self._init_schema()

    # ─────────────────────────────── служебное ────────────────────────────

    @staticmethod
    def _default_path() -> Path:
        base = os.environ.get("JARVIS_BRAIN_DIR") or os.environ.get("JARVIS_STATE_DIR")
        if not base:
            try:
                from plugins.plugin_storage import plugin_data_dir  # type: ignore

                base = str(plugin_data_dir("jarvis-brain"))
            except Exception:  # вне Hermes
                base = os.path.join(os.path.expanduser(os.environ.get("HERMES_HOME") or "~/.hermes"), "plugin-data", "jarvis-brain")
        return Path(base) / "brain.db"

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            had_fts = bool(self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notes_fts'").fetchone())
            try:
                self._conn.executescript(FTS_SCHEMA)
                if not had_fts:
                    # индекс только что создан поверх существующих заметок (миграция/восстановление) — построить
                    self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
            except sqlite3.OperationalError:
                self.has_fts = False  # SQLite без FTS5 — деградируем до LIKE
            for name, desc in DEFAULT_KINDS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO kinds(name, description, created_at) VALUES (?,?,?)", (name, desc, now())
                )
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (now(),))
            self._migrate()

    def _migrate(self) -> None:
        """Пошаговые миграции схемы: meta.schema_version → SCHEMA_VERSION."""
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        current = int(row["value"]) if row else 0
        if current == 0:
            self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            return
        def _v2(c):
            cols = {r[1] for r in c.execute("PRAGMA table_info(notes)")}
            if "valid_from" not in cols:
                c.execute("ALTER TABLE notes ADD COLUMN valid_from TEXT")
            c.execute("UPDATE notes SET valid_from=created_at WHERE valid_from IS NULL")

        migrations = {2: _v2}
        for ver in range(current + 1, SCHEMA_VERSION + 1):
            fn = migrations.get(ver)
            if fn:
                fn(self._conn)
            self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(ver),))
            self._log("system", "migrate", "meta", None, after={"schema_version": ver})

    def _log(self, actor: str, op: str, table: str, row_id: int | None, before=None, after=None) -> None:
        self._conn.execute(
            "INSERT INTO changelog(ts, actor, op, table_name, row_id, before, after) VALUES (?,?,?,?,?,?,?)",
            (now(), actor, op, table, row_id,
             json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
             json.dumps(after, ensure_ascii=False, default=str) if after is not None else None),
        )

    def _row(self, table: str, row_id: int) -> dict | None:
        r = self._conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(r) if r else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ─────────────────────────────── таксономия ───────────────────────────

    def kinds(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT k.name, k.description, COUNT(n.id) AS notes
                   FROM kinds k LEFT JOIN notes n ON n.kind=k.name AND n.status='active'
                   GROUP BY k.name ORDER BY notes DESC, k.name"""
            ).fetchall()
            return [dict(r) for r in rows]

    def define_kind(self, name: str, description: str, actor: str = "agent") -> dict:
        name = name.strip().lower()
        if not re.fullmatch(r"[a-z_]{2,32}", name):
            raise BrainError("Имя типа: латиница/подчёркивание, 2–32 символа")
        with self._lock:
            self._conn.execute(
                "INSERT INTO kinds(name, description, created_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET description=excluded.description",
                (name, description, now()),
            )
            self._log(actor, "kind_define", "kinds", None, after={"name": name, "description": description})
        return {"name": name, "description": description}

    def rename_kind(self, old: str, new: str, actor: str = "nightly") -> int:
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM kinds WHERE name=?", (new,)).fetchone():
                self.define_kind(new, f"(переименовано из {old})", actor)
            cur = self._conn.execute("UPDATE notes SET kind=?, updated_at=? WHERE kind=?", (new, now(), old))
            self._conn.execute("DELETE FROM kinds WHERE name=?", (old,))
            self._log(actor, "kind_rename", "kinds", None, before={"name": old}, after={"name": new, "moved": cur.rowcount})
            return cur.rowcount

    # ─────────────────────────────── сущности ─────────────────────────────

    def entity_upsert(self, name: str, kind: str = "topic", summary: str = "", tags="", actor: str = "agent") -> dict:
        name = name.strip()
        if not name:
            raise BrainError("Пустое имя сущности")
        with self._lock:
            row = self._conn.execute("SELECT * FROM entities WHERE name=?", (name,)).fetchone()
            if row:
                before = dict(row)
                new_kind = kind if kind and kind != "topic" else before["kind"]
                new_summary = summary or before["summary"]
                new_tags = norm_tags(tags) or before["tags"]
                if (new_kind, new_summary, new_tags, "active") == (before["kind"], before["summary"], before["tags"], before["status"]):
                    return before  # ничего не изменилось — updated_at не трогаем (по нему судим о свежести резюме)
                self._conn.execute(
                    "UPDATE entities SET kind=?, summary=?, tags=?, updated_at=?, status='active' WHERE id=?",
                    (new_kind, new_summary, new_tags, now(), row["id"]),
                )
                after = self._row("entities", row["id"])
                self._log(actor, "entity_update", "entities", row["id"], before, after)
                return after
            cur = self._conn.execute(
                "INSERT INTO entities(name, kind, summary, tags, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (name, kind or "topic", summary, norm_tags(tags), now(), now()),
            )
            after = self._row("entities", cur.lastrowid)
            self._log(actor, "entity_create", "entities", cur.lastrowid, after=after)
            return after

    def entity_get(self, name_or_id, with_notes: bool = True) -> dict | None:
        with self._lock:
            if isinstance(name_or_id, int) or str(name_or_id).isdigit():
                row = self._conn.execute("SELECT * FROM entities WHERE id=?", (int(name_or_id),)).fetchone()
            else:
                row = self._conn.execute("SELECT * FROM entities WHERE name=?", (str(name_or_id).strip(),)).fetchone()
                if not row:
                    row = self._conn.execute(
                        "SELECT * FROM entities WHERE name LIKE ? AND status='active' ORDER BY updated_at DESC",
                        (f"%{str(name_or_id).strip()}%",),
                    ).fetchone()
            if not row:
                return None
            ent = dict(row)
            if ent.get("merged_into"):
                return self.entity_get(int(ent["merged_into"]), with_notes)
            if with_notes:
                ent["notes"] = [
                    dict(r) for r in self._conn.execute(
                        "SELECT id, content, kind, tags, importance, confidence, updated_at FROM notes "
                        "WHERE entity_id=? AND status='active' ORDER BY importance DESC, updated_at DESC", (ent["id"],)
                    )
                ]
                ent["relations"] = [
                    dict(r) for r in self._conn.execute(
                        """SELECT r.type, e.name AS other, r.note,
                                  CASE WHEN r.src=? THEN 'out' ELSE 'in' END AS direction
                           FROM relations r JOIN entities e ON e.id = CASE WHEN r.src=? THEN r.dst ELSE r.src END
                           WHERE r.src=? OR r.dst=?""", (ent["id"],) * 4
                    )
                ]
            return ent

    def entity_list(self, kind: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            sql = """SELECT e.id, e.name, e.kind, e.summary, e.tags, COUNT(n.id) AS notes, e.updated_at
                     FROM entities e LEFT JOIN notes n ON n.entity_id=e.id AND n.status='active'
                     WHERE e.status='active' {} GROUP BY e.id ORDER BY notes DESC, e.updated_at DESC LIMIT ?"""
            params: list = []
            where = ""
            if kind:
                where, params = "AND e.kind=?", [kind]
            return [dict(r) for r in self._conn.execute(sql.format(where), (*params, limit))]

    def entity_link(self, src: str, dst: str, rel_type: str, note: str = "", actor: str = "agent") -> dict:
        with self._lock:
            a = self.entity_upsert(src, actor=actor)
            b = self.entity_upsert(dst, actor=actor)
            self._conn.execute(
                "INSERT OR IGNORE INTO relations(src, dst, type, note, created_at) VALUES (?,?,?,?,?)",
                (a["id"], b["id"], rel_type.strip().lower(), note, now()),
            )
            self._log(actor, "relation", "relations", None, after={"src": src, "dst": dst, "type": rel_type})
            return {"src": a["name"], "dst": b["name"], "type": rel_type}

    def entity_merge(self, keep: str, drop: str, actor: str = "nightly") -> dict:
        with self._lock:
            k = self.entity_get(keep, with_notes=False)
            d = self.entity_get(drop, with_notes=False)
            if not k or not d or k["id"] == d["id"]:
                raise BrainError("Сущности для слияния не найдены или совпадают")
            self._conn.execute("UPDATE notes SET entity_id=?, updated_at=? WHERE entity_id=?", (k["id"], now(), d["id"]))
            self._conn.execute("UPDATE OR IGNORE relations SET src=? WHERE src=?", (k["id"], d["id"]))
            self._conn.execute("UPDATE OR IGNORE relations SET dst=? WHERE dst=?", (k["id"], d["id"]))
            self._conn.execute("DELETE FROM relations WHERE src=dst")
            summary = (k["summary"] or "") if k["summary"] else (d["summary"] or "")
            self._conn.execute(
                "UPDATE entities SET status='merged', merged_into=?, updated_at=? WHERE id=?", (k["id"], now(), d["id"])
            )
            self._conn.execute("UPDATE entities SET summary=?, updated_at=? WHERE id=?", (summary, now(), k["id"]))
            self._log(actor, "entity_merge", "entities", d["id"], before=d, after={"merged_into": k["id"]})
            return {"kept": k["name"], "dropped": d["name"]}

    # ─────────────────────────────── заметки ──────────────────────────────

    def remember(self, content: str, kind: str = "fact", entity: str | None = None, tags="", importance: int = 3,
                 confidence: float = 0.8, source: str = "agent", valid_until: str | None = None,
                 actor: str | None = None, dedupe_threshold: float = 0.75) -> dict:
        """Сохранить знание. Если очень похожая активная заметка уже есть — обновить её, а не дублировать.

        Порог 0.75 намеренно высокий: в короткой фразе одно отличающееся слово — это обычно значение
        («живёт в Цюрихе» / «живёт в Берне»), и такое должно вернуться как possible_conflicts, а не перезаписаться молча.
        """
        content = " ".join((content or "").split())
        content, had_secret = redact(content)
        if had_secret:
            tags = norm_tags(tags) + ",redacted" if norm_tags(tags) else "redacted"
        if len(content) < 3:
            raise BrainError("Слишком короткая заметка")
        if len(content) > 2000:
            content = content[:2000]
        actor = actor or source
        kind = (kind or "fact").strip().lower()
        importance = max(1, min(5, int(importance or 3)))
        confidence = max(0.0, min(1.0, float(confidence if confidence is not None else 0.8)))
        with self._lock:
            if not self._conn.execute("SELECT 1 FROM kinds WHERE name=?", (kind,)).fetchone():
                self.define_kind(kind, "(создан автоматически)", actor)
            entity_id = None
            if entity:
                # карточка наследует тип заметки, если он «сущностный» (person/project/place/device)
                ent_kind = kind if kind in ("person", "project", "place", "device", "org") else "topic"
                entity_id = self.entity_upsert(entity, kind=ent_kind, actor=actor)["id"]

            # дедупликация + поиск возможных противоречий
            best, best_sim, conflicts = None, 0.0, []
            for cand in self._search_raw(content, limit=6, kinds=None):
                sim = jaccard(content, cand["content"])
                if sim > best_sim:
                    best, best_sim = cand, sim
                same_scope = (entity_id and cand.get("entity_id") == entity_id) or cand["kind"] == kind
                if 0.25 <= sim < dedupe_threshold and same_scope:
                    conflicts.append({"id": cand["id"], "content": cand["content"], "similarity": round(sim, 2)})
            if best and best_sim >= dedupe_threshold:
                before = self._row("notes", best["id"])
                new_tags = norm_tags((before["tags"] + "," + norm_tags(tags)) if before["tags"] else tags)
                self._conn.execute(
                    "UPDATE notes SET content=?, kind=?, entity_id=COALESCE(?, entity_id), tags=?, "
                    "importance=MAX(importance, ?), confidence=?, source=?, valid_until=COALESCE(?, valid_until), "
                    "status='active', updated_at=? WHERE id=?",
                    (content if len(content) >= len(before["content"]) * 0.8 else before["content"], kind, entity_id,
                     new_tags, importance, confidence, source, valid_until, now(), best["id"]),
                )
                after = self._row("notes", best["id"])
                self._log(actor, "note_update", "notes", best["id"], before, after)
                res = {"action": "updated", "id": best["id"], "similarity": round(best_sim, 2), "note": after}
                if after["content"] != before["content"]:
                    res["previous"] = before["content"]
                return res

            if entity_id is None:
                entity_id = self._auto_link(content)
            cur = self._conn.execute(
                "INSERT INTO notes(content, kind, entity_id, tags, importance, confidence, source, valid_until, valid_from, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (content, kind, entity_id, norm_tags(tags), importance, confidence, source, valid_until, now(), now(), now()),
            )
            after = self._row("notes", cur.lastrowid)
            self._log(actor, "note_create", "notes", cur.lastrowid, after=after)
            res = {"action": "created", "id": cur.lastrowid, "note": after}
            if conflicts:
                res["possible_conflicts"] = conflicts[:3]
            if had_secret:
                res["redacted"] = True
            return res

    def _auto_link(self, content: str) -> int | None:
        """Если в тексте упоминается имя существующей карточки (≥3 символов, целым словом) — привязать к ней."""
        low = f" {content.lower()} "
        best = None
        for r in self._conn.execute("SELECT id, name FROM entities WHERE status='active' AND length(name)>=3"):
            name = r["name"].lower()
            # целое слово или начало словоформы (Atlas → Atlas'а, Анна → Анне)
            if re.search(r"(?<![\w])" + re.escape(name[: max(3, len(name) - 1)]) + r"\w{0,3}(?![\w])", low):
                if best is None or len(name) > len(best[1]):
                    best = (r["id"], name)
        return best[0] if best else None

    def supersede(self, old_id: int, new_content: str, actor: str = "agent", **fields) -> dict:
        """Темпоральная замена: старая заметка закрывается (valid_until=now, status=superseded),
        новая создаётся с той же карточкой/типом. История «раньше жил в Цюрихе → теперь в Берне» сохраняется."""
        with self._lock:
            old = self._row("notes", int(old_id))
            if not old:
                raise BrainError(f"Заметка #{old_id} не найдена")
            new = self.remember(new_content, kind=fields.get("kind") or old["kind"],
                                entity=(self._row("entities", old["entity_id"]) or {}).get("name") if old["entity_id"] else fields.get("entity"),
                                tags=fields.get("tags") or old["tags"], importance=fields.get("importance") or old["importance"],
                                confidence=fields.get("confidence", 0.9), source=actor, actor=actor, dedupe_threshold=1.1)
            self._conn.execute(
                "UPDATE notes SET status='superseded', superseded_by=?, valid_until=?, updated_at=? WHERE id=?",
                (new["id"], now(), now(), old_id))
            self._log(actor, "note_supersede", "notes", int(old_id), old, {"superseded_by": new["id"]})
            return {"old_id": int(old_id), "new_id": new["id"], "note": new["note"]}

    def history(self, query: str = "", entity: str | None = None, limit: int = 10) -> list[dict]:
        """Что было верно раньше: закрытые заметки с интервалом валидности («жил в Цюрихе 2024-01 → 2026-09»)."""
        with self._lock:
            ent_id = None
            if entity:
                e = self.entity_get(entity, with_notes=False)
                ent_id = e["id"] if e else -1
            rows = self._search_raw(query, limit, None, ent_id, include_archived=True) if query else [
                dict(r) for r in self._conn.execute(
                    "SELECT * FROM notes WHERE status IN ('superseded','archived','expired') " + ("AND entity_id=? " if ent_id else "") +
                    "ORDER BY updated_at DESC LIMIT ?", ((ent_id, limit) if ent_id else (limit,)))]
            out = []
            for r in rows:
                if r["status"] == "active":
                    continue
                out.append({"id": r["id"], "content": r["content"], "kind": r["kind"], "status": r["status"],
                            "valid_from": (r.get("valid_from") or r["created_at"] or "")[:10], "valid_until": (r.get("valid_until") or r["updated_at"] or "")[:10],
                            "superseded_by": r.get("superseded_by")})
            return out[:limit]

    # ── журнал сбоев инструментов ──
    def log_failure(self, tool: str, error_type: str, message: str, args: str = "") -> int:
        message, _ = redact((message or "")[:500])
        with self._lock:
            row = self._conn.execute(
                "SELECT id, count FROM failures WHERE tool=? AND error_type=? AND resolved=0 AND substr(message,1,80)=? ",
                (tool, error_type or "", message[:80])).fetchone()
            if row:
                self._conn.execute("UPDATE failures SET count=count+1, last_seen=?, args=? WHERE id=?", (now(), args[:300], row["id"]))
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO failures(tool, error_type, message, args, first_seen, last_seen) VALUES (?,?,?,?,?,?)",
                (tool, error_type or "", message, args[:300], now(), now()))
            return cur.lastrowid

    def failures(self, limit: int = 20, include_resolved: bool = False) -> list[dict]:
        with self._lock:
            where = "" if include_resolved else "WHERE resolved=0"
            return [dict(r) for r in self._conn.execute(
                f"SELECT * FROM failures {where} ORDER BY count DESC, last_seen DESC LIMIT ?", (limit,))]

    def resolve_failure(self, failure_id: int, actor: str = "nightly") -> None:
        with self._lock:
            self._conn.execute("UPDATE failures SET resolved=1 WHERE id=?", (int(failure_id),))
            self._log(actor, "failure_resolve", "failures", int(failure_id))

    # ── «ментальные модели»: карточки, чьё резюме пора обновить ──
    def stale_summaries(self, min_notes: int = 3, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.id, e.name, e.kind, e.summary, e.updated_at AS summary_at, COUNT(n.id) AS notes, MAX(n.updated_at) AS last_note
                   FROM entities e JOIN notes n ON n.entity_id=e.id AND n.status='active'
                   WHERE e.status='active' GROUP BY e.id HAVING notes>=? AND (e.summary='' OR MAX(n.updated_at) > e.updated_at)
                   ORDER BY notes DESC LIMIT ?""", (min_notes, limit)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["notes_text"] = [x["content"] for x in self._conn.execute(
                    "SELECT content FROM notes WHERE entity_id=? AND status='active' ORDER BY importance DESC, updated_at DESC LIMIT 12", (r["id"],))]
                out.append(d)
            return out

    def vocabulary(self, limit: int = 60) -> list[str]:
        """Имена карточек и заглавные слова из важных заметок — подсказка для распознавания речи."""
        with self._lock:
            names = [r[0] for r in self._conn.execute(
                """SELECT e.name FROM entities e LEFT JOIN notes n ON n.entity_id=e.id AND n.status='active'
                   WHERE e.status='active' GROUP BY e.id ORDER BY COUNT(n.id) DESC, e.updated_at DESC LIMIT ?""", (limit,))]
            extra = set()
            for (c,) in self._conn.execute("SELECT content FROM notes WHERE status='active' AND importance>=4 LIMIT 100"):
                for w in re.findall(r"\b[A-ZА-ЯЁ][a-zа-яё]{2,}[A-Za-zА-Яа-яЁё0-9]*\b", c[1:]):
                    if w.lower() not in _STOP and w not in ("Пользователь", "Пользователя", "User"):
                        extra.add(w)
            seen, out = set(), []
            for w in names + sorted(extra):
                if w.lower() not in seen:
                    seen.add(w.lower())
                    out.append(w)
            return out[:limit]

    def low_confidence_to_verify(self, limit: int = 2, min_age_days: int = 1) -> list[dict]:
        """Сомнительные факты для уточнения у пользователя (по одному-два в брифинге)."""
        cut = (dt.datetime.now() - dt.timedelta(days=min_age_days)).isoformat()
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                """SELECT id, content, kind, confidence FROM notes WHERE status='active' AND confidence<0.6 AND importance>=3
                   AND created_at<? AND (tags NOT LIKE '%verify_asked%') ORDER BY importance DESC, created_at LIMIT ?""", (cut, limit))]

    def reflect_material(self, question: str, limit: int = 12) -> dict:
        """Всё, что относится к вопросу: заметки, карточки, эпизоды, история — для синтеза ответа моделью."""
        notes = self.recall(question, limit=limit, touch=True)
        ent_names = {n["entity"] for n in notes if n.get("entity")}
        with self._lock:
            for r in self._conn.execute("SELECT name FROM entities WHERE status='active'"):
                if r["name"].lower() in question.lower():
                    ent_names.add(r["name"])
        cards = [self.entity_get(n, with_notes=True) for n in list(ent_names)[:4]]
        return {
            "question": question,
            "notes": notes,
            "entities": [{k: c[k] for k in ("name", "kind", "summary", "relations")} | {"notes": [x["content"] for x in c["notes"][:8]]} for c in cards if c],
            "episodes": self.search_episodes(question, 4),
            "history": self.history(question, limit=5),
        }

    def _search_raw(self, query: str, limit: int, kinds: list[str] | None, entity_id: int | None = None,
                    include_archived: bool = False) -> list[dict]:
        """Поиск без побочных эффектов; возвращает строки notes с полем score."""
        q = fts_query(query)
        status_sql = "" if include_archived else "AND n.status='active'"
        kind_sql = f"AND n.kind IN ({','.join('?' * len(kinds))})" if kinds else ""
        ent_sql = "AND n.entity_id=?" if entity_id else ""
        params: list = []
        if self.has_fts and q:
            sql = f"""SELECT n.*, bm25(notes_fts, 1.0, 0.5) AS rank FROM notes_fts f
                      JOIN notes n ON n.id=f.rowid WHERE notes_fts MATCH ? {status_sql} {kind_sql} {ent_sql}
                      ORDER BY rank LIMIT ?"""
            params = [q, *(kinds or []), *([entity_id] if entity_id else []), limit * 3]
        else:
            toks = tokens(query)[:6]
            if not toks:
                return []
            like = " OR ".join("n.content LIKE ?" for _ in toks)
            sql = f"""SELECT n.*, 0.0 AS rank FROM notes n WHERE ({like}) {status_sql} {kind_sql} {ent_sql}
                      ORDER BY n.importance DESC, n.updated_at DESC LIMIT ?"""
            params = [*(f"%{t}%" for t in toks), *(kinds or []), *([entity_id] if entity_id else []), limit * 3]
        try:
            rows = [dict(r) for r in self._conn.execute(sql, params)]
        except sqlite3.OperationalError:
            return []
        cutoff = (dt.datetime.now() - dt.timedelta(days=30)).isoformat()
        for r in rows:
            score = -float(r.get("rank") or 0.0) + 0.4 * r["importance"] + 0.5 * r["confidence"]
            if (r.get("updated_at") or "") > cutoff:
                score += 0.3
            r["score"] = round(score, 3)
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows[:limit]

    def recall(self, query: str, limit: int = 5, kinds: list[str] | None = None, entity: str | None = None,
               touch: bool = True) -> list[dict]:
        with self._lock:
            ent_id = None
            if entity:
                e = self.entity_get(entity, with_notes=False)
                ent_id = e["id"] if e else -1
            rows = self._search_raw(query, limit, kinds, ent_id) if ent_id != -1 else []
            if touch and rows:
                self._conn.executemany(
                    "UPDATE notes SET access_count=access_count+1, last_accessed=? WHERE id=?",
                    [(now(), r["id"]) for r in rows],
                )
            out = []
            for r in rows:
                ent = self._row("entities", r["entity_id"]) if r.get("entity_id") else None
                out.append({
                    "id": r["id"], "content": r["content"], "kind": r["kind"], "entity": ent["name"] if ent else None,
                    "tags": r["tags"], "importance": r["importance"], "confidence": r["confidence"],
                    "updated_at": r["updated_at"], "score": r["score"],
                })
            return out

    def get_note(self, note_id: int) -> dict | None:
        with self._lock:
            return self._row("notes", int(note_id))

    def update_note(self, note_id: int, actor: str = "agent", **fields) -> dict:
        allowed = {"content", "kind", "tags", "importance", "confidence", "status", "valid_until", "entity"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        with self._lock:
            before = self._row("notes", int(note_id))
            if not before:
                raise BrainError(f"Заметка #{note_id} не найдена")
            if "entity" in fields:
                fields["entity_id"] = self.entity_upsert(fields.pop("entity"), actor=actor)["id"] if fields["entity"] else None
            if "tags" in fields:
                fields["tags"] = norm_tags(fields["tags"])
            if "content" in fields:
                fields["content"] = " ".join(str(fields["content"]).split())[:2000]
            if "kind" in fields and not self._conn.execute("SELECT 1 FROM kinds WHERE name=?", (fields["kind"],)).fetchone():
                self.define_kind(fields["kind"], "(создан автоматически)", actor)
            if not fields:
                return before
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?"
            self._conn.execute(f"UPDATE notes SET {sets} WHERE id=?", (*fields.values(), now(), note_id))
            after = self._row("notes", int(note_id))
            self._log(actor, "note_update", "notes", int(note_id), before, after)
            return after

    def feedback(self, note_ids: list[int], delta: float, actor: str = "feedback") -> list[dict]:
        """Обратная связь по подсказанным заметкам: пользователь подтвердил (+) или опроверг (−) → сдвигаем confidence.
        Ниже 0.6 заметка получает тег verify — ночная ревизия спросит о ней или заархивирует."""
        out = []
        with self._lock:
            for nid in note_ids:
                row = self._row("notes", int(nid))
                if not row or row["status"] != "active":
                    continue
                conf = max(0.0, min(1.0, float(row["confidence"]) + delta))
                tags = set(filter(None, (row["tags"] or "").split(",")))
                if conf < 0.6:
                    tags.add("verify")
                elif delta > 0:
                    tags.discard("verify")
                self._conn.execute("UPDATE notes SET confidence=?, tags=?, updated_at=? WHERE id=?", (conf, ",".join(sorted(tags)), now(), nid))
                self._log(actor, "feedback", "notes", nid, row, {"confidence": conf})
                out.append({"id": nid, "confidence": round(conf, 2), "content": row["content"]})
            self._conn.commit()
        return out

    def forget(self, note_id: int, reason: str = "", actor: str = "agent") -> dict:
        """Мягкое удаление: заметка архивируется, история сохраняется."""
        with self._lock:
            before = self._row("notes", int(note_id))
            if not before:
                raise BrainError(f"Заметка #{note_id} не найдена")
            self._conn.execute("UPDATE notes SET status='archived', updated_at=? WHERE id=?", (now(), note_id))
            self._log(actor, "note_archive", "notes", int(note_id), before, {"reason": reason})
            return {"id": int(note_id), "archived": True}

    def merge_notes(self, keep: int, drop: list[int], content: str | None = None, actor: str = "nightly") -> dict:
        with self._lock:
            k = self._row("notes", int(keep))
            if not k:
                raise BrainError(f"Заметка #{keep} не найдена")
            tags, imp = set(norm_tags(k["tags"]).split(",")) - {""}, k["importance"]
            for d in drop:
                r = self._row("notes", int(d))
                if not r or int(d) == int(keep):
                    continue
                tags |= set(norm_tags(r["tags"]).split(",")) - {""}
                imp = max(imp, r["importance"])
                self._conn.execute(
                    "UPDATE notes SET status='merged', superseded_by=?, updated_at=? WHERE id=?", (keep, now(), d)
                )
                self._log(actor, "note_merge", "notes", int(d), r, {"superseded_by": int(keep)})
            self._conn.execute(
                "UPDATE notes SET content=?, tags=?, importance=?, updated_at=? WHERE id=?",
                (" ".join((content or k["content"]).split())[:2000], ",".join(sorted(tags)), imp, now(), keep),
            )
            return {"kept": int(keep), "merged": [int(d) for d in drop]}

    # ─────────────────────────────── журнал ходов и эпизоды ───────────────

    def log_turn(self, session_id: str, platform: str, user_text: str, assistant_text: str, max_chars: int = 700) -> int:
        u, _ = redact((user_text or "")[:max_chars])
        a, _ = redact((assistant_text or "")[:max_chars])
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO turns(session_id, platform, user_text, assistant_text, created_at) VALUES (?,?,?,?,?)",
                (session_id or "", platform or "", u, a, now()),
            )
            return cur.lastrowid

    def digest_queue(self, max_chars: int = 7000, max_days: int = 3) -> list[dict]:
        """Непереваренные ходы, сгруппированные по дням (для ночного резюме)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, platform, user_text, assistant_text, created_at FROM turns "
                "WHERE digested=0 ORDER BY created_at LIMIT 400"
            ).fetchall()
        days: dict[str, dict] = {}
        used = 0
        for r in rows:
            day = r["created_at"][:10]
            if day not in days:
                if len(days) >= max_days:
                    break
                days[day] = {"day": day, "turns": [], "truncated": False}
            piece = {"t": r["created_at"][11:16], "p": r["platform"], "u": r["user_text"][:300], "a": r["assistant_text"][:300]}
            size = len(piece["u"]) + len(piece["a"])
            if used + size > max_chars:
                days[day]["truncated"] = True
                continue
            used += size
            days[day]["turns"].append(piece)
        return list(days.values())

    def save_episode(self, day: str, summary: str, highlights: str = "", actor: str = "nightly") -> dict:
        with self._lock:
            self._conn.execute(
                "INSERT INTO episodes(day, summary, highlights, created_at, updated_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET summary=excluded.summary, highlights=excluded.highlights, updated_at=excluded.updated_at",
                (day, summary.strip(), highlights.strip(), now(), now()),
            )
            cur = self._conn.execute("UPDATE turns SET digested=1 WHERE digested=0 AND substr(created_at,1,10)=?", (day,))
            self._log(actor, "episode", "episodes", None, after={"day": day, "turns": cur.rowcount})
            return {"day": day, "turns_digested": cur.rowcount}

    def search_episodes(self, query: str, limit: int = 5) -> list[dict]:
        """Поиск по дневнику (LIKE по словам — эпизодов мало, FTS не нужен)."""
        toks = tokens(query)[:6]
        with self._lock:
            if not toks:
                return self.episodes(limit)
            like = " OR ".join("(summary LIKE ? OR highlights LIKE ?)" for _ in toks)
            params = [x for t in toks for x in (f"%{t}%", f"%{t}%")]
            return [dict(r) for r in self._conn.execute(
                f"SELECT day, summary, highlights FROM episodes WHERE {like} ORDER BY day DESC LIMIT ?", (*params, limit))]

    def pending_days(self, limit: int = 10) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute(
                "SELECT DISTINCT substr(created_at,1,10) FROM turns WHERE digested=0 ORDER BY 1 LIMIT ?", (limit,))]

    def episodes(self, limit: int = 7) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT day, summary, highlights FROM episodes ORDER BY day DESC LIMIT ?", (limit,))]

    # ─────────────────────────────── статистика и обслуживание ────────────

    def stats(self) -> dict:
        with self._lock:
            c = self._conn
            one = lambda sql, *p: c.execute(sql, p).fetchone()[0]
            last_review = c.execute("SELECT finished_at, report FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
            return {
                "db_path": str(self.path),
                "db_size_kb": round(self.path.stat().st_size / 1024, 1) if self.path.exists() else 0,
                "fts": self.has_fts,
                "notes_active": one("SELECT COUNT(*) FROM notes WHERE status='active'"),
                "notes_archived": one("SELECT COUNT(*) FROM notes WHERE status!='active'"),
                "entities": one("SELECT COUNT(*) FROM entities WHERE status='active'"),
                "relations": one("SELECT COUNT(*) FROM relations"),
                "turns_pending": one("SELECT COUNT(*) FROM turns WHERE digested=0"),
                "episodes": one("SELECT COUNT(*) FROM episodes"),
                "changes_total": one("SELECT COUNT(*) FROM changelog"),
                "open_failures": one("SELECT COUNT(*) FROM failures WHERE resolved=0"),
                "kinds": self.kinds(),
                "top_entities": self.entity_list(limit=8),
                "last_review": dict(last_review) if last_review else None,
            }

    def backup(self, keep: int = 7, mirror_dir: str | os.PathLike | None = None) -> str:
        with self._lock:
            dst = self.path.with_name(f"{self.path.stem}.bak-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
            bconn = sqlite3.connect(str(dst))
            self._conn.backup(bconn)
            bconn.close()
            if mirror_dir:
                try:
                    m = Path(mirror_dir).expanduser()
                    m.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, m / "brain.latest.db")
                except OSError:
                    pass
            baks = sorted(self.path.parent.glob(f"{self.path.stem}.bak-*.db"))
            for old in baks[:-keep]:
                old.unlink(missing_ok=True)
            return str(dst)

    def auto_maintenance(self, retention_days: int = 30, backup_keep: int = 7, actor: str = "nightly",
                         mirror_dir: str | None = None) -> dict:
        """Детерминированная (без LLM) уборка: бэкап, точные дубли, просрочка, decay, обрезка журнала, VACUUM."""
        report: dict = {"backup": self.backup(backup_keep, mirror_dir)}
        with self._lock:
            c = self._conn
            # 1. точные дубликаты (нормализованный текст + тип)
            # (SQLite lower() не понимает кириллицу — нормализуем в Python)
            groups: dict[tuple, list[int]] = {}
            for r in c.execute("SELECT id, content, kind FROM notes WHERE status='active' ORDER BY id"):
                key = (" ".join(r["content"].lower().split()).rstrip(".!"), r["kind"])
                groups.setdefault(key, []).append(r["id"])
            merged = 0
            for ids in groups.values():
                if len(ids) > 1:
                    self.merge_notes(ids[0], ids[1:], actor=actor)
                    merged += len(ids) - 1
            report["exact_duplicates_merged"] = merged
            # 2. просроченные
            cur = c.execute(
                "UPDATE notes SET status='expired', updated_at=? WHERE status='active' AND valid_until IS NOT NULL AND valid_until < ?",
                (now(), now()),
            )
            report["expired"] = cur.rowcount
            # 3. затухание важности у невостребованного
            old = (dt.datetime.now() - dt.timedelta(days=120)).isoformat()
            cur = c.execute(
                """UPDATE notes SET importance=importance-1, updated_at=? WHERE status='active' AND importance>1
                   AND access_count=0 AND created_at < ? AND (last_accessed IS NULL) AND kind IN ('fact','idea','event')""",
                (now(), old),
            )
            report["importance_decayed"] = cur.rowcount
            # 4. обрезка сырого журнала
            cutoff = (dt.datetime.now() - dt.timedelta(days=retention_days)).isoformat()
            cur = c.execute("DELETE FROM turns WHERE digested=1 AND created_at < ?", (cutoff,))
            report["turns_pruned"] = cur.rowcount
            cutoff2 = (dt.datetime.now() - dt.timedelta(days=retention_days * 3)).isoformat()
            report["turns_pruned"] += c.execute("DELETE FROM turns WHERE created_at < ?", (cutoff2,)).rowcount
            # 4b. сбои, не повторявшиеся 30 дней — считаем решёнными
            report["failures_autoresolved"] = c.execute(
                "UPDATE failures SET resolved=1 WHERE resolved=0 AND last_seen < ?", (cutoff,)).rowcount
            # 5. чистка старого changelog (оставляем год)
            year = (dt.datetime.now() - dt.timedelta(days=365)).isoformat()
            c.execute("DELETE FROM changelog WHERE ts < ?", (year,))
            # 6. индексы
            if self.has_fts:
                c.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
            c.execute("PRAGMA optimize")
            self._log(actor, "auto_maintenance", "meta", None, after=report)
        try:
            self._conn.execute("VACUUM")
        except sqlite3.OperationalError:
            pass
        return report

    def review_plan(self, max_items: int = 25) -> dict:
        """Кандидаты на реструктуризацию — решения принимает модель (ночной cron)."""
        with self._lock:
            c = self._conn
            active = [dict(r) for r in c.execute(
                "SELECT id, content, kind, entity_id, tags, importance, confidence, access_count, created_at, updated_at, last_accessed "
                "FROM notes WHERE status='active' ORDER BY updated_at DESC LIMIT 400")]
            # near-duplicates
            pairs, seen = [], set()
            for n in active[:200]:
                for cand in self._search_raw(n["content"], limit=4, kinds=None):
                    if cand["id"] == n["id"]:
                        continue
                    key = tuple(sorted((n["id"], cand["id"])))
                    if key in seen:
                        continue
                    sim = jaccard(n["content"], cand["content"])
                    if sim >= 0.3:  # кандидаты; окончательное решение — за моделью
                        seen.add(key)
                        pairs.append({"a": {"id": n["id"], "content": n["content"], "kind": n["kind"]},
                                      "b": {"id": cand["id"], "content": cand["content"], "kind": cand["kind"]},
                                      "similarity": round(sim, 2)})
                if len(pairs) >= max_items:
                    break
            stale_cut = (dt.datetime.now() - dt.timedelta(days=180)).isoformat()
            stale = [n for n in active if (n["last_accessed"] or n["updated_at"]) < stale_cut and n["importance"] <= 2][:max_items]
            low_conf = [n for n in active if n["confidence"] < 0.5][:max_items]
            uncategorized = [n for n in active if n["kind"] == "fact" and not n["entity_id"] and not n["tags"]][:max_items]
            # возможные противоречия: одна сущность + общий тег, несколько заметок
            groups = [dict(r) for r in c.execute(
                """SELECT entity_id, tags, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM notes
                   WHERE status='active' AND entity_id IS NOT NULL AND tags!='' GROUP BY entity_id, tags HAVING n>=2 LIMIT ?""",
                (max_items,))]
            conflicts = []
            for g in groups:
                ids = [int(x) for x in g["ids"].split(",")][:6]
                conflicts.append({"entity": (self._row("entities", g["entity_id"]) or {}).get("name"), "tags": g["tags"],
                                  "notes": [{"id": i, "content": (self._row("notes", i) or {}).get("content")} for i in ids]})
            auto_captured = [{k: n[k] for k in ("id", "content", "kind")} for n in active
                             if c.execute("SELECT source FROM notes WHERE id=?", (n["id"],)).fetchone()[0] == "auto-capture"][:max_items]
            reflection_material = {
                "recent_episodes": self.episodes(7),
                "recent_decisions": [{k: n[k] for k in ("id", "content")} for n in active if n["kind"] in ("decision", "goal", "habit")][:15],
                "existing_insights": [{k: n[k] for k in ("id", "content")} for n in active if n["kind"] == "insight"][:10],
            }
            empty_entities = [dict(r) for r in c.execute(
                """SELECT e.id, e.name, e.kind FROM entities e LEFT JOIN notes n ON n.entity_id=e.id AND n.status='active'
                   WHERE e.status='active' GROUP BY e.id HAVING COUNT(n.id)=0 LIMIT ?""", (max_items,))]
            return {
                "kinds": self.kinds(),
                "near_duplicates": pairs,
                "stale_low_value": [{k: n[k] for k in ("id", "content", "kind", "importance", "updated_at")} for n in stale],
                "low_confidence": [{k: n[k] for k in ("id", "content", "kind", "confidence")} for n in low_conf],
                "uncategorized": [{k: n[k] for k in ("id", "content")} for n in uncategorized],
                "possible_conflicts": conflicts,
                "empty_entities": empty_entities,
                "auto_captured_to_rephrase": auto_captured,
                "stale_entity_summaries": self.stale_summaries(),
                "tool_failures": self.failures(10),
                "reflection_material": reflection_material,
                "pending_turns_days": self.pending_days(),
            }

    def apply_ops(self, ops: list[dict], actor: str = "nightly") -> dict:
        """Пакет структурных операций одной транзакцией."""
        done, errors = [], []
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for op in ops or []:
                    kind = (op.get("op") or "").lower()
                    try:
                        if kind == "merge":
                            done.append(self.merge_notes(op["keep"], op.get("drop") or [], op.get("content"), actor))
                        elif kind == "archive":
                            done.append(self.forget(op["id"], op.get("reason", ""), actor))
                        elif kind == "update":
                            f = {k: op.get(k) for k in ("content", "kind", "tags", "importance", "confidence", "entity", "valid_until")}
                            done.append({"updated": self.update_note(op["id"], actor, **f)["id"]})
                        elif kind == "retag":
                            done.append({"updated": self.update_note(op["id"], actor, tags=op.get("tags", ""))["id"]})
                        elif kind == "rekind":
                            done.append({"updated": self.update_note(op["id"], actor, kind=op["kind"])["id"]})
                        elif kind == "link":
                            done.append({"linked": self.update_note(op["id"], actor, entity=op["entity"])["id"]})
                        elif kind == "kind_define":
                            done.append(self.define_kind(op["name"], op.get("description", ""), actor))
                        elif kind == "kind_rename":
                            done.append({"kind_renamed": op["from"], "to": op["to"], "moved": self.rename_kind(op["from"], op["to"], actor)})
                        elif kind == "entity_merge":
                            done.append(self.entity_merge(op["keep"], op["drop"], actor))
                        elif kind == "entity_update":
                            done.append({"entity": self.entity_upsert(op["name"], op.get("kind", "topic"), op.get("summary", ""), op.get("tags", ""), actor)["name"]})
                        elif kind == "relate":
                            done.append(self.entity_link(op["src"], op["dst"], op["type"], op.get("note", ""), actor))
                        elif kind == "supersede":
                            done.append(self.supersede(op["id"], op["content"], actor))
                        elif kind == "entity_summary":
                            e = self.entity_upsert(op["name"], summary=op["summary"], actor=actor)
                            done.append({"entity_summary": e["name"]})
                        elif kind == "resolve_failure":
                            self.resolve_failure(op["id"], actor)
                            done.append({"failure_resolved": op["id"]})
                        else:
                            errors.append({"op": op, "error": f"неизвестная операция {kind!r}"})
                    except (BrainError, KeyError, TypeError, ValueError) as e:
                        errors.append({"op": op, "error": str(e)})
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return {"applied": len(done), "results": done, "errors": errors}

    def finish_review(self, report: str, actor: str = "nightly", started_at: str | None = None) -> dict:
        with self._lock:
            st = self.stats()
            st.pop("top_entities", None)
            self._conn.execute(
                "INSERT INTO reviews(started_at, finished_at, actor, report, stats) VALUES (?,?,?,?,?)",
                (started_at or now(), now(), actor, report.strip(), json.dumps(st, ensure_ascii=False)),
            )
            self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_review', ?)", (now(),))
            return {"saved": True, "finished_at": now()}

    def changelog(self, limit: int = 30, since: str | None = None) -> list[dict]:
        with self._lock:
            if since:
                rows = self._conn.execute("SELECT * FROM changelog WHERE ts>=? ORDER BY id DESC LIMIT ?", (since, limit))
            else:
                rows = self._conn.execute("SELECT * FROM changelog ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    # ─────────────────────────────── экспорт ──────────────────────────────

    def export_profile(self, min_importance: int = 4, limit: int = 40) -> str:
        """Короткий профиль пользователя: только самое важное — для синхронизации с памятью Hermes / быстрого взгляда."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT n.content, n.kind, e.name AS entity FROM notes n LEFT JOIN entities e ON e.id=n.entity_id "
                "WHERE n.status='active' AND n.importance>=? ORDER BY n.importance DESC, n.confidence DESC, n.updated_at DESC LIMIT ?",
                (min_importance, limit)).fetchall()
            insights = self._conn.execute(
                "SELECT content FROM notes WHERE status='active' AND kind='insight' ORDER BY updated_at DESC LIMIT 10").fetchall()
        lines = [f"# Профиль пользователя (важность ≥{min_importance}) — {now()[:16].replace('T', ' ')}", ""]
        for r in rows:
            ent = f" ({r['entity']})" if r["entity"] else ""
            lines.append(f"- [{r['kind']}]{ent} {r['content']}")
        if insights:
            lines += ["", "## Наблюдения (insights)", ""] + [f"- {r['content']}" for r in insights]
        return "\n".join(lines) + "\n"

    def restore_backup(self, backup_path: str | os.PathLike | None = None) -> str:
        """Откатить базу из бэкапа (по умолчанию — последний). Текущая база сохраняется как .pre-restore."""
        with self._lock:
            baks = sorted(self.path.parent.glob(f"{self.path.stem}.bak-*.db"))
            src = Path(backup_path) if backup_path else (baks[-1] if baks else None)
            if not src or not src.exists():
                raise BrainError("Бэкап не найден")
            keep = self.path.with_name(f"{self.path.stem}.pre-restore-{dt.datetime.now():%Y%m%d-%H%M%S}.db")
            self._conn.execute("VACUUM INTO ?", (str(keep),))
            bconn = sqlite3.connect(str(src))
            bconn.backup(self._conn)
            bconn.close()
            self._log("user", "restore", "meta", None, after={"from": str(src), "saved_current_as": str(keep)})
            return str(src)

    def export_markdown(self) -> str:
        """Человекочитаемый снимок всей активной базы."""
        with self._lock:
            c = self._conn
            lines = [f"# JARVIS Brain — снимок {now()[:16].replace('T', ' ')}", ""]
            st = self.stats()
            lines.append(f"Заметок: **{st['notes_active']}** · сущностей: **{st['entities']}** · связей: {st['relations']} · "
                         f"эпизодов: {st['episodes']} · изменений в журнале: {st['changes_total']}")
            lines.append("")
            ents = [dict(r) for r in c.execute("SELECT * FROM entities WHERE status='active' ORDER BY kind, name")]
            if ents:
                lines += ["## Карточки", ""]
                for e in ents:
                    notes = [dict(r) for r in c.execute(
                        "SELECT * FROM notes WHERE entity_id=? AND status='active' ORDER BY importance DESC, updated_at DESC", (e["id"],))]
                    lines.append(f"### {e['name']}  `{e['kind']}`")
                    if e["summary"]:
                        lines.append(f"_{e['summary']}_")
                    for n in notes:
                        star = "★" * n["importance"]
                        tags = f"  #{n['tags'].replace(',', ' #')}" if n["tags"] else ""
                        lines.append(f"- [{n['kind']}] {n['content']}  {star}{tags}")
                    rels = c.execute(
                        "SELECT r.type, e2.name FROM relations r JOIN entities e2 ON e2.id=r.dst WHERE r.src=?", (e["id"],)).fetchall()
                    for r in rels:
                        lines.append(f"- → {r['type']} **{r['name']}**")
                    lines.append("")
            for k in self.kinds():
                notes = [dict(r) for r in c.execute(
                    "SELECT * FROM notes WHERE kind=? AND entity_id IS NULL AND status='active' ORDER BY importance DESC, updated_at DESC",
                    (k["name"],))]
                if not notes:
                    continue
                lines += [f"## {k['name']} — {k['description']}", ""]
                for n in notes:
                    tags = f"  #{n['tags'].replace(',', ' #')}" if n["tags"] else ""
                    lines.append(f"- {n['content']}  {'★' * n['importance']}{tags}")
                lines.append("")
            eps = self.episodes(14)
            if eps:
                lines += ["## Дневник (последние дни)", ""]
                for e in eps:
                    lines.append(f"**{e['day']}** — {e['summary']}")
                    if e["highlights"]:
                        lines.append(f"  _{e['highlights']}_")
                lines.append("")
            return "\n".join(lines)
