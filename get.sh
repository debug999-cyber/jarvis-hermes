#!/usr/bin/env bash
# JARVIS — установка одной командой (macOS):
#
#     curl -fsSL https://raw.githubusercontent.com/debug999-cyber/jarvis-hermes/main/get.sh | bash
#
# Что делает: скачивает последний стабильный релиз в ~/Downloads/jarvis-hermes (git clone, если git есть,
# иначе zip релиза), затем запускает обычный install.sh — интерактивно, с вопросами (stdin берём из /dev/tty,
# поэтому работает даже через `curl | bash`). Переменные:
#     JARVIS_REPO=owner/name   другой репозиторий      JARVIS_CHANNEL=main   свежий main вместо релиза
#     JARVIS_DIR=~/path        куда положить исходники  JARVIS_INSTALL_ARGS="--yes --no-voice"  флаги install.sh
set -euo pipefail

REPO="${JARVIS_REPO:-debug999-cyber/jarvis-hermes}"
CHANNEL="${JARVIS_CHANNEL:-stable}"
DIR="${JARVIS_DIR:-$HOME/Downloads/jarvis-hermes}"
ARGS="${JARVIS_INSTALL_ARGS:-}"

CB=$'\033[1;36m'; CG=$'\033[1;32m'; CR=$'\033[1;31m'; CD=$'\033[2m'; C0=$'\033[0m'
say()  { printf "%s▸ %s%s\n" "$CB" "$*" "$C0"; }
ok()   { printf "  %s✔ %s%s\n" "$CG" "$*" "$C0"; }
fail() { printf "%s✖ %s%s\n" "$CR" "$*" "$C0" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || fail "JARVIS рассчитан на macOS (здесь: $(uname -s))"

# stdin — терминал, даже если нас запустили через curl | bash
if [[ ! -t 0 ]] && [[ -r /dev/tty ]]; then exec < /dev/tty; fi

printf "\n%s   J.A.R.V.I.S.%s  %sустановщик · %s · канал %s%s\n\n" "$CB" "$C0" "$CD" "$REPO" "$CHANNEL" "$C0"

# Xcode Command Line Tools — нужны для git, swiftc (JARVIS.app) и вообще для всего
if ! xcode-select -p >/dev/null 2>&1; then
  say "Нужны Xcode Command Line Tools (git, компилятор). macOS сейчас предложит их установить."
  xcode-select --install 2>/dev/null || true
  echo "  Дождитесь окончания установки в появившемся окне, затем запустите эту же команду ещё раз."
  exit 0
fi

# ── источники ──
latest_tag() { curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1; }

say "Скачиваю JARVIS в $DIR"
if command -v git >/dev/null 2>&1; then
  if [[ -d "$DIR/.git" ]]; then
    git -C "$DIR" fetch -q --tags origin
  else
    rm -rf "$DIR"; git clone -q "https://github.com/$REPO.git" "$DIR"
  fi
  if [[ "$CHANNEL" == "stable" ]]; then
    TAG="$(latest_tag)"
    if [[ -n "$TAG" ]]; then git -C "$DIR" checkout -q "$TAG" 2>/dev/null || git -C "$DIR" checkout -q main; ok "версия $TAG"
    else git -C "$DIR" checkout -q main && git -C "$DIR" pull -q --ff-only origin main; ok "релизов пока нет — беру main"; fi
  else
    git -C "$DIR" checkout -q main && git -C "$DIR" pull -q --ff-only origin main; ok "канал main ($(git -C "$DIR" rev-parse --short HEAD))"
  fi
else
  TAG="$(latest_tag)"; [[ -n "$TAG" ]] || fail "не удалось узнать последний релиз $REPO (нет сети?)"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  curl -fsSL -o "$TMP/j.zip" "https://github.com/$REPO/releases/download/$TAG/jarvis-hermes-${TAG#v}.zip" || fail "не скачался релиз $TAG"
  rm -rf "$DIR"; mkdir -p "$DIR"; ditto -x -k "$TMP/j.zip" "$DIR"
  ok "версия $TAG (zip)"
fi

[[ -f "$DIR/install.sh" ]] || fail "в $DIR нет install.sh — скачивание не удалось"

# ── установка ──
say "Запускаю установщик (он спросит про модель, голос, автозапуск)"
echo
# ARGS намеренно разбивается по словам
# shellcheck disable=SC2086
cd "$DIR" && bash install.sh $ARGS
