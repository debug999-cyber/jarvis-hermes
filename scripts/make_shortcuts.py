#!/usr/bin/env python3
"""
Быстрые команды (Shortcuts.app) для JARVIS — генерируются и подписываются локально, импорт одним кликом.

    jarvis shortcuts            собрать, подписать и открыть для импорта (macOS 12+)
    python3 scripts/make_shortcuts.py --out ~/Desktop/jarvis-shortcuts   только файлы

Зачем генератор, а не готовые .shortcut в репозитории: с macOS 12 Shortcuts импортирует только ПОДПИСАННЫЕ файлы,
а подпись (`shortcuts sign`) привязана к устройству пользователя. Поэтому собираем plist здесь, подписываем на месте
командой `shortcuts sign --mode anyone`, затем `open` → Shortcuts предложит «Добавить».

Команды (все зовут `~/.local/bin/jarvis`, ничего больше):
  «Спросить JARVIS»           — Siri: «Запусти Спросить JARVIS» → вопрос голосом/текстом → ответ окном и уведомлением
  «JARVIS брифинг»            — утренний брифинг в Terminal
  «JARVIS замолчать»          — остановить речь (на кнопку в Control Center / Touch Bar / клавишу)
  «В хранилище JARVIS»        — Share Sheet / Finder Quick Action: выбранные файлы копируются в ~/JARVIS/inbox
  «JARVIS heartbeat»          — тихая проверка «нужно ли что-то сказать?» (для Automation по времени/событию)
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

JARVIS = "$HOME/.local/bin/jarvis"
ENV = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; [ -f "$HOME/.jarvis-home" ] && export HERMES_HOME="$(cat "$HOME/.jarvis-home")"; '


def shell(script: str, input_mode: str = "as arguments", uid: str | None = None) -> dict:
    return {
        "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
        "WFWorkflowActionParameters": {
            "Shell": "/bin/zsh", "Script": ENV + script, "InputMode": input_mode,
            "Input": {"Value": {"Type": "ExtensionInput"}, "WFSerializationType": "WFTextTokenAttachment"},
            "UUID": uid or str(uuid.uuid4()).upper(),
        },
    }


def ask(prompt: str) -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.ask",
            "WFWorkflowActionParameters": {"WFAskActionPrompt": prompt, "WFInputType": "Text", "WFAllowsMultilineText": False}}


def show_result() -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.showresult",
            "WFWorkflowActionParameters": {"Text": {"Value": {"attachmentsByRange": {"{0, 1}": {"Type": "ExtensionInput"}}, "string": "\ufffc"},
                                                    "WFSerializationType": "WFTextTokenString"}}}


def notification(title: str) -> dict:
    return {"WFWorkflowActionIdentifier": "is.workflow.actions.notification",
            "WFWorkflowActionParameters": {"WFNotificationActionTitle": title, "WFNotificationActionSound": False,
                                           "WFNotificationActionBody": {"Value": {"attachmentsByRange": {"{0, 1}": {"Type": "ExtensionInput"}}, "string": "\ufffc"},
                                                                        "WFSerializationType": "WFTextTokenString"}}}


def workflow(actions: list[dict], input_types: list[str] | None = None, color: int = 4282601983, glyph: int = 59511,
             quick_action: bool = False) -> dict:
    wf = {
        "WFWorkflowClientVersion": "2605.0.5", "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": color, "WFWorkflowIconGlyphNumber": glyph},
        "WFWorkflowActions": actions,
        "WFWorkflowInputContentItemClasses": input_types or ["WFStringContentItem"],
        "WFWorkflowTypes": ["NCWidget", "WatchKit"] + (["QuickActions", "ActionExtension"] if quick_action else []),
        "WFWorkflowHasShortcutInputVariables": bool(input_types),
        "WFWorkflowHasOutputFallback": False, "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowImportQuestions": [], "WFQuickActionSurfaces": ["Finder", "ServicesMenu"] if quick_action else [],
    }
    return wf


SHORTCUTS = {
    "Спросить JARVIS": workflow([
        ask("Что спросить у JARVIS?"),
        shell(f'{JARVIS} ask "$@" 2>/dev/null | tail -c 1500'),
        notification("JARVIS"), show_result(),
    ], glyph=59511),
    "JARVIS брифинг": workflow([shell(f'open -a Terminal; sleep 0.5; osascript -e \'tell application "Terminal" to do script "{JARVIS} brief"\' >/dev/null')], glyph=59761),
    "JARVIS замолчать": workflow([shell(f"{JARVIS} hush >/dev/null 2>&1; echo ok")], glyph=59695, color=4292093695),
    "В хранилище JARVIS": workflow([
        shell('mkdir -p "$HOME/JARVIS/inbox"; n=0; for f in "$@"; do [ -e "$f" ] && cp -R "$f" "$HOME/JARVIS/inbox/" && n=$((n+1)); done; '
              f'{JARVIS} vault reindex >/dev/null 2>&1; echo "В хранилище JARVIS: $n файл(ов)"'),
        notification("JARVIS"),
    ], input_types=["WFGenericFileContentItem", "WFImageContentItem", "WFPDFContentItem", "WFRichTextContentItem", "WFURLContentItem"],
       quick_action=True, glyph=59446, color=4271458815),
    "JARVIS heartbeat": workflow([shell(f'{JARVIS} heartbeat 2>/dev/null | tail -c 500 | grep -v "^NO_REPLY$" || true'), notification("JARVIS")], glyph=59731),
}


def build(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for name, wf in SHORTCUTS.items():
        p = out / f"{name}.shortcut"
        with open(p, "wb") as f:
            plistlib.dump(wf, f, fmt=plistlib.FMT_BINARY)
        files.append(p)
    return files


def sign_and_import(files: list[Path]) -> int:
    if sys.platform != "darwin" or not shutil.which("shortcuts"):
        print("Подпись и импорт возможны только на macOS 12+ (команда `shortcuts`). Файлы собраны:", *files, sep="\n  ")
        return 0
    signed_dir = files[0].parent / "signed"
    signed_dir.mkdir(exist_ok=True)
    ok = 0
    for p in files:
        dst = signed_dir / p.name
        r = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(p), "--output", str(dst)], capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            print(f"  ✖ {p.name}: не подписалась ({(r.stderr or r.stdout).strip()[:120]})")
            continue
        ok += 1
        subprocess.run(["open", str(dst)], check=False)  # Shortcuts.app покажет «Добавить быструю команду»
    print(f"  ✔ подписано {ok}/{len(files)} — подтвердите добавление в открывшихся окнах Shortcuts.")
    print("  Siri: «Запусти Спросить JARVIS». Finder: правый клик на файле → Быстрые действия → В хранилище JARVIS.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/Library/Application Support/JARVIS/shortcuts"))
    ap.add_argument("--no-import", action="store_true", help="только собрать файлы")
    a = ap.parse_args(argv)
    files = build(Path(a.out).expanduser())
    if a.no_import:
        print(*files, sep="\n")
        return 0
    return sign_and_import(files)


if __name__ == "__main__":
    sys.exit(main())
