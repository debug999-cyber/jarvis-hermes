#!/usr/bin/env python3
"""
JARVIS HUD — локальный сервер «голографического» интерфейса.

Только стандартная библиотека Python (никаких зависимостей), поэтому его можно
запускать любым python3 — не обязательно из venv Hermes.

Роли:
  * GET  /              — HTML-интерфейс (арк-реактор, лента активности, панели, чат);
  * GET  /events        — Server-Sent Events: живой поток событий для браузера;
  * POST /api/event     — приём событий от плагина jarvis-core (tool.start, stream.delta …);
  * POST /api/chat      — прокси к OpenAI-совместимому API Hermes (порт 8642) со стримингом;
  * GET  /api/status    — здоровье HUD + доступность Hermes API;
  * GET  /static/*      — статика;
  * GET  /file?path=…   — отдать локальный файл (скриншот/картинку) для панели (только из разрешённых папок).

Запуск:  python3 hud/server.py  [--port 8765] [--hermes http://127.0.0.1:8642] [--key API_SERVER_KEY]
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))
import sysinfo  # noqa: E402
import tts  # noqa: E402 — серверный синтез речи (edge-tts / say) для POST /api/tts — живые данные виджетов (батарея, календарь, таймеры…); путь добавлен строкой выше

DASH: sysinfo.Collector | None = None
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()

CONFIG = {
    "hermes_url": os.environ.get("JARVIS_HERMES_URL", "http://127.0.0.1:8642"),
    "hermes_key": os.environ.get("API_SERVER_KEY", ""),
    "model": os.environ.get("JARVIS_MODEL", "hermes-agent"),
    "brain_db": os.environ.get("JARVIS_BRAIN_DB", str(HERMES_HOME / "plugin-data" / "jarvis-brain" / "brain.db")),
    "allowed_file_roots": [  # /file?path= отдаёт файлы только отсюда (скриншоты, снимки камеры, пользовательские папки)
        str(HERMES_HOME / "cache"),
        os.path.expanduser("~/Pictures"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Downloads"),
    ],
}


def _load_env_key() -> str:
    """Прочитать API_SERVER_KEY из $HERMES_HOME/.env, если не задан в окружении."""
    if CONFIG["hermes_key"]:
        return CONFIG["hermes_key"]
    env = HERMES_HOME / ".env"
    try:
        for line in env.read_text().splitlines():
            if line.startswith("API_SERVER_KEY="):
                val = line.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
                if val:
                    return val
    except OSError:
        pass
    return ""


def _explain_http_error(code: int, body: str) -> str:
    """Человеческое объяснение ошибки от Hermes/провайдера вместо «HTTP 405: Error code: 405»."""
    body = body.strip()[:300]
    hints = {
        401: f"Hermes отклонил ключ HUD. Проверьте API_SERVER_KEY в {HERMES_HOME}/.env и перезапустите `jarvis hud restart`.",
        403: "Доступ запрещён провайдером модели. Проверьте ключ провайдера: `hermes model`.",
        404: "Модель не найдена у провайдера. Выберите другую: `hermes model`.",
        405: "Провайдер модели не принимает запросы (405) — обычно неверный URL/endpoint кастомной модели. "
             "Выполните `hermes model` и выберите рабочую модель (OpenRouter/Anthropic/OpenAI/Ollama).",
        429: "Лимит запросов провайдера исчерпан. Подождите или смените модель: `hermes model`.",
        500: "Ошибка на стороне модели. Попробуйте ещё раз или смените модель: `hermes model`.",
        502: "Провайдер модели недоступен.",
        503: "Провайдер модели перегружен. Повторите позже.",
    }
    hint = hints.get(code, f"Ошибка {code} от Hermes API.")
    return f"{hint}" + (f"  ({body})" if body and len(body) < 160 else "")


# ═════════════════════════════ база знаний (read-only) ════════════════════

def brain_overview(query: str = "", limit: int = 12) -> dict:
    """Снимок базы знаний для панели HUD. Только чтение; при отсутствии файла — пустой ответ."""
    path = CONFIG["brain_db"]
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


# ═════════════════════════════ шина событий ═══════════════════════════════

class EventBus:
    """Fan-out событий всем подключённым SSE-клиентам + кольцевой буфер истории."""

    def __init__(self, history: int = 200):
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._max_history = history

    def subscribe(self) -> queue.Queue:
        """Новому клиенту отдаём не всю историю, а только «липкое» состояние:
        открытые панели (после последнего panel.clear, не старше 30 минут, без дублей по id/позиции)
        и текущий режим. Старые ходы/инструменты не повторяем — иначе после перезагрузки страницы
        на экране снова всплывали давно закрытые карточки."""
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._clients.add(q)
            cutoff = time.time() - 1800
            panels: dict[str, dict] = {}
            mode = None
            for ev in self._history:
                name = ev.get("event")
                if name == "panel.clear":
                    panels.clear()
                elif name == "panel.show" and ev.get("ts", 0) > cutoff:
                    d = ev.get("data") or {}
                    if d.get("ttl"):
                        continue  # временные панели не восстанавливаем
                    key = str(d.get("id") or ("center" if d.get("position", "center") == "center" else f"{d.get('position')}:{d.get('title')}"))
                    panels[key] = ev
                elif name == "mode.set":
                    mode = ev
            for ev in ([mode] if mode else []) + list(panels.values()):
                q.put_nowait({**ev, "replay": True})
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        with self._lock:
            if event.get("event") != "stream.delta":  # дельты не храним
                self._history.append(event)
                self._history = self._history[-self._max_history:]
            dead = []
            for q in self._clients:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._clients.discard(q)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


BUS = EventBus()

# Сколько чатов сейчас идёт через прокси /api/chat. Пока > 0, события хода от плагина
# (turn.start/stream.delta/turn.end) отбрасываются — прокси уже рассылает их сам, иначе текст
# в HUD появлялся два-три раза, а озвучка запускалась дважды.
_PROXY_TURNS = 0
_PROXY_LOCK = threading.Lock()
_TURN_EVENTS = {"turn.start", "stream.delta", "stream.end", "turn.end"}


def hush(reason: str = "user") -> dict:
    """Заглушить всё, что сейчас говорит: системный `say`, afplay, озвучку в браузерах."""
    killed = []
    if sys.platform == "darwin":
        for proc in ("say", "afplay"):
            try:
                if subprocess.run(["pkill", "-x", proc], capture_output=True, timeout=3).returncode == 0:
                    killed.append(proc)
            except (OSError, subprocess.SubprocessError):
                pass
    BUS.publish({"event": "speech.stop", "data": {"reason": reason}})
    return {"ok": True, "killed": killed}


# ═════════════════════════════ HTTP-обработчик ════════════════════════════

class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisHUD/1.0"

    # ── утилиты ──────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):  # тише стандартного логгера
        if os.environ.get("JARVIS_HUD_DEBUG"):
            super().log_message(fmt, *args)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:  # защита от случайного/злонамеренного гигантского тела
            return {}
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            return {}

    def _send_file(self, path: Path, ctype: str | None = None, extra_headers: dict | None = None) -> None:
        if not path.exists() or not path.is_file():
            self._json(404, {"error": "not found"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # ── GET ──────────────────────────────────────────────────────────────
    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send_file(STATIC / "index.html", "text/html; charset=utf-8",
                                   extra_headers={"X-Frame-Options": "SAMEORIGIN", "Referrer-Policy": "no-referrer",
                                                  "X-Content-Type-Options": "nosniff"})
        if u.path.startswith("/static/"):
            rel = u.path[len("/static/"):]
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents:
                return self._json(403, {"error": "forbidden"})
            return self._send_file(target)
        if u.path == "/events":
            return self._sse()
        if u.path == "/api/status":
            return self._json(200, {
                "ok": True,
                "clients": BUS.client_count,
                "hermes": self._hermes_health(),
                "model": CONFIG["model"],
                "tts": tts.engine(),
            })
        if u.path == "/api/brain":
            return self._json(200, brain_overview(parse_qs(u.query).get("q", [""])[0]))
        if u.path == "/api/dashboard":
            return self._json(200, DASH.snapshot() if DASH else {})
        if u.path == "/file":
            p = parse_qs(u.query).get("path", [""])[0]
            real = os.path.realpath(os.path.expanduser(p))
            if not any(real.startswith(os.path.realpath(r) + os.sep) for r in CONFIG["allowed_file_roots"]):
                return self._json(403, {"error": "path not allowed"})
            return self._send_file(Path(real))
        return self._json(404, {"error": "not found"})

    # ── POST ─────────────────────────────────────────────────────────────
    def _same_origin(self) -> bool:
        """Защита от CSRF: браузерные POST принимаем только со своей страницы.

        Любой сайт может отправить fetch на http://127.0.0.1:8765 — без этой проверки чужая вкладка
        могла бы командовать JARVIS через /api/chat. Запросы без Origin (curl, плагин) — не из браузера, пропускаем.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin.split("://", 1)[-1] == host

    def do_POST(self):
        u = urlparse(self.path)
        if not self._same_origin():
            return self._json(403, {"error": "cross-origin requests are not allowed"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "expected application/json"})
        if u.path == "/api/event":
            ev = self._read_json()
            if not ev.get("event"):
                return self._json(400, {"error": "event required"})
            if ev["event"] in _TURN_EVENTS and _PROXY_TURNS > 0 and (ev.get("data") or {}).get("source") != "cron":
                return self._json(200, {"ok": True, "dropped": "proxy turn in flight"})
            BUS.publish(ev)
            if ev["event"] == "timer.fire":
                BUS.publish({"event": "timer.update", "data": {"timers": sysinfo.timers()}})
            return self._json(200, {"ok": True})
        if u.path == "/api/chat":
            return self._chat(self._read_json())
        if u.path == "/api/timer":
            return self._timer(self._read_json())
        if u.path == "/api/hush":
            return self._json(200, hush())
        if u.path == "/api/tts":
            return self._tts(self._read_json())
        if u.path == "/api/mode":
            body = self._read_json()
            mode = body.get("mode") if body.get("mode") in ("normal", "focus", "night", "presentation") else "normal"
            sysinfo.set_mode(mode)
            BUS.publish({"event": "mode.set", "data": {"mode": mode, "source": "hud"}})
            return self._json(200, {"ok": True, "mode": mode})
        return self._json(404, {"error": "not found"})

    def _tts(self, body: dict) -> None:
        """Озвучить текст голосом Hermes (edge-tts, тот же, что в voice mode). 204 = движка нет, HUD озвучит сам."""
        text = str(body.get("text") or "")[:tts.MAX_CHARS]
        if not text.strip():
            return self._json(400, {"error": "text required"})
        res = tts.synthesize(text)
        if not res:
            self.send_response(204)
            self.send_header("X-JARVIS-TTS", tts.engine())
            self.end_headers()
            return None
        audio, mime = res
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(audio)
        return None

    def _timer(self, body: dict) -> None:
        """Таймеры из виджета HUD: общий state.json с плагином jarvis-core, стреляет TimerWatcher."""
        import datetime as dt
        action = body.get("action")
        if action == "cancel":
            n = sysinfo.cancel_timer(str(body.get("label", "")), body.get("target"))
            BUS.publish({"event": "timer.update", "data": {"timers": sysinfo.timers()}})
            return self._json(200, {"ok": True, "cancelled": n})
        if action == "set":
            label = (str(body.get("label") or "").strip() or "Таймер")[:60]
            try:
                minutes = float(body.get("minutes") or 0)
            except (TypeError, ValueError):
                minutes = 0
            if body.get("at"):
                hh, mm = [int(x) for x in str(body["at"]).split(":")[:2]]
                target = dt.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
                if target <= dt.datetime.now():
                    target += dt.timedelta(days=1)
            elif 0 < minutes <= 24 * 60:
                target = dt.datetime.now() + dt.timedelta(minutes=minutes)
            else:
                return self._json(400, {"error": "minutes (1..1440) или at (HH:MM)"})
            sysinfo.add_timer(label, target)
            BUS.publish({"event": "timer.update", "data": {"timers": sysinfo.timers()}})
            return self._json(200, {"ok": True, "label": label, "target": target.isoformat()})
        return self._json(400, {"error": "action: set|cancel"})

    def do_OPTIONS(self):
        # CORS-preflight сознательно не разрешаем: HUD — same-origin приложение
        self.send_response(204)
        self.end_headers()

    # ── SSE ──────────────────────────────────────────────────────────────
    def _sse(self) -> None:
        q = BUS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(f"event: {ev.get('event', 'message')}\ndata: {payload}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")  # keep-alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            BUS.unsubscribe(q)

    # ── Hermes API ───────────────────────────────────────────────────────
    def _hermes_health(self) -> dict:
        try:
            req = urllib.request.Request(CONFIG["hermes_url"] + "/health")
            with urllib.request.urlopen(req, timeout=2) as r:
                return {"up": True, "status": r.status}
        except Exception as e:
            return {"up": False, "error": str(e)[:120]}

    def _chat(self, body: dict) -> None:
        """Прокси к POST /v1/chat/completions (stream=true) — ретранслируем SSE в браузер."""
        messages = body.get("messages") or []
        if not messages:
            return self._json(400, {"error": "messages required"})
        key = _load_env_key()
        payload = json.dumps({"model": CONFIG["model"], "messages": messages, "stream": True}).encode()
        req = urllib.request.Request(
            CONFIG["hermes_url"] + "/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            return self._json(e.code, {"error": _explain_http_error(e.code, e.read().decode(errors="ignore"))})
        except Exception as e:
            return self._json(502, {
                "error": f"Hermes API недоступен: {e}. Запустите `hermes gateway` и убедитесь, что "
                         f"в {HERMES_HOME}/.env есть API_SERVER_ENABLED=true и API_SERVER_KEY."
            })

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        global _PROXY_TURNS
        with _PROXY_LOCK:
            _PROXY_TURNS += 1
        BUS.publish({"event": "turn.start", "data": {"text": messages[-1].get("content", "")[:300], "source": "hud"}})
        full = []
        try:
            for raw in upstream:
                line = raw.decode(errors="ignore").rstrip("\n")
                if not line:
                    continue
                self.wfile.write((line + "\n").encode())
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        chunk = json.loads(line[6:])
                        delta = chunk["choices"][0]["delta"].get("content")
                        if delta:
                            full.append(delta)
                            BUS.publish({"event": "stream.delta", "data": {"delta": delta}})
                    except Exception:
                        pass
                elif line.startswith("event: hermes.tool.progress"):
                    pass  # следующая data-строка содержит имя инструмента — прокинем как есть
                if line.startswith("data: ") and '"tool"' in line:
                    try:
                        d = json.loads(line[6:])
                        BUS.publish({"event": "tool.start", "data": {"tool": d.get("tool") or d.get("name", "?"), "args": ""}})
                    except Exception:
                        pass
            self.wfile.write(b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                upstream.close()
            except Exception:
                pass
            BUS.publish({"event": "turn.end", "data": {"text": "".join(full)[:2000], "source": "hud"}})
            with _PROXY_LOCK:
                _PROXY_TURNS = max(0, _PROXY_TURNS - 1)


# ═════════════════════════════ точка входа ════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(description="JARVIS HUD server")
    ap.add_argument("--host", default=os.environ.get("JARVIS_HUD_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("JARVIS_HUD_PORT", "8765")))
    ap.add_argument("--hermes", default=CONFIG["hermes_url"], help="URL API-сервера Hermes")
    ap.add_argument("--key", default="", help="API_SERVER_KEY (иначе читается из $HERMES_HOME/.env)")
    ap.add_argument("--model", default=CONFIG["model"])
    ap.add_argument("--demo", action="store_true", help="демо-данные в виджетах (для скриншотов и разработки)")
    args = ap.parse_args()

    CONFIG["hermes_url"] = args.hermes.rstrip("/")
    CONFIG["model"] = args.model
    if args.key:
        CONFIG["hermes_key"] = args.key

    global DASH
    demo = args.demo or bool(os.environ.get("JARVIS_HUD_DEMO"))
    DASH = sysinfo.Collector(demo=demo, on_change=lambda ch: BUS.publish({"event": "dashboard.update", "data": ch}))
    DASH.start()
    if not demo:
        def _fire(label: str) -> None:
            BUS.publish({"event": "timer.fire", "data": {"label": label}})
            BUS.publish({"event": "timer.update", "data": {"timers": sysinfo.timers()}})
            sysinfo.notify("JARVIS ⏰", label)
        sysinfo.TimerWatcher(_fire).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"[JARVIS HUD] http://{args.host}:{args.port}  →  Hermes API {CONFIG['hermes_url']}", flush=True)
    BUS.publish({"event": "hud.online", "data": {}})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[JARVIS HUD] offline")
        sys.exit(0)


if __name__ == "__main__":
    main()
