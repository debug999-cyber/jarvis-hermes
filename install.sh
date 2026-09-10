#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  J.A.R.V.I.S. on Hermes Agent — установщик для macOS
#
#  Что делает (идемпотентно — можно запускать повторно):
#   1. Проверяет macOS, Xcode CLT, Homebrew; ставит portaudio/ffmpeg/opus и утилиты.
#   2. Устанавливает Hermes Agent (официальный install.sh), если его ещё нет.
#   3. Ставит extras: voice, wake (openWakeWord), faster-whisper, edge-tts.
#   4. Копирует плагины jarvis-core / jarvis-macos в ~/.hermes/plugins.
#   5. Ставит SOUL.md (личность), навыки, хук boot, cron-задачи.
#   6. Аккуратно вливает config.jarvis.yaml в ~/.hermes/config.yaml.
#   7. Включает OpenAI-совместимый API (порт 8642) для HUD.
#   8. Устанавливает команду `jarvis` и (по желанию) автозапуск через launchd.
#   9. Запускает `hermes doctor` и печатает следующие шаги.
#
#  Флаги:  --no-launchd  --no-voice  --no-brew-tools  --yes  --hermes-home DIR
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ─── параметры ────────────────────────────────────────────────────────────
JARVIS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_REPO="$HERMES_HOME/hermes-agent"
JARVIS_HOME="$HERMES_HOME/jarvis"          # копия HUD и служебных файлов
BIN_DIR="$HOME/.local/bin"
INSTALL_LAUNCHD=1; INSTALL_VOICE=1; INSTALL_BREW_TOOLS=1; ASSUME_YES=0

for arg in "$@"; do
  case "$arg" in
    --no-launchd)     INSTALL_LAUNCHD=0 ;;
    --no-voice)       INSTALL_VOICE=0 ;;
    --no-brew-tools)  INSTALL_BREW_TOOLS=0 ;;
    --yes|-y)         ASSUME_YES=1 ;;
    --hermes-home=*)  HERMES_HOME="${arg#*=}"; HERMES_REPO="$HERMES_HOME/hermes-agent"; JARVIS_HOME="$HERMES_HOME/jarvis" ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
  esac
done

# ─── оформление ───────────────────────────────────────────────────────────
C0='\033[0m'; CB='\033[1;36m'; CG='\033[1;32m'; CY='\033[1;33m'; CR='\033[1;31m'; CD='\033[2m'
step(){ printf "\n${CB}▶ %s${C0}\n" "$*"; }
ok(){   printf "${CG}  ✔ %s${C0}\n" "$*"; }
warn(){ printf "${CY}  ⚠ %s${C0}\n" "$*"; }
die(){  printf "${CR}  ✖ %s${C0}\n" "$*"; exit 1; }
ask(){  # ask "вопрос" → 0=yes
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "  $1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[YyДд] ]]
}

cat <<'BANNER'

     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
        on Hermes Agent  ·  macOS installer
BANNER

# ─── 1. пререквизиты ──────────────────────────────────────────────────────
step "Проверка системы"
[[ "$(uname -s)" == "Darwin" ]] || die "Этот установщик только для macOS. Для Linux используйте docs/INSTALL.md → раздел Linux."
ARCH="$(uname -m)"; ok "macOS $(sw_vers -productVersion) · $ARCH"

if ! xcode-select -p >/dev/null 2>&1; then
  warn "Xcode Command Line Tools не найдены — запускаю установку (появится окно)."
  xcode-select --install || true
  die "Дождитесь установки Xcode CLT и запустите install.sh снова."
fi
ok "Xcode Command Line Tools"

if ! command -v brew >/dev/null 2>&1; then
  if ask "Homebrew не найден. Установить?"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -x /usr/local/bin/brew ]]    && eval "$(/usr/local/bin/brew shellenv)"
  else
    die "Без Homebrew не поставить portaudio/ffmpeg. Установите вручную: https://brew.sh"
  fi
fi
ok "Homebrew $(brew --version | head -1 | awk '{print $2}')"

