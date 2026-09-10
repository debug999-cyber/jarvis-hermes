"""
JARVIS Vault — хранилище файлов и проектов пользователя с полнотекстовым индексом.

Идея: одна папка (по умолчанию ~/JARVIS), куда пользователь просто кладёт что угодно — документы, заметки,
целые проекты — или подключает существующие папки (`jarvis vault add ~/Projects/foo` → символическая ссылка
в ~/JARVIS/projects/foo). JARVIS:
  * индексирует текстовое содержимое (md/txt/код/csv/json/yaml/html, PDF через pdftotext, docx/rtf/pages через textutil)
    в ту же базу brain.db (таблицы files + files_fts) — поиск по смыслу слов, а не по имени файла;
  * подмешивает релевантные файлы в контекст каждого хода (см. __init__.build_memory_context);
  * имеет к файлам полный доступ: чтение через vault_read (в т.ч. PDF/DOCX), запись/выполнение — штатными
    инструментами Hermes (write_file, terminal, …), пути ему известны из поиска.

Безопасность: не индексируем секреты (.env, ключи, сертификаты), служебные каталоги (node_modules, .git, venv…)
и бинарники; из текста вырезаются похожие на пароли/токены фрагменты (db.redact). Файлы > MAX_FILE_BYTES пропускаются.

Модуль работает и как библиотека (из плагина), и как скрипт:  python3 vault.py [status|reindex|list|search|add|remove|tree]
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    from .db import Brain, redact  # внутри плагина
except ImportError:  # запуск как скрипт: python3 vault.py …
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db import Brain, redact  # type: ignore

TEXT_EXT = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".org", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".xml", ".html", ".htm", ".css", ".scss", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".py",
    ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".m", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".sh", ".zsh",
    ".bash", ".fish", ".sql", ".r", ".jl", ".lua", ".pl", ".ps1", ".bat", ".dockerfile", ".env.example", ".log",
    ".tex", ".bib", ".srt", ".vtt", ".ics", ".vcf", ".plist", ".gradle", ".make", ".mk", ".cmake", ".proto", ".graphql",
}
CONVERT_EXT = {".pdf": "pdftotext", ".docx": "textutil", ".doc": "textutil", ".rtf": "textutil", ".rtfd": "textutil",
               ".odt": "textutil", ".pages": "textutil", ".webarchive": "textutil", ".pptx": "zip-xml", ".xlsx": "zip-xml"}
SPECIAL_NAMES = {"Makefile", "Dockerfile", "README", "LICENSE", "CHANGELOG", "TODO", "NOTES", "Procfile", "Gemfile", "Rakefile"}
SKIP_DIRS = {"node_modules", ".git", ".hg", ".svn", "venv", ".venv", "env", "__pycache__", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "dist", "build", "target", ".next", ".nuxt", ".cache", ".idea", ".vscode", "Pods", "DerivedData",
             ".DS_Store", ".Trash", "coverage", ".tox", ".gradle", "vendor", "bower_components"}
SKIP_NAME_RE = re.compile(r"(^\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|\.keystore$|^id_(rsa|ed25519|ecdsa)|\.crt$|\.der$|"
                          r"credentials|secrets?\.(json|ya?ml|toml)$|\.sqlite3?$|\.db$|\.lock$|-lock\.json$|\.min\.(js|css)$|\.map$)",
                          re.I)
MAX_FILE_BYTES = 5 * 1024 * 1024
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_CHUNKS_PER_FILE = 400
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, rel TEXT NOT NULL, name TEXT NOT NULL, ext TEXT DEFAULT '',
    source TEXT DEFAULT 'vault', size INTEGER DEFAULT 0, mtime REAL DEFAULT 0, sha1 TEXT DEFAULT '',
    chunks INTEGER DEFAULT 0, chars INTEGER DEFAULT 0, kind TEXT DEFAULT 'text', status TEXT DEFAULT 'ok',
    note TEXT DEFAULT '', summary TEXT DEFAULT '', indexed_at TEXT, seen_at TEXT);
CREATE INDEX IF NOT EXISTS files_rel ON files(rel);
CREATE TABLE IF NOT EXISTS file_chunks(
    id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, no INTEGER NOT NULL, line_from INTEGER DEFAULT 1, content TEXT NOT NULL,
    name TEXT DEFAULT '', rel TEXT DEFAULT '');   -- name/rel дублируются: внешняя content-таблица FTS5 требует те же колонки
CREATE INDEX IF NOT EXISTS file_chunks_file ON file_chunks(file_id);
CREATE TABLE IF NOT EXISTS vault_sources(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, path TEXT NOT NULL, added_at TEXT, note TEXT DEFAULT '');
"""
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    content, name, rel, content='file_chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS file_chunks_ai AFTER INSERT ON file_chunks BEGIN
    INSERT INTO files_fts(rowid, content, name, rel) VALUES (new.id, new.content, new.name, new.rel); END;
