"""
Долговременное состояние ядра JARVIS (режим, таймеры).

Хранится в JSON-файле в каталоге данных плагина
(~/.hermes/plugin-data/jarvis-core/state.json) — переживает рестарты и
обновления плагина. Доступ потокобезопасен (таймеры срабатывают из фоновых потоков).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

MODE_HINTS = {
    "normal": "обычный режим",
    "focus": "не беспокоить: отвечай максимально кратко, без лишних слов, не предлагай ничего сверх запроса",
    "night": "тихий ночной режим: кратко, спокойно, не включай громкие звуки",
    "presentation": "идёт презентация: не трогай окна и громкость, не показывай уведомления",
}

_LOCK = threading.RLock()


def _path() -> Path:
    base = os.environ.get("JARVIS_STATE_DIR")
    if not base:
        try:
            # официальное место для данных плагина Hermes
            from plugins.plugin_storage import plugin_data_dir  # type: ignore

            base = str(plugin_data_dir("jarvis-core"))
        except Exception:  # noqa: BLE001 — вне Hermes (тесты)
            base = os.path.expanduser("~/.hermes/plugin-data/jarvis-core")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p / "state.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except Exception:  # noqa: BLE001
        return {"mode": "normal", "timers": []}


def _save(data: dict) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(_path())


def get_mode() -> str:
    with _LOCK:
        return _load().get("mode", "normal")


def set_mode(mode: str) -> None:
    with _LOCK:
        d = _load()
        d["mode"] = mode
        _save(d)


def add_timer(label: str, target: dt.datetime) -> None:
    with _LOCK:
        d = _load()
        d.setdefault("timers", []).append({"label": label, "target": target.isoformat()})
        _save(d)


def cancel_timer(label: str) -> int:
    with _LOCK:
        d = _load()
        before = len(d.get("timers", []))
        if label:
            d["timers"] = [t for t in d.get("timers", []) if label.lower() not in t["label"].lower()]
        else:
            d["timers"] = []
        _save(d)
        return before - len(d["timers"])


def timer_alive(label: str, target: dt.datetime) -> bool:
    with _LOCK:
        return any(t["label"] == label and t["target"] == target.isoformat() for t in _load().get("timers", []))


def _human(delta: dt.timedelta) -> str:
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s} с"
    if s < 3600:
        return f"{s // 60} мин"
    return f"{s // 3600} ч {(s % 3600) // 60} мин"


def active_timers(raw: bool = False) -> list[dict]:
    """Список живых таймеров; протухшие удаляются."""
    with _LOCK:
        d = _load()
        now = dt.datetime.now()
        alive = [t for t in d.get("timers", []) if dt.datetime.fromisoformat(t["target"]) > now]
        if len(alive) != len(d.get("timers", [])):
            d["timers"] = alive
            _save(d)
        if raw:
            return alive
        return [
            {"label": t["label"], "fires_at": t["target"][11:16], "remaining_h": _human(dt.datetime.fromisoformat(t["target"]) - now)}
            for t in alive
        ]