if [[ $INSTALL_BREW_TOOLS -eq 1 ]]; then
  step "Системные зависимости (brew)"
  PKGS=(git portaudio ffmpeg opus jq)
  [[ $INSTALL_VOICE -eq 1 ]] && PKGS+=(espeak-ng)
  # опциональные утилиты для плагина jarvis-macos
  PKGS+=(blueutil imagesnap brightness)
  for p in "${PKGS[@]}"; do
    if brew list --formula "$p" >/dev/null 2>&1; then ok "$p"; else
      printf "  ${CD}… устанавливаю %s${C0}\n" "$p"; brew install "$p" >/dev/null 2>&1 && ok "$p" || warn "не удалось установить $p (не критично)"
    fi
  done
fi

# ─── 2. Hermes Agent ──────────────────────────────────────────────────────
step "Hermes Agent"
if command -v hermes >/dev/null 2>&1 || [[ -x "$BIN_DIR/hermes" ]]; then
  ok "уже установлен: $(command -v hermes || echo "$BIN_DIR/hermes")"
else
  echo "  Устанавливаю Hermes Agent официальным скриптом (uv + Python 3.11 + репозиторий)…"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-computer-use || \
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
fi
export PATH="$BIN_DIR:$PATH"
command -v hermes >/dev/null 2>&1 || die "Команда hermes недоступна. Откройте новый терминал и запустите install.sh снова."
[[ -d "$HERMES_REPO" ]] || die "Не найден репозиторий Hermes в $HERMES_REPO"
ok "hermes $(hermes --version 2>/dev/null | head -1 || echo '')"

# venv Hermes (нужен для pip-extras)
VENV_PY="$HERMES_REPO/venv/bin/python"
[[ -x "$VENV_PY" ]] || VENV_PY="$(command -v python3)"

# ─── 3. голосовые extras ──────────────────────────────────────────────────
if [[ $INSTALL_VOICE -eq 1 ]]; then
  step "Голос: faster-whisper (STT), Edge TTS, openWakeWord (wake word)"
  ( cd "$HERMES_REPO" && {
      if command -v uv >/dev/null 2>&1; then
        export VIRTUAL_ENV="$HERMES_REPO/venv"
        uv pip install -q -e ".[voice,wake]" 2>/dev/null || uv pip install -q -e ".[voice]" || true
        uv pip install -q faster-whisper edge-tts sounddevice numpy 2>/dev/null || true
      else
        "$VENV_PY" -m pip install -q -e ".[voice]" || true
        "$VENV_PY" -m pip install -q faster-whisper edge-tts sounddevice numpy || true
      fi
  } ) && ok "voice extras установлены" || warn "часть голосовых пакетов не установилась — см. docs/TROUBLESHOOTING.md"
fi

# ─── 4. плагины ───────────────────────────────────────────────────────────
step "Плагины JARVIS → $HERMES_HOME/plugins"
mkdir -p "$HERMES_HOME/plugins"
for plug in jarvis-core jarvis-macos jarvis-brain; do
  rm -rf "$HERMES_HOME/plugins/$plug"
  cp -R "$JARVIS_SRC/plugins/$plug" "$HERMES_HOME/plugins/$plug"
  ok "$plug"
done

# ─── 5. личность, навыки, хуки, HUD ───────────────────────────────────────
step "Личность (SOUL.md), навыки, хуки, HUD"
if [[ -f "$HERMES_HOME/SOUL.md" ]] && ! grep -q "J.A.R.V.I.S" "$HERMES_HOME/SOUL.md"; then
  cp "$HERMES_HOME/SOUL.md" "$HERMES_HOME/SOUL.md.bak.$(date +%s)"; warn "ваш прежний SOUL.md сохранён как SOUL.md.bak.*"
fi
cp "$JARVIS_SRC/config/SOUL.md" "$HERMES_HOME/SOUL.md"; ok "SOUL.md"

mkdir -p "$HERMES_HOME/skills/jarvis"
cp -R "$JARVIS_SRC/skills/." "$HERMES_HOME/skills/jarvis/"; ok "skills/jarvis/*"

