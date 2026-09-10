#!/usr/bin/env python3
"""
JARVIS selftest — проверка macOS-слоя на РЕАЛЬНОМ Mac без LLM.

Прогоняет каждый mac_* инструмент в безопасном (read-only) режиме и печатает таблицу:
    ✔ работает  ·  ⚠ нет прав → какую панель открыть  ·  ✖ сломано (текст ошибки)
Ничего не меняет в системе: не трогает громкость, окна, файлы; не отправляет сообщений.

Запуск:  jarvis selftest        (или python3 ~/.hermes/jarvis/scripts/selftest.py [--json] [--fix])
  --fix   открыть панели Системных настроек для всех «⚠ нет прав»
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
CANDIDATES = [HERMES_HOME / "plugins" / "jarvis-macos", Path(__file__).resolve().parents[1] / "plugins" / "jarvis-macos"]

PERMISSION_HINTS = {
    "Accessibility": ("Универсальный доступ", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"),
    "Автоматизац": ("Автоматизация", "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"),
    "Not authorized": ("Автоматизация", "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"),
    "Screen": ("Запись экрана", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
    "Full Disk": ("Полный доступ к диску", "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"),
    "Камера": ("Камера", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"),
}

# (инструмент, аргументы, что проверяем) — только чтение
CHECKS = [
    ("mac_app", {"action": "list_running"}, "System Events: список приложений"),
    ("mac_volume", {"action": "get"}, "громкость"),
    ("mac_dark_mode", {"action": "get"}, "тема оформления"),
    ("mac_battery", {}, "pmset"),
    ("mac_system_info", {"section": "hardware"}, "sysctl/sw_vers"),
    ("mac_system_info", {"section": "network"}, "сеть"),
    ("mac_wifi", {"action": "status"}, "networksetup"),
    ("mac_bluetooth", {"action": "status"}, "blueutil (brew)"),
    ("mac_media", {"action": "now_playing"}, "Music/Spotify"),
    ("mac_clipboard", {"action": "get"}, "pbpaste"),
    ("mac_spotlight", {"query": "kMDItemKind == 'Application'", "limit": 1}, "mdfind"),
    ("mac_window", {"action": "list"}, "окна (Accessibility)"),
    ("mac_calendar", {"action": "today"}, "Calendar.app (Автоматизация)"),
    ("mac_reminders", {"action": "list"}, "Reminders.app (Автоматизация)"),
    ("mac_notes", {"action": "search", "query": "jarvis-selftest-nonexistent"}, "Notes.app (Автоматизация)"),
    ("mac_contacts", {"action": "search", "query": "zzz-nonexistent"}, "Contacts.app (Автоматизация)"),
    ("mac_shortcut", {"action": "list"}, "Shortcuts"),
    ("mac_screenshot", {"target": "screen"}, "screencapture (Запись экрана)"),
    ("mac_focus", {"action": "get"}, "Focus (DoNotDisturb DB)"),
    ("mac_file_manage", {"action": "list", "path": "~/Downloads", "limit": 3}, "файлы"),
    ("mac_notify", {"title": "JARVIS selftest", "message": "Уведомления работают"}, "display notification"),
]


def load_plugin():
    for base in CANDIDATES:
        if (base / "__init__.py").exists():
            spec = importlib.util.spec_from_file_location("jarvis_macos_selftest", base / "__init__.py",
                                                          submodule_search_locations=[str(base)])
            mod = importlib.util.module_from_spec(spec)
            sys.modules["jarvis_macos_selftest"] = mod
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod, base
    raise SystemExit("Плагин jarvis-macos не найден ни в ~/.hermes/plugins, ни в репозитории")


def classify(result: dict) -> tuple[str, str, str | None]:
    """→ (статус, комментарий, url панели настроек)"""
    if result.get("success"):
        return "ok", "", None
    err = result.get("error", "") + " " + result.get("hint", "")
    for key, (label, url) in PERMISSION_HINTS.items():
        if key.lower() in err.lower():
            return "perm", f"нет прав: {label}", url
    if "не найден" in err.lower() and ("brew" in err.lower() or "команда не найдена" in err.lower()):
        return "missing", err.strip()[:90], None
    return "fail", err.strip()[:110], None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fix", action="store_true", help="открыть панели настроек для недостающих прав")
    args = ap.parse_args()

    if platform.system() != "Darwin":
        print("selftest имеет смысл только на macOS (здесь всё будет «только macOS»)", file=sys.stderr)
    mod, base = load_plugin()
    handlers = mod.tools.HANDLERS
    rows, panels = [], []
    for name, a, what in CHECKS:
        fn = handlers.get(name)
        if not fn:
            rows.append({"tool": name, "status": "fail", "note": "нет обработчика", "what": what, "ms": 0})
            continue
        t0 = time.time()
        try:
            res = json.loads(fn(a))
        except Exception as e:  # noqa: BLE001
            res = {"success": False, "error": f"{type(e).__name__}: {e}"}
        ms = int((time.time() - t0) * 1000)
        st, note, url = classify(res)
        if url:
            panels.append(url)
        rows.append({"tool": name, "status": st, "note": note, "what": what, "ms": ms})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "✔", "perm": "⚠", "missing": "◌", "fail": "✖"}
        print(f"\nJARVIS selftest · {platform.platform()} · плагин: {base}\n")
        for r in rows:
            print(f"  {icon[r['status']]}  {r['tool']:<16} {r['what']:<34} {r['ms']:>5} мс  {r['note']}")
        ok = sum(r["status"] == "ok" for r in rows)
        perm = sum(r["status"] == "perm" for r in rows)
        fail = sum(r["status"] == "fail" for r in rows)
        miss = sum(r["status"] == "missing" for r in rows)
        print(f"\n  итого: {ok} работает · {perm} нет прав · {miss} нет утилиты (brew) · {fail} сломано\n")
        if perm:
            print("  Права выдаются терминалу, из которого запущен JARVIS (Terminal / iTerm / Warp).")
            print("  Запустите с --fix, чтобы открыть нужные панели, затем перезапустите терминал и повторите.\n")
        if fail:
            print("  «✖ сломано» — пришлите вывод `jarvis selftest --json` разработчику или JARVIS'у: «почини selftest».\n")
    if args.fix:
        for url in dict.fromkeys(panels):
            subprocess.run(["open", url], check=False)
    return 0 if not any(r["status"] == "fail" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
