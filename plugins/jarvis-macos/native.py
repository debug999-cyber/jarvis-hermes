"""
Нативные CLI-бэкенды для jarvis-macos — готовые проекты вместо собственного AppleScript.

Принцип №1 проекта (docs/DEVELOPMENT.md): сначала GitHub, потом свой код. Для календаря, напоминаний и окон
на GitHub есть зрелые инструменты на EventKit/Accessibility — они в десятки раз быстрее AppleScript, отдают JSON
и не требуют разрешения «Автоматизация» для каждого приложения:

  * ical      — BRO3886/ical        (EventKit, JSON, натуральные даты)       brew install BRO3886/tap/ical
  * remindctl — openclaw/remindctl  (EventKit, JSON; тот же CLI, что в навыке apple-reminders Hermes)
                                                                             brew install steipete/tap/remindctl
  * peekaboo  — openclaw/Peekaboo   (окна/скриншоты/AX, macOS 15+, 5k★)      brew install steipete/tap/peekaboo

Каждая функция здесь возвращает None, если CLI не установлен или ответил не так, как ожидалось, — тогда
обработчик в tools.py откатывается на прежний AppleScript. Так JARVIS работает и без brew-утилит, но с ними
становится быстрее и надёжнее. Поле `backend` в ответе показывает, какой путь сработал.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
from pathlib import Path

from . import mac

logger = logging.getLogger(__name__)

# Имя CLI → как поставить (для подсказок doctor/selftest)
INSTALL_HINTS = {
    "ical": "brew tap BRO3886/tap && brew install ical",
    "remindctl": "brew install steipete/tap/remindctl",
    "peekaboo": "brew install steipete/tap/peekaboo   # macOS 15+",
}


def which(name: str) -> str | None:
    """Путь к CLI (учитывает Homebrew). Вынесено, чтобы тесты могли подменить."""
    return mac.which(name)


def run(cmd: list[str], timeout: int = 40) -> tuple[int, str]:
    """Запуск без исключений: (код, stdout+stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def run_json(cmd: list[str], timeout: int = 40):
    """Запустить CLI и разобрать JSON из вывода. None — если CLI упал или JSON не нашёлся."""
    code, out = run(cmd, timeout)
    if code != 0:
        logger.debug("%s → код %s: %s", cmd[0], code, out[:200])
        return None
    text = out.strip()
    for opener in ("[", "{"):
        i = text.find(opener)
        if i >= 0:
            try:
                return json.loads(text[i:])
            except ValueError:
                continue
    return None


def available() -> dict[str, str | None]:
    """Какие нативные CLI установлены (для doctor / status)."""
    return {name: which(name) for name in INSTALL_HINTS}


# ─────────────────────────────── даты ──────────────────────────────────────

def _hhmm(iso: str) -> str:
    """'2026-09-12T15:00:00+03:00' → '15:00'. Пустое/кривое → как есть."""
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M")
    except (ValueError, AttributeError):
        return iso or ""


# ─────────────────────────────── календарь (ical) ──────────────────────────

def _norm_event(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "calendar": e.get("calendar", ""),
        "title": e.get("title", ""),
        "start": "весь день" if e.get("all_day") else _hhmm(e.get("start_date", "")),
        "end": "" if e.get("all_day") else _hhmm(e.get("end_date", "")),
        "all_day": bool(e.get("all_day")),
        "location": e.get("location") or "",
    }


def calendar_events(day: dt.date) -> list[dict] | None:
    """События на дату через `ical list -o json`. None → откат на AppleScript."""
    bin_ = which("ical")
    if not bin_:
        return None
    data = run_json([bin_, "list", "-f", f"{day.isoformat()} 00:00", "-t", f"{day.isoformat()} 23:59", "-o", "json"])
    if not isinstance(data, list):
        return None
    events = [_norm_event(e) for e in data if isinstance(e, dict)]
    events.sort(key=lambda e: (not e["all_day"], e["start"]))
    return events


def calendar_create(title: str, start: dt.datetime, duration_min: int, calendar: str | None = None,
                    location: str | None = None) -> dict | None:
    bin_ = which("ical")
    if not bin_:
        return None
    end = start + dt.timedelta(minutes=duration_min)
    cmd = [bin_, "add", title, "-s", start.strftime("%Y-%m-%dT%H:%M:%S"), "-e", end.strftime("%Y-%m-%dT%H:%M:%S"), "-o", "json"]
    if calendar:
        cmd += ["-c", calendar]
    if location:
        cmd += ["-l", location]
    code, out = run(cmd)
    if code != 0:
        return {"error": out.strip()[:300]}
    return {"created": True}


# ─────────────────────────────── напоминания (remindctl) ───────────────────

def _norm_reminder(r: dict) -> dict:
    due = r.get("dueDate") or ""
    return {
        "id": r.get("id"),
        "title": r.get("title", ""),
        "list": r.get("listName", ""),
        "due": due[:16].replace("T", " ") if due else None,
        "notes": (r.get("notes") or "")[:200],
        "priority": r.get("priority"),
    }


def reminders_list(list_name: str | None = None) -> list[dict] | None:
    bin_ = which("remindctl")
    if not bin_:
        return None
    cmd = [bin_, "open", "--json"] + (["--list", list_name] if list_name else [])
    data = run_json(cmd)
    if not isinstance(data, list):
        return None
    return [_norm_reminder(r) for r in data if isinstance(r, dict)]


def reminders_add(title: str, due: str | None = None, list_name: str | None = None, notes: str | None = None) -> dict | None:
    bin_ = which("remindctl")
    if not bin_:
        return None
    cmd = [bin_, "add", title, "--json"]
    if due:
        cmd += ["--due", due]
    if list_name:
        cmd += ["--list", list_name]
    if notes:
        cmd += ["--notes", notes]
    code, out = run(cmd)
    if code != 0:
        return {"error": out.strip()[:300]}
    return {"added": True}


def reminders_complete(title: str, list_name: str | None = None) -> dict | None:
    """Найти по подстроке названия и отметить выполненным (по стабильному id)."""
    bin_ = which("remindctl")
    if not bin_:
        return None
    found = run_json([bin_, "search", title, "--json"] + (["--list", list_name] if list_name else []))
    if not isinstance(found, list) or not found:
        return {"error": f"Напоминание «{title}» не найдено"}
    target = next((r for r in found if not r.get("isCompleted")), found[0])
    code, out = run([bin_, "complete", str(target.get("id"))])
    if code != 0:
        return {"error": out.strip()[:300]}
    return {"completed": target.get("title", title), "id": target.get("id")}


# ─────────────────────────────── окна и экран (peekaboo) ───────────────────

def window_list(app: str) -> list[dict] | None:
    bin_ = which("peekaboo")
    if not bin_:
        return None
    data = run_json([bin_, "window", "list", "--app", app, "--json"])
    rows = data.get("data", data).get("windows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    out = []
    for w in rows:
        if isinstance(w, dict):
            out.append({"id": w.get("window_id") or w.get("id"), "title": w.get("title") or w.get("name") or "",
                        "frontmost": bool(w.get("is_frontmost")), "minimized": bool(w.get("is_minimized"))})
    return out


def window_action(app: str, action: str, bounds: tuple[int, int, int, int] | None = None) -> bool | None:
    """minimize | maximize | focus | set-bounds(x, y, w, h). None → нет peekaboo; False → ошибка."""
    bin_ = which("peekaboo")
    if not bin_:
        return None
    if action == "set-bounds" and bounds:
        x, y, w, h = bounds
        cmd = [bin_, "window", "set-bounds", "--app", app, "--x", str(x), "--y", str(y), "--width", str(w), "--height", str(h)]
    elif action in ("minimize", "maximize", "focus", "restore", "close"):
        cmd = [bin_, "window", action, "--app", app]
    else:
        return None
    code, _ = run(cmd, timeout=20)
    return code == 0


def screenshot(path: Path, mode: str = "screen") -> bool | None:
    """Скриншот через peekaboo see (без карты элементов — самый дешёвый путь). None → нет peekaboo."""
    bin_ = which("peekaboo")
    if not bin_:
        return None
    pmode = "frontmost" if mode == "front_window" else "screen"
    code, _ = run([bin_, "see", "--no-elements", "--mode", pmode, "--path", str(path)], timeout=30)
    return code == 0 and path.exists()


def screen_text(path: Path, mode: str = "screen", app: str | None = None, limit: int = 120) -> dict | None:
    """Скриншот + распознанный текст экрана (Apple Vision через `peekaboo see --ocr`). None → нет peekaboo.

    Возвращает {"path", "app", "window", "lines": [...], "elements": N}. Текст даёт модели прочитать ошибку/
    документ без vision-модели (быстрее и дешевле), картинка остаётся для vision_analyze при необходимости.
    """
    bin_ = which("peekaboo")
    if not bin_:
        return None
    cmd = [bin_, "see", "--ocr", "--json", "--path", str(path), "--mode", "frontmost" if mode == "front_window" else "screen"]
    if app:
        cmd += ["--app", app]
    data = run_json(cmd, timeout=60)
    if not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    lines, seen = [], set()
    for el in payload.get("ui_elements") or []:
        if not isinstance(el, dict):
            continue
        text = " ".join(str(el.get(k)) for k in ("title", "label", "value") if el.get(k)).strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
        if len(lines) >= limit:
            break
    return {"path": str(path), "app": payload.get("application_name"), "window": payload.get("window_title"),
            "lines": lines, "elements": payload.get("element_count")}