mkdir -p "$HERMES_HOME/hooks"
rm -rf "$HERMES_HOME/hooks/jarvis-boot"; cp -R "$JARVIS_SRC/hooks/jarvis-boot" "$HERMES_HOME/hooks/"; ok "hooks/jarvis-boot"
[[ -f "$HERMES_HOME/BOOT.md" ]] || cp "$JARVIS_SRC/config/BOOT.md" "$HERMES_HOME/BOOT.md"

mkdir -p "$HERMES_HOME/skill-bundles"
cp "$JARVIS_SRC/skill-bundles/jarvis.yaml" "$HERMES_HOME/skill-bundles/jarvis.yaml"; ok "skill-bundles/jarvis.yaml  (/jarvis в чате)"
# (.env.example НЕ копируем как .env — пустые KEY= ломали бы детекцию ниже; он лежит рядом для справки)
mkdir -p "$JARVIS_HOME"; cp "$JARVIS_SRC/config/.env.example" "$JARVIS_HOME/env.example"

mkdir -p "$JARVIS_HOME"
rm -rf "$JARVIS_HOME/hud"; cp -R "$JARVIS_SRC/hud" "$JARVIS_HOME/hud"
cp "$JARVIS_SRC/config/config.jarvis.yaml" "$JARVIS_HOME/"
cp "$JARVIS_SRC/scripts/merge_config.py" "$JARVIS_HOME/"
cp "$JARVIS_SRC/scripts/setup_cron.sh" "$JARVIS_HOME/"
cp "$JARVIS_SRC/scripts/selftest.py" "$JARVIS_HOME/"
cp "$JARVIS_SRC/config/HEARTBEAT.md" "$JARVIS_HOME/" 2>/dev/null || true
ok "HUD → $JARVIS_HOME/hud"

# ─── 6. конфигурация ──────────────────────────────────────────────────────
step "Конфигурация ~/.hermes/config.yaml"
[[ -f "$HERMES_HOME/config.yaml" ]] || hermes config >/dev/null 2>&1 || true
[[ -f "$HERMES_HOME/config.yaml" ]] || echo "{}" > "$HERMES_HOME/config.yaml"
cp "$HERMES_HOME/config.yaml" "$HERMES_HOME/config.yaml.bak.jarvis"
"$VENV_PY" "$JARVIS_SRC/scripts/merge_config.py" "$JARVIS_SRC/config/config.jarvis.yaml" "$HERMES_HOME/config.yaml" \
  && ok "ключи JARVIS добавлены (бэкап: config.yaml.bak.jarvis)" || warn "merge не удался — примените config/config.jarvis.yaml вручную"

# ─── 7. API-сервер для HUD ────────────────────────────────────────────────
step "OpenAI-совместимый API Hermes (для HUD)"
ENV_FILE="$HERMES_HOME/.env"; touch "$ENV_FILE"
env_has(){ grep -Eq "^$1=[^[:space:]#]+" "$ENV_FILE"; }   # ключ есть И непустой
env_has API_SERVER_ENABLED || { sed -i '' '/^API_SERVER_ENABLED=/d' "$ENV_FILE"; echo "API_SERVER_ENABLED=true" >> "$ENV_FILE"; }
if ! env_has API_SERVER_KEY; then
  sed -i '' '/^API_SERVER_KEY=/d' "$ENV_FILE"
  echo "API_SERVER_KEY=$(openssl rand -hex 24)" >> "$ENV_FILE"; ok "сгенерирован API_SERVER_KEY"
else ok "API_SERVER_KEY уже задан"; fi
env_has API_SERVER_HOST || { sed -i '' '/^API_SERVER_HOST=/d' "$ENV_FILE"; echo "API_SERVER_HOST=127.0.0.1" >> "$ENV_FILE"; }
chmod 600 "$ENV_FILE"