CREATE TRIGGER IF NOT EXISTS file_chunks_ad AFTER DELETE ON file_chunks BEGIN
    INSERT INTO files_fts(files_fts, rowid, content, name, rel) VALUES ('delete', old.id, old.content, old.name, old.rel); END;
"""

README_TEXT = """# JARVIS Vault — ваше хранилище

Кладите сюда **что угодно**: документы, заметки, PDF, таблицы, целые проекты. JARVIS индексирует содержимое
(не только имена файлов) и использует его в разговоре: «что написано в договоре с Acme?», «найди в моих заметках
про отпуск», «в проекте foo — где обрабатывается логин?».

* `projects/` — сюда `jarvis vault add ~/Projects/foo` добавляет ссылки на существующие папки (сами файлы не копируются).
* `inbox/` — быстрый сброс: всё, что нужно «показать Джарвису».
* Остальную структуру придумывайте сами — папки, подпапки, любые имена.

Что НЕ индексируется: `.env`, ключи и сертификаты, `node_modules`, `.git`, `venv`, бинарники, файлы больше 5 МБ.
Индекс обновляется автоматически (каждые несколько минут и при старте) или вручную: `jarvis vault reindex`.
Поиск из терминала: `jarvis vault search "слова"`. Статус: `jarvis vault status`.
"""


def now() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def default_vault_dir() -> Path:
    return Path(os.environ.get("JARVIS_VAULT_DIR") or "~/JARVIS").expanduser()


def _which(name: str) -> str | None:
    return shutil.which(name) or next((p for p in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}")
                                       if os.path.exists(p)), None)


# ──────────────────────────── извлечение текста ────────────────────────────

def file_kind(path: Path) -> str | None:
    """'text' | 'convert' | None (не индексируем)."""
    name = path.name
    if SKIP_NAME_RE.search(name):
        return None
    ext = path.suffix.lower()
    if ext in TEXT_EXT or name in SPECIAL_NAMES or (not ext and name.upper() in SPECIAL_NAMES):
        return "text"
    if ext in CONVERT_EXT:
        return "convert"
    return None


def extract_text(path: Path, max_bytes: int = MAX_FILE_BYTES) -> tuple[str, str]:
    """Текст файла и примечание (пусто = ок). Никогда не бросает."""
    try:
        size = path.stat().st_size
    except OSError as e:
        return "", f"недоступен: {e}"
    if size > max_bytes:
        return "", f"пропущен: {size // 1024 // 1024} МБ > лимита"
    ext = path.suffix.lower()
    kind = file_kind(path)
    try:
        if kind == "text":
            raw = path.read_bytes()
            if b"\x00" in raw[:4096]:
                return "", "бинарный файл"
            for enc in ("utf-8", "utf-16", "cp1251", "latin-1"):
                try:
                    return raw.decode(enc), ""
                except UnicodeDecodeError:
                    continue
            return "", "неизвестная кодировка"
        if kind == "convert":
            tool = CONVERT_EXT[ext]
            if tool == "pdftotext":
                exe = _which("pdftotext")
                if not exe:
                    return "", "для PDF нужен pdftotext: brew install poppler"
                out = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=120)
                return out.stdout.decode("utf-8", errors="ignore"), "" if out.returncode == 0 else "pdftotext: ошибка"
            if tool == "textutil":
                exe = _which("textutil")
                if not exe:
                    return "", "конвертация доступна только на macOS (textutil)"
                out = subprocess.run([exe, "-convert", "txt", "-stdout", str(path)], capture_output=True, timeout=120)
                return out.stdout.decode("utf-8", errors="ignore"), "" if out.returncode == 0 else "textutil: ошибка"
            if tool == "zip-xml":  # pptx/xlsx: вытащить текст из XML внутри zip без зависимостей
                import zipfile
                texts = []
                with zipfile.ZipFile(path) as z:
                    for n in z.namelist():
                        if n.endswith(".xml") and ("slides/slide" in n or "sharedStrings" in n or "worksheets/sheet" in n):
                            xml = z.read(n).decode("utf-8", errors="ignore")
                            texts.append(" ".join(re.findall(r">([^<>]{2,})<", xml)))
                return "\n".join(t for t in texts if t.strip()), ""
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return "", f"ошибка чтения: {str(e)[:80]}"
    return "", "не индексируется"


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, str]]:
    """[(строка_начала, кусок)] — режем по абзацам/строкам, чтобы куски были осмысленными."""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    chunks: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_len = 0
    start_line = 1
    for i, line in enumerate(lines, 1):
        if buf_len + len(line) + 1 > size and buf:
            chunk = "\n".join(buf).strip()
            if chunk:
                chunks.append((start_line, chunk))
            # перекрытие: оставляем хвост, чтобы не рвать мысль на границе
            tail: list[str] = []
            tl = 0
            for prev in reversed(buf):
                if tl + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tl += len(prev) + 1
            start_line = i - len(tail)
            buf, buf_len = tail, tl
        buf.append(line)
        buf_len += len(line) + 1
        if len(chunks) >= MAX_CHUNKS_PER_FILE:
            break
    chunk = "\n".join(buf).strip()
    if chunk and len(chunks) < MAX_CHUNKS_PER_FILE:
        chunks.append((start_line, chunk))
    return chunks


# ──────────────────────────────── индекс ───────────────────────────────────

class Vault:
    """Индекс файлов поверх соединения Brain (та же brain.db, те же бэкапы)."""

    def __init__(self, brain: Brain, root: str | os.PathLike | None = None):
        self.brain = brain
        self.root = Path(root).expanduser() if root else default_vault_dir()
        self._conn = brain._conn
        self._lock = brain._lock
        self.has_fts = True
        self._init_schema()

    # служебное ------------------------------------------------------------
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            try:
                self._conn.executescript(FTS_SCHEMA)
            except sqlite3.OperationalError:
                self.has_fts = False
            self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('vault_schema', ?)", (str(SCHEMA_VERSION),))

    def ensure_layout(self) -> Path:
        """Создать ~/JARVIS с README и стандартными папками (идемпотентно)."""
        for sub in ("", "inbox", "projects"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        readme = self.root / "README.md"
        if not readme.exists():
            readme.write_text(README_TEXT, encoding="utf-8")
        return self.root

    def rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    # источники (подключённые внешние папки) --------------------------------
    def add_source(self, path: str | os.PathLike, name: str | None = None) -> dict:
        src = Path(path).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"папка не найдена: {src}")
        if self.root.resolve() in (src, *src.parents):
            raise ValueError("эта папка уже внутри хранилища")
        name = (name or src.name).strip().replace("/", "-")
        self.ensure_layout()
        link = self.root / "projects" / name
        if link.is_symlink() or link.exists():
            if link.is_symlink() and link.resolve() == src:
                pass  # уже подключено
            else:
                raise FileExistsError(f"в projects/ уже есть «{name}» — укажите другое имя")
        else:
            link.symlink_to(src, target_is_directory=True)
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO vault_sources(name, path, added_at) VALUES (?,?,?)", (name, str(src), now()))
            self.brain._log("user", "vault_add", "vault_sources", None, after={"name": name, "path": str(src)})
        return {"name": name, "path": str(src), "link": str(link)}

    def remove_source(self, name: str) -> dict:
        link = self.root / "projects" / name
        removed = False
        if link.is_symlink():
            link.unlink()
            removed = True
        with self._lock:
            n = self._conn.execute("DELETE FROM vault_sources WHERE name=?", (name,)).rowcount
            self._conn.execute("DELETE FROM file_chunks WHERE file_id IN (SELECT id FROM files WHERE rel LIKE ?)", (f"projects/{name}/%",))
            self._conn.execute("DELETE FROM files WHERE rel LIKE ?", (f"projects/{name}/%",))
        return {"name": name, "removed": removed or n > 0}

    def sources(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute("SELECT name, path, added_at, note FROM vault_sources ORDER BY name")]

    # обход -----------------------------------------------------------------
    def walk(self) -> list[Path]:
        """Все кандидаты на индексацию (следуем по symlink'ам в projects/, защищаемся от циклов)."""
        out: list[Path] = []
        seen_dirs: set[str] = set()
        if not self.root.exists():
            return out
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                real = str(d.resolve())
            except OSError:
                continue
            if real in seen_dirs:
                continue
            seen_dirs.add(real)
            try:
                entries = sorted(os.scandir(d), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                name = e.name
                if name.startswith(".") and name not in (".env.example",):
                    continue
                if e.is_dir(follow_symlinks=True):
                    if name in SKIP_DIRS:
                        continue
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=True):
                    p = Path(e.path)
                    if file_kind(p):
                        out.append(p)
        return out

    def index_file(self, path: Path, source: str = "vault", force: bool = False) -> str:
        """'indexed' | 'unchanged' | 'skipped' | 'error'."""
        try:
            st = path.stat()
        except OSError:
            return "error"
        with self._lock:
            row = self._conn.execute("SELECT id, size, mtime, status FROM files WHERE path=?", (str(path),)).fetchone()
            if row and not force and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1e-6:
                self._conn.execute("UPDATE files SET seen_at=? WHERE id=?", (now(), row["id"]))
                return "unchanged"
        text, note = extract_text(path)
        sha1 = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest() if text else ""
        text, _ = redact(text)
        chunks = chunk_text(text) if text else []
        with self._lock:
            c = self._conn
            c.execute("BEGIN")
            try:
                if row:
                    fid = row["id"]
                    c.execute("DELETE FROM file_chunks WHERE file_id=?", (fid,))
                    c.execute("UPDATE files SET rel=?, name=?, ext=?, source=?, size=?, mtime=?, sha1=?, chunks=?, chars=?, "
                              "kind=?, status=?, note=?, indexed_at=?, seen_at=? WHERE id=?",
                              (self.rel(path), path.name, path.suffix.lower(), source, st.st_size, st.st_mtime, sha1, len(chunks),
                               len(text), file_kind(path) or "", "ok" if text else "skipped", note, now(), now(), fid))
                else:
                    cur = c.execute("INSERT INTO files(path, rel, name, ext, source, size, mtime, sha1, chunks, chars, kind, status, "
                                    "note, indexed_at, seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (str(path), self.rel(path), path.name, path.suffix.lower(), source, st.st_size, st.st_mtime,
                                     sha1, len(chunks), len(text), file_kind(path) or "", "ok" if text else "skipped", note, now(), now()))
                    fid = cur.lastrowid
                c.executemany("INSERT INTO file_chunks(file_id, no, line_from, content, name, rel) VALUES (?,?,?,?,?,?)",
                              [(fid, i, ln, ch, path.name, self.rel(path)) for i, (ln, ch) in enumerate(chunks)])
                c.execute("COMMIT")
            except sqlite3.Error:
                c.execute("ROLLBACK")
                return "error"
        return "indexed" if text else "skipped"

    def reindex(self, force: bool = False, max_seconds: float = 300.0) -> dict:
        """Полный проход: новые/изменённые файлы индексируются, исчезнувшие — удаляются."""
        t0 = time.time()
        stats = {"indexed": 0, "unchanged": 0, "skipped": 0, "error": 0, "removed": 0, "seconds": 0.0, "root": str(self.root)}
        present: set[str] = set()
        for p in self.walk():
            present.add(str(p))
            rel = self.rel(p)
            source = "project:" + rel.split("/")[1] if rel.startswith("projects/") and "/" in rel[9:] else "vault"
            stats[self.index_file(p, source=source, force=force)] += 1
            if time.time() - t0 > max_seconds:
                stats["note"] = "прервано по времени — продолжу в следующий проход"
                break
        else:
            with self._lock:
                gone = [r["id"] for r in self._conn.execute("SELECT id, path FROM files") if r["path"] not in present]
                for fid in gone:
                    self._conn.execute("DELETE FROM file_chunks WHERE file_id=?", (fid,))
                    self._conn.execute("DELETE FROM files WHERE id=?", (fid,))
                stats["removed"] = len(gone)
        stats["seconds"] = round(time.time() - t0, 2)
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('vault_last_scan', ?)", (now(),))
            if stats["indexed"] or stats["removed"]:
                self.brain._log("system", "vault_reindex", "files", None, after=stats)
        return stats

    # поиск -----------------------------------------------------------------
    @staticmethod
    def _fts_query(query: str) -> str:
        words = [w for w in re.findall(r"[\w\-\.]{2,}", query.lower()) if not w.isdigit() or len(w) > 3]
        if not words:
            return ""
        # каждое слово с префиксом; OR — чтобы находить документы даже по части слов
        return " OR ".join(f'"{w.replace(chr(34), "")}"*' for w in words[:12])

    def search(self, query: str, limit: int = 8, prefix: str | None = None) -> list[dict]:
        """Куски файлов, релевантные запросу: path, rel, line, snippet, score."""
        q = (query or "").strip()
        if not q:
            return []
        with self._lock:
            c = self._conn
            rows: list[sqlite3.Row] = []
            if self.has_fts:
                fq = self._fts_query(q)
                if fq:
                    sql = ("SELECT fc.id, fc.file_id, fc.no, fc.line_from, f.path, f.rel, f.name, f.source, "
                           "snippet(files_fts, 0, '«', '»', ' … ', 24) AS snip, bm25(files_fts, 1.0, 3.0, 1.5) AS score "
                           "FROM files_fts JOIN file_chunks fc ON fc.id = files_fts.rowid JOIN files f ON f.id = fc.file_id "
                           "WHERE files_fts MATCH ? AND f.status='ok' ")
                    params: list = [fq]
                    if prefix:
                        sql += "AND f.rel LIKE ? "
                        params.append(prefix.rstrip("/") + "/%")
                    sql += "ORDER BY score LIMIT ?"
                    params.append(limit * 3)
                    try:
                        rows = c.execute(sql, params).fetchall()
                    except sqlite3.OperationalError:
                        rows = []
            if not rows:  # без FTS или ничего не нашлось по префиксам — LIKE по словам
                words = [w for w in re.findall(r"\w{3,}", q.lower())][:5]
                if not words:
                    return []
                cond = " AND ".join("lower(fc.content) LIKE ?" for _ in words)
                sql = (f"SELECT fc.id, fc.file_id, fc.no, fc.line_from, f.path, f.rel, f.name, f.source, "
                       f"substr(fc.content, 1, 240) AS snip, 0 AS score FROM file_chunks fc JOIN files f ON f.id=fc.file_id "
                       f"WHERE f.status='ok' AND {cond} ")
                params = [f"%{w}%" for w in words]
                if prefix:
                    sql += "AND f.rel LIKE ? "
                    params.append(prefix.rstrip("/") + "/%")
                sql += "LIMIT ?"
                params.append(limit * 3)
                rows = c.execute(sql, params).fetchall()
        # не больше 2 кусков на файл — иначе один большой файл вытесняет остальные
        out: list[dict] = []
        per_file: dict[int, int] = {}
        for r in rows:
            if per_file.get(r["file_id"], 0) >= 2:
                continue
            per_file[r["file_id"]] = per_file.get(r["file_id"], 0) + 1
            out.append({"file_id": r["file_id"], "path": r["path"], "rel": r["rel"], "name": r["name"], "source": r["source"],
                        "line": r["line_from"], "chunk": r["no"], "snippet": re.sub(r"\s+", " ", r["snip"]).strip()[:300],
                        "score": round(-float(r["score"]), 2) if r["score"] else 0.0})
            if len(out) >= limit:
                break
        return out

    def read(self, path: str, offset: int = 0, limit: int = 6000) -> dict:
        """Текст файла (с конвертацией PDF/DOCX), кусками по limit символов. Путь — абсолютный или относительно хранилища."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.root / p
        if not p.exists():
            return {"error": f"файл не найден: {p}"}
        if p.is_dir():
            return {"path": str(p), "dir": True, "entries": self.tree(p, depth=1)}
        text, note = extract_text(p)
        if not text:
            return {"path": str(p), "error": note or "пустой файл или не текст"}
        total = len(text)
        piece = text[offset: offset + limit]
        return {"path": str(p), "offset": offset, "chars": len(piece), "total": total,
                "next_offset": offset + limit if offset + limit < total else None, "content": piece}

    def tree(self, start: Path | None = None, depth: int = 2, limit: int = 200) -> list[str]:
        base = Path(start).expanduser() if start else self.root
        out: list[str] = []
        base_depth = len(base.parts)
        for d, dirs, files in os.walk(base, followlinks=True):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
            level = len(Path(d).parts) - base_depth
            if level >= depth:
                dirs[:] = []
            indent = "  " * level
            if level:
                out.append(f"{indent}{Path(d).name}/")
            for f in sorted(files):
                if f.startswith(".") or SKIP_NAME_RE.search(f):
                    continue
                out.append(f"{indent}  {f}")
                if len(out) >= limit:
                    out.append("  …")
                    return out
        return out

    def list_files(self, prefix: str | None = None, limit: int = 100, recent: bool = False) -> list[dict]:
        with self._lock:
            sql = "SELECT rel, path, name, ext, source, size, chunks, status, note, indexed_at, mtime FROM files "
            params: list = []
            if prefix:
                sql += "WHERE rel LIKE ? "
                params.append(prefix.rstrip("/") + "/%")
            sql += ("ORDER BY mtime DESC " if recent else "ORDER BY rel ") + "LIMIT ?"
            params.append(limit)
            return [dict(r) for r in self._conn.execute(sql, params)]

    def stats(self) -> dict:
        with self._lock:
            c = self._conn
            one = lambda sql: c.execute(sql).fetchone()[0]
            last = c.execute("SELECT value FROM meta WHERE key='vault_last_scan'").fetchone()
            by_source = [dict(r) for r in c.execute(
                "SELECT source, COUNT(*) AS files, SUM(chunks) AS chunks FROM files WHERE status='ok' GROUP BY source ORDER BY files DESC")]
            skipped = [dict(r) for r in c.execute("SELECT rel, note FROM files WHERE status!='ok' ORDER BY rel LIMIT 10")]
        return {"root": str(self.root), "exists": self.root.exists(), "files": one("SELECT COUNT(*) FROM files WHERE status='ok'"),
                "chunks": one("SELECT COUNT(*) FROM file_chunks"), "chars": one("SELECT COALESCE(SUM(chars),0) FROM files"),
                "skipped": one("SELECT COUNT(*) FROM files WHERE status!='ok'"), "skipped_examples": skipped,
                "sources": self.sources(), "by_source": by_source, "last_scan": last["value"] if last else None,
                "fts": self.has_fts, "pdf": bool(_which("pdftotext")), "office": bool(_which("textutil"))}


# ─────────────────────────── фоновое обновление ────────────────────────────

class VaultWatcher:
    """Периодический пересчёт индекса в фоне (без сторонних зависимостей вроде watchdog/fsevents)."""

    def __init__(self, vault: Vault, interval_min: float = 10.0, on_change=None):
        self.vault = vault
        self.interval = max(1.0, float(interval_min)) * 60
        self.on_change = on_change
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="jarvis-vault", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # первый проход почти сразу (после старта Hermes), дальше — по интервалу
        if self._stop.wait(20):
            return
        while not self._stop.is_set():
            try:
                st = self.vault.reindex()
                if self.on_change and (st["indexed"] or st["removed"]):
                    self.on_change(st)
            except Exception:  # индекс — вспомогательная вещь, не роняем процесс
                pass
            if self._stop.wait(self.interval):
                return


# ──────────────────────────────── CLI ──────────────────────────────────────

def _main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="jarvis vault", description="Хранилище файлов и проектов JARVIS")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    r = sub.add_parser("reindex"); r.add_argument("--force", action="store_true")
    ls = sub.add_parser("list"); ls.add_argument("prefix", nargs="?"); ls.add_argument("--recent", action="store_true")
    s = sub.add_parser("search"); s.add_argument("query", nargs="+"); s.add_argument("-n", type=int, default=8); s.add_argument("--in", dest="prefix")
    a = sub.add_parser("add"); a.add_argument("path"); a.add_argument("--name")
    rm = sub.add_parser("remove"); rm.add_argument("name")
    t = sub.add_parser("tree"); t.add_argument("path", nargs="?"); t.add_argument("--depth", type=int, default=2)
    rd = sub.add_parser("read"); rd.add_argument("path"); rd.add_argument("--offset", type=int, default=0)
    sub.add_parser("init")
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    v = Vault(Brain())
    try:
        if args.cmd in (None, "status"):
            st = v.stats()
            if args.json:
                print(json.dumps(st, ensure_ascii=False, indent=2)); return 0
            print(f"Хранилище: {st['root']}  {'(есть)' if st['exists'] else '(ещё не создано — jarvis vault init)'}")
            print(f"Файлов в индексе: {st['files']} · кусков: {st['chunks']} · символов: {st['chars']:,} · пропущено: {st['skipped']}")
            print(f"Последний проход: {st['last_scan'] or '—'} · PDF: {'да' if st['pdf'] else 'нет (brew install poppler)'} · Office: {'да' if st['office'] else 'нет'}")
            for s_ in st["sources"]:
                print(f"  ⤷ проект {s_['name']} → {s_['path']}")
            for b in st["by_source"]:
                print(f"  {b['source']}: {b['files']} файлов, {b['chunks'] or 0} кусков")
            return 0
        if args.cmd == "init":
            print(v.ensure_layout()); return 0
        if args.cmd == "reindex":
            v.ensure_layout()
            st = v.reindex(force=args.force)
            print(json.dumps(st, ensure_ascii=False) if args.json else
                  f"✔ проиндексировано {st['indexed']}, без изменений {st['unchanged']}, пропущено {st['skipped']}, удалено {st['removed']} ({st['seconds']} с)")
            return 0
        if args.cmd == "list":
            rows = v.list_files(args.prefix, recent=args.recent)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=1)); return 0
            for r_ in rows:
                flag = "" if r_["status"] == "ok" else f"  [{r_['note']}]"
                print(f"{r_['rel']}  ({r_['size'] // 1024} КБ, {r_['chunks']} кусков){flag}")
            return 0
        if args.cmd == "search":
            hits = v.search(" ".join(args.query), limit=args.n, prefix=args.prefix)
            if args.json:
                print(json.dumps(hits, ensure_ascii=False, indent=1)); return 0
            if not hits:
                print("ничего не найдено"); return 1
            for h in hits:
                print(f"◆ {h['rel']}:{h['line']}  ({h['score']})\n    {h['snippet']}")
            return 0
        if args.cmd == "add":
            res = v.add_source(args.path, args.name)
            print(f"✔ подключено: projects/{res['name']} → {res['path']}")
            st = v.reindex()
            print(f"  проиндексировано {st['indexed']} файлов")
            return 0
        if args.cmd == "remove":
            print(json.dumps(v.remove_source(args.name), ensure_ascii=False)); return 0
        if args.cmd == "tree":
            print("\n".join(v.tree(Path(args.path) if args.path else None, depth=args.depth))); return 0
        if args.cmd == "read":
            res = v.read(args.path, offset=args.offset)
            print(res.get("content") or res.get("error") or json.dumps(res, ensure_ascii=False)); return 0
    except (OSError, ValueError) as e:
        print(f"✖ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
