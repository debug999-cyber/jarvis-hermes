"""
Живые данные для виджетов HUD (без LLM): календарь, батарея, Focus, таймеры, режим,
база знаний, версия/обновления, модель Hermes.

Всё читается локально и кэшируется в фоне, чтобы HTTP-запрос /api/dashboard отвечал мгновенно.
На не-macOS (тесты, Linux) macOS-части возвращают None — виджеты покажут «—».
Режим demo подставляет правдоподобные данные для скриншотов и разработки.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

IS_MAC = platform.system() == "Darwin"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
STATE_FILE = Path(os.environ.get("JARVIS_STATE_DIR") or (HERMES_HOME / "plugin-data" / "jarvis-core")) / "state.json"
BRAIN_DB = Path(os.environ.get("JARVIS_BRAIN_DB") or (HERMES_HOME / "plugin-data" / "jarvis-brain" / "brain.db"))
INSTALL_JSON = HERMES_HOME / "jarvis" / "install.json"
UPDATE_JSON = HERMES_HOME / "jarvis" / "update.json"
CONFIG_YAML = HERMES_HOME / "config.yaml"

_LOCK = threading.RLock()


# ───────────────────────────── утилиты ─────────────────────────────

def _run(cmd: list[str], timeout: float = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


# ───────────────────────────── источники ─────────────────────────────

def battery() -> dict | None:
    """pmset -g batt → {'percent': 82, 'charging': False, 'remaining': '3:12'}"""
    if not IS_MAC:
        return None
    out = _run(["pmset", "-g", "batt"], timeout=3)
    m = re.search(r"(\d+)%;\s*([\w ]+?);(?:\s*([\d:]+) remaining)?", out)
    if not m:
        return None
    status = m.group(2).strip()
    return {"percent": int(m.group(1)), "charging": status in ("charging", "charged", "finishing charge"),
            "plugged": "AC Power" in out, "remaining": m.group(3) or ""}


def focus() -> dict | None:
    """Активный режим Focus macOS из ~/Library/DoNotDisturb (нужен Full Disk Access; иначе None)."""
    if not IS_MAC:
        return None
    base = Path.home() / "Library" / "DoNotDisturb" / "DB"
    try:
        records = (_read_json(base / "Assertions.json", {}).get("data") or [{}])[0].get("storeAssertionRecords") or []
        if not records:
            return {"active": False, "name": ""}
        rec = max(records, key=lambda r: r.get("assertionStartDateTimestamp", 0))
        mode_id = rec["assertionDetails"]["assertionDetailsModeIdentifier"]
        modes = (_read_json(base / "ModeConfigurations.json", {}).get("data") or [{}])[0].get("modeConfigurations") or {}
        name = modes.get(mode_id, {}).get("mode", {}).get("name") or mode_id.rsplit(".", 1)[-1]
        return {"active": True, "name": name}
    except (KeyError, TypeError, IndexError):
        return None


_CAL_SCRIPT = '''
set startDate to (current date)
set time of startDate to 0
set endDate to startDate + 1 * days
set output to ""
tell application "Calendar"
    repeat with cal in calendars
        try
            set evs to (every event of cal whose start date ≥ startDate and start date < endDate)
            repeat with ev in evs
                set sd to start date of ev
                set ed to end date of ev
                set output to output & (name of cal) & "|" & (summary of ev) & "|" & (hours of sd) & ":" & (minutes of sd) & "|" & (hours of ed) & ":" & (minutes of ed) & "|" & (allday event of ev) & linefeed
            end repeat
        end try
    end repeat
end tell
return output
'''


def calendar_today() -> list[dict] | None:
    """События на сегодня из Calendar.app (AppleScript). Медленно (секунды) — вызывать только из фонового потока."""
    if not IS_MAC:
        return None
    out = _run(["osascript", "-e", _CAL_SCRIPT], timeout=45)
    events = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        def hm(s: str) -> str:
            h, _, m = s.partition(":")
            return f"{int(h or 0):02d}:{int(m or 0):02d}"
        events.append({"calendar": parts[0], "title": parts[1], "start": hm(parts[2]), "end": hm(parts[3]),
                       "all_day": parts[4].strip() == "true"})
    events.sort(key=lambda e: (not e["all_day"], e["start"]))
    return events


def timers() -> list[dict]:
    """Живые таймеры из state.json ядра (общий файл с плагином jarvis-core)."""
    with _LOCK:
        d = _read_json(STATE_FILE, {})
        now = dt.datetime.now()
        alive = []
        for t in d.get("timers", []):
            try:
                target = dt.datetime.fromisoformat(t["target"])
            except (KeyError, ValueError):
                continue
            if target > now:
                alive.append({"label": t["label"], "target": t["target"], "seconds": int((target - now).total_seconds())})
        return alive


def add_timer(label: str, target: dt.datetime) -> None:
    with _LOCK:
        d = _read_json(STATE_FILE, {"mode": "normal", "timers": []})
        d.setdefault("timers", []).append({"label": label, "target": target.isoformat()})
        _write_json(STATE_FILE, d)


def cancel_timer(label: str, target: str | None = None) -> int:
    with _LOCK:
        d = _read_json(STATE_FILE, {})
        before = d.get("timers", [])
        d["timers"] = [t for t in before if not (t.get("label") == label and (target is None or t.get("target") == target))]
        _write_json(STATE_FILE, d)
        return len(before) - len(d["timers"])


def get_mode() -> str:
    return _read_json(STATE_FILE, {}).get("mode", "normal")


def set_mode(mode: str) -> None:
    with _LOCK:
        d = _read_json(STATE_FILE, {"mode": "normal", "timers": []})
        d["mode"] = mode
        _write_json(STATE_FILE, d)


def brain_stats() -> dict | None:
    if not BRAIN_DB.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{BRAIN_DB}?mode=ro", uri=True, timeout=1)
        notes = c.execute("SELECT COUNT(*) FROM notes WHERE status='active'").fetchone()[0]
        cards = c.execute("SELECT COUNT(*) FROM entities WHERE status='active'").fetchone()[0]
        pending = c.execute("SELECT COUNT(*) FROM turns WHERE digested=0").fetchone()[0]
        c.close()
        return {"notes": notes, "cards": cards, "pending": pending}
    except sqlite3.Error:
        return None


def version_info() -> dict:
    inst = _read_json(INSTALL_JSON, {})
    upd = _read_json(UPDATE_JSON, {})
    return {"version": inst.get("version", ""), "channel": inst.get("channel", ""),
            "update_available": bool(upd.get("available")), "latest": upd.get("latest", "")}


def hermes_model() -> str:
    """model.default из ~/.hermes/config.yaml (без PyYAML — простой разбор)."""
    try:
        lines = CONFIG_YAML.read_text().splitlines()
    except OSError:
        return ""
    in_model = False
    for line in lines:
        if re.match(r"^model:\s*(\S.*)?$", line):
            rest = line.split(":", 1)[1].strip().strip('"').strip("'")
            if rest:
                return rest
            in_model = True
            continue
        if in_model:
            if line and not line.startswith((" ", "\t")):
                break
            m = re.match(r"^\s+default:\s*(.+)$", line)
            if m:
                return m.group(1).split("#", 1)[0].strip().strip('"').strip("'")
    return ""


# ───────────────────────────── demo-данные ─────────────────────────────

def demo_snapshot() -> dict:
    now = dt.datetime.now()
    return {
        "battery": {"percent": 82, "charging": False, "plugged": False, "remaining": "4:10"},
        "focus": {"active": True, "name": "Работа"},
        "calendar": [
            {"calendar": "Работа", "title": "Стендап команды", "start": "09:30", "end": "09:45", "all_day": False},
            {"calendar": "Работа", "title": "Ревью продукта", "start": "13:00", "end": "14:00", "all_day": False},
            {"calendar": "Личное", "title": "Стратегический созвон", "start": "16:00", "end": "17:00", "all_day": False},
        ],
        "timers": [
            {"label": "Глубокая работа", "target": (now + dt.timedelta(minutes=85)).isoformat(), "seconds": 85 * 60},
            {"label": "Перерыв", "target": (now + dt.timedelta(minutes=15)).isoformat(), "seconds": 15 * 60},
        ],
        "mode": "focus",
        "brain": {"notes": 214, "cards": 37, "pending": 3},
        "version": {"version": "1.5.0", "channel": "stable", "update_available": False, "latest": ""},
        "model": "claude-sonnet-4.5",
        "demo": True,
    }


# ───────────────────────────── фоновый сборщик ─────────────────────────────

class Collector:
    """Обновляет снимок в фоне. Быстрые источники — каждые `fast` с, календарь — каждые `slow` с."""

    def __init__(self, demo: bool = False, fast: float = 20, slow: float = 120, on_change=None):
        self.demo = demo
        self.fast, self.slow = fast, slow
        self.on_change = on_change
        self._data: dict = demo_snapshot() if demo else {"calendar": None}
        self._lock = threading.Lock()
        self._last_slow = 0.0

    def snapshot(self) -> dict:
        with self._lock:
            d = dict(self._data)
        if not self.demo:
            d["timers"] = timers()            # всегда свежие — секунды тикают
            d["mode"] = get_mode()
        d["ts"] = time.time()
        return d

    def refresh(self, slow: bool = False) -> None:
        if self.demo:
            return
        new = {"battery": battery(), "focus": focus(), "brain": brain_stats(), "version": version_info(),
               "model": hermes_model(), "timers": timers(), "mode": get_mode()}
        with self._lock:
            new["calendar"] = self._data.get("calendar")
        if slow or time.time() - self._last_slow > self.slow:
            new["calendar"] = calendar_today()
            self._last_slow = time.time()
        with self._lock:
            changed = {k: v for k, v in new.items() if k != "timers" and self._data.get(k) != v}
            self._data = new
        if changed and self.on_change:
            self.on_change(changed)

    def start(self) -> None:
        def loop():
            self.refresh(slow=True)
            while True:
                time.sleep(self.fast)
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001 — сборщик не должен падать
                    pass
        threading.Thread(target=loop, name="hud-sysinfo", daemon=True).start()


class TimerWatcher:
    """Срабатывание таймеров, поставленных из HUD (когда процесс Hermes с плагином не запущен).

    Плагин jarvis-core стреляет ровно в момент и удаляет таймер из state.json; мы ждём ещё 3 секунды
    и стреляем только если таймер всё ещё в файле — так двойного сигнала не бывает.
    """

    def __init__(self, on_fire):
        self.on_fire = on_fire

    def start(self) -> None:
        def loop():
            while True:
                time.sleep(2)
                try:
                    now = dt.datetime.now()
                    for t in _read_json(STATE_FILE, {}).get("timers", []):
                        try:
                            target = dt.datetime.fromisoformat(t["target"])
                        except (KeyError, ValueError):
                            continue
                        if (now - target).total_seconds() > 3:
                            if cancel_timer(t["label"], t["target"]):
                                self.on_fire(t["label"])
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, name="hud-timers", daemon=True).start()


def notify(title: str, text: str, sound: str = "Glass") -> None:
    if not IS_MAC:
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    subprocess.Popen(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}" sound name "{sound}"'])
    subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"])