# ─── 8. команда jarvis + launchd ──────────────────────────────────────────
step "Команда \`jarvis\`"
mkdir -p "$BIN_DIR"
sed -e "s#__HERMES_HOME__#$HERMES_HOME#g" -e "s#__PYTHON__#$VENV_PY#g" "$JARVIS_SRC/bin/jarvis" > "$BIN_DIR/jarvis"
chmod +x "$BIN_DIR/jarvis"; ok "$BIN_DIR/jarvis"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  [[ -f "$rc" ]] && ! grep -q '.local/bin' "$rc" && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$rc" || true
done

if [[ $INSTALL_LAUNCHD -eq 1 ]] && ask "Настроить автозапуск HUD и gateway при входе в систему (launchd)?"; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA" "$HERMES_HOME/logs"
  for plist in ai.jarvis.hud ai.jarvis.gateway; do
    sed -e "s#__HERMES_HOME__#$HERMES_HOME#g" -e "s#__PYTHON__#$VENV_PY#g" -e "s#__HERMES_BIN__#$(command -v hermes)#g" \
        -e "s#__HOME__#$HOME#g" "$JARVIS_SRC/config/launchd/$plist.plist" > "$LA/$plist.plist"
    launchctl unload "$LA/$plist.plist" >/dev/null 2>&1 || true
    launchctl load -w "$LA/$plist.plist" && ok "launchd: $plist" || warn "не удалось загрузить $plist"
  done
fi

# ─── 9. cron-задачи JARVIS ────────────────────────────────────────────────
step "Фоновые задачи (утренний брифинг, контроль батареи)"
if ask "Создать cron-задачи JARVIS (брифинг 08:00, вечерний итог 21:00, батарея каждые 30 мин)?"; then
  bash "$JARVIS_SRC/scripts/setup_cron.sh" || warn "cron не настроен — можно позже: bash ~/.hermes/jarvis/setup_cron.sh"
fi

# ─── 10. модель ───────────────────────────────────────────────────────────
step "Провайдер LLM"
if hermes config get model >/dev/null 2>&1 && [[ -n "$(hermes config get model 2>/dev/null | tr -d '[:space:]')" ]]; then
  ok "модель: $(hermes config get model 2>/dev/null)"
else
  warn "Модель не настроена. Сейчас откроется мастер — выберите провайдера (OpenRouter / Anthropic / OpenAI / Nous Portal / Ollama)."
  [[ $ASSUME_YES -eq 1 ]] || hermes model || true
fi

# ─── 11. доктор ───────────────────────────────────────────────────────────
step "Диагностика"
hermes doctor 2>/dev/null | tail -n 25 || true
hermes plugins list 2>/dev/null | grep -i jarvis || warn "плагины не отображаются — выполните: hermes plugins enable jarvis-core jarvis-macos jarvis-brain"

# ─── итог ─────────────────────────────────────────────────────────────────
cat <<EOF

${CG}══════════════════════════════════════════════════════════════════════${C0}
${CG} J.A.R.V.I.S. установлен.${C0}

 Разрешения macOS (один раз, иначе часть команд не сработает):
   Системные настройки → Конфиденциальность и безопасность →
     • Микрофон            → Terminal / iTerm
     • Универсальный доступ → Terminal / iTerm      (клавиши, окна)
     • Запись экрана        → Terminal / iTerm      (скриншоты)
     • Автоматизация        → разрешить Calendar, Reminders, Notes, Music, System Events

 Запуск:
   ${CB}jarvis${C0}            — голосовой режим в терминале (wake word «Hey Jarvis», Ctrl+B — говорить)
   ${CB}jarvis hud${C0}        — открыть голографический HUD в браузере (http://127.0.0.1:8765)
   ${CB}jarvis gateway${C0}    — Telegram/Discord/WhatsApp + API для HUD
   ${CB}jarvis status${C0}     — состояние всех компонентов
   ${CB}jarvis doctor${C0}     — диагностика

 Документация: $JARVIS_SRC/docs/  (README.md → начните с него)
${CG}══════════════════════════════════════════════════════════════════════${C0}
EOF
