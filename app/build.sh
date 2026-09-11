#!/usr/bin/env bash
# Сборка JARVIS.app из одного Swift-файла. Нужны только Xcode Command Line Tools (swiftc).
#   bash app/build.sh [/путь/куда/JARVIS.app]      по умолчанию ~/Applications/JARVIS.app
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$HOME/Applications/JARVIS.app}"
VERSION="$(cat "$HERE/../VERSION" 2>/dev/null || echo 0.0.0)"
command -v swiftc >/dev/null 2>&1 || { echo "swiftc не найден: xcode-select --install"; exit 2; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/JARVIS.app/Contents/MacOS" "$TMP/JARVIS.app/Contents/Resources"
swiftc -O -framework AppKit -o "$TMP/JARVIS.app/Contents/MacOS/JARVIS" "$HERE/JarvisMenuBar.swift"
sed "s/__VERSION__/$VERSION/g" "$HERE/Info.plist" > "$TMP/JARVIS.app/Contents/Info.plist"
# иконка: рисуем арк-реактор в .icns через Python + iconutil (если есть); без иконки приложение тоже работает
if command -v iconutil >/dev/null 2>&1 && python3 "$HERE/make_icon.py" "$TMP/AppIcon.iconset" 2>/dev/null; then
  iconutil -c icns "$TMP/AppIcon.iconset" -o "$TMP/JARVIS.app/Contents/Resources/AppIcon.icns" 2>/dev/null || true
fi
# ad-hoc подпись, чтобы macOS не жаловался и разрешения (уведомления, Automation) запоминались за бандлом
codesign --force --deep --sign - "$TMP/JARVIS.app" >/dev/null 2>&1 || true
mkdir -p "$(dirname "$OUT")"
rm -rf "$OUT"; cp -R "$TMP/JARVIS.app" "$OUT"
echo "$OUT"
