"""
Низкоуровневые помощники для работы с macOS.

Всё общение с системой идёт через три канала:
  * osascript  — AppleScript / JXA (управление приложениями, System Events);
  * shell      — утилиты macOS (open, pmset, screencapture, mdfind, shortcuts…);
  * файлы      — папка кэша для скриншотов и т.п.

Каждая функция безопасна к ошибкам: возвращает (ok, output) и никогда не бросает
исключений наружу — обработчики инструментов оборачивают результат в JSON.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import time
from pathlib import Path
from typing import Iterable

IS_MAC = platform.system() == "Darwin"


class MacError(Exception):
    """Ошибка выполнения системной команды с человекочитаемым сообщением."""


# ───────────────────────────── запуск команд ───────────────────────────────

def run(cmd: list[str] | str, timeout: int = 30, check: bool = True) -> str:
    """Запустить shell-команду и вернуть stdout (strip).

    cmd — список аргументов (предпочтительно) или строка (будет разобрана shlex).
    """
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise MacError(f"Команда не найдена: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise MacError(f"Команда превысила лимит {timeout}с: {' '.join(cmd)}") from e
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise MacError(err or f"Код возврата {proc.returncode}")
    return (proc.stdout or "").strip()


def osascript(script: str, language: str = "applescript", timeout: int = 30) -> str:
    """Выполнить AppleScript (или JXA при language='javascript')."""
    cmd = ["osascript"]
    if language == "javascript":
        cmd += ["-l", "JavaScript"]
    cmd += ["-e", script]
    try:
        return run(cmd, timeout=timeout)
    except MacError as e:
        msg = str(e)
        low = msg.lower()
        # Наиболее частые проблемы — переводим на понятный язык (macOS локализует текст ошибок: en/ru)
        if "assistive access" in low or "упрощенного доступа" in low or "упрощённого доступа" in low or "-1719" in msg or "1002" in msg:
            raise MacError(
                "Нет прав Accessibility. Откройте Системные настройки → Конфиденциальность и безопасность → "
                "Универсальный доступ и разрешите Terminal / iTerm / Hermes."
            ) from e
        if "not authorized to send apple events" in low or "не разрешено отправлять" in low or "-1743" in msg:
            raise MacError(
                "macOS запросила разрешение на Автоматизацию. Нажмите «Разрешить» в диалоге или включите доступ в "
                "Системные настройки → Конфиденциальность → Автоматизация."
            ) from e
        raise


def which(binary: str) -> str | None:
    """Путь к бинарнику или None (учитывает Homebrew-пути)."""
    for p in [*os.environ.get("PATH", "").split(":"), "/opt/homebrew/bin", "/usr/local/bin"]:
        cand = Path(p) / binary
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def as_str(value) -> str:
    """Экранировать значение для вставки в AppleScript-строку."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def hermes_home() -> Path:
    """$HERMES_HOME или ~/.hermes."""
    return Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser()


def cache_dir(sub: str = "") -> Path:
    base = Path(os.environ["JARVIS_CACHE_DIR"]).expanduser() if os.environ.get("JARVIS_CACHE_DIR") else hermes_home() / "cache" / "jarvis"
    if sub:
        base = base / sub
    base.mkdir(parents=True, exist_ok=True)
    return base


def stamp(prefix: str, ext: str) -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"


# ───────────────────────────── типовые действия ────────────────────────────

FOLDER_ALIASES = {
    "downloads": "~/Downloads", "загрузки": "~/Downloads",
    "desktop": "~/Desktop", "рабочий стол": "~/Desktop",
    "documents": "~/Documents", "документы": "~/Documents",
    "home": "~", "домашняя": "~",
    "applications": "/Applications", "программы": "/Applications",
    "pictures": "~/Pictures", "изображения": "~/Pictures",
    "movies": "~/Movies", "music": "~/Music",
}


def resolve_target(target: str) -> str:
    """URL оставить как есть; папки-алиасы и ~ раскрыть."""
    t = target.strip()
    low = t.lower()
    if low in FOLDER_ALIASES:
        return str(Path(FOLDER_ALIASES[low]).expanduser())
    if "://" in t or low.startswith("mailto:"):
        return t
    if "." in t and " " not in t and "/" not in t and not t.startswith("~"):
        # похоже на домен вроде youtube.com
        return "https://" + t
    return str(Path(t).expanduser())


def frontmost_app() -> str:
    return osascript('tell application "System Events" to get name of first application process whose frontmost is true')


def running_apps() -> list[str]:
    out = osascript('tell application "System Events" to get name of every application process whose background only is false')
    return [x.strip() for x in out.split(",") if x.strip()]


def detect_player(prefer: str = "auto") -> str:
    """Определить активный плеер: spotify или music."""
    if prefer in ("music", "spotify"):
        return prefer
    apps = {a.lower() for a in running_apps()}
    if "spotify" in apps:
        return "spotify"
    return "music"


def json_ok(**data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False)


def json_err(message: str, **data) -> str:
    return json.dumps({"success": False, "error": message, **data}, ensure_ascii=False)


def require_mac() -> None:
    if not IS_MAC:
        raise MacError("Этот инструмент работает только на macOS")


def safe_list(items: Iterable[str]) -> list[str]:
    return [i for i in (s.strip() for s in items) if i]
