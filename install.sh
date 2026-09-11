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
#  Флаги:  --no-launchd  --no-voice  --no-brew-tools  --no-cron  --no-app  --yes  --hermes-home DIR
#  Переменные (для updater): JARVIS_QUIET=1  JARVIS_COMMIT=sha  JARVIS_CHANNEL=stable|main  JARVIS_AUTO_UPDATE=off|check|auto
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ─── параметры ────────────────────────────────────────────────────────────
JARVIS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_REPO="$HERMES_HOME/hermes-agent"
JARVIS_HOME="$HERMES_HOME/jarvis"          # копия HUD и служебных файлов
BIN_DIR="$HOME/.local/bin"
INSTALL_LAUNCHD=1; INSTALL_VOICE=1; INSTALL_BREW_TOOLS=1; INSTALL_CRON=1; INSTALL_APP=1; ASSUME_YES=0
JARVIS_VERSION="$(cat "$JARVIS_SRC/VERSION" 2>/dev/null || echo 0.0.0)"
JARVIS_REPO="${JARVIS_REPO:-debug999-cyber/jarvis-hermes}"

for arg in "$@"; do
  case "$arg" in
    --no-launchd)     INSTALL_LAUNCHD=0 ;;
    --no-voice)       INSTALL_VOICE=0 ;;
    --no-brew-tools)  INSTALL_BREW_TOOLS=0 ;;
    --no-cron)        INSTALL_CRON=0 ;;
    --no-app)         INSTALL_APP=0 ;;
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

if ! command -v brew >/dev/null 2>&1 && [[ $INSTALL_BREW_TOOLS -eq 0 ]]; then
  warn "Homebrew не найден — системные утилиты (portaudio/ffmpeg/blueutil) пропущены (--no-brew-tools)"
elif ! command -v brew >/dev/null 2>&1; then
  if ask "Homebrew не найден. Установить?"; then
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    [[ -x /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
    [[ -x /usr/local/bin/brew ]]    && eval "$(/usr/local/bin/brew shellenv)"
  else
    die "Без Homebrew не поставить portaudio/ffmpeg. Установите вручную: https://brew.sh"
  fi
fi
command -v brew >/dev/null 2>&1 && ok "Homebrew $(brew --version 2>/dev/null | head -1 | awk '{print $2}')"

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
  # Нативные CLI с GitHub вместо AppleScript (принцип «сначала GitHub»): календарь, напоминания, окна/скриншоты.
  # Без них JARVIS работает по-старому через AppleScript; с ними — быстрее и без диалогов «Автоматизация».
  NATIVE=("BRO3886/tap/ical" "steipete/tap/remindctl" "steipete/tap/peekaboo")
  for f in "${NATIVE[@]}"; do
    name="${f##*/}"
    if command -v "$name" >/dev/null 2>&1 || brew list --formula "$name" >/dev/null 2>&1; then ok "$name"; else
      printf "  ${CD}… устанавливаю %s (GitHub: %s)${C0}\n" "$name" "$f"
      brew install "$f" >/dev/null 2>&1 && ok "$name" || warn "не удалось установить $name (не критично; peekaboo требует macOS 15+)"
    fi
  done
fi

# ─── 2. Hermes Agent ──────────────────────────────────────────────────────
step "Hermes Agent"
if command -v hermes >/dev/null 2>&1 || [[ -x "$BIN_DIR/hermes" ]]; then
  ok "уже установлен: $(command -v hermes || echo "$BIN_DIR/hermes")"
else
  echo "  Устанавливаю Hermes Agent официальным скриптом (uv + Python 3.11 + репозиторий)…"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
fi
export PATH="$BIN_DIR:$PATH"
command -v hermes >/dev/null 2>&1 || die "Команда hermes недоступна. Откройте новый терминал и запустите install.sh снова."
[[ -d "$HERMES_REPO" ]] || die "Не найден репозиторий Hermes в $HERMES_REPO"
ok "hermes $(hermes --version 2>/dev/null | head -1 || echo '')"
# Computer Use (cua-driver, trycua/cua) — готовое фоновое управление окнами вместо нашего AppleScript-ввода
if hermes computer-use status >/dev/null 2>&1; then ok "computer-use driver"; else
  printf "  ${CD}… устанавливаю cua-driver (hermes computer-use install)${C0}\n"
  hermes computer-use install >/dev/null 2>&1 && ok "computer-use driver" || warn "cua-driver не поставился (не критично; повторите: hermes computer-use install)"
fi

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
  [[ -f "$JARVIS_SRC/plugins/$plug/plugin.yaml" ]] || die "в архиве нет плагина $plug — скачайте проект заново"
done
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
cp "$JARVIS_SRC/scripts/update.py" "$JARVIS_HOME/"
cp "$JARVIS_SRC/scripts/doctor.py" "$JARVIS_HOME/"
cp "$JARVIS_SRC/scripts/make_shortcuts.py" "$JARVIS_HOME/"
cp -R "$JARVIS_SRC/app" "$JARVIS_HOME/app.src"   # исходник приложения строки меню (пересобирается при обновлении)
cp "$JARVIS_SRC/VERSION" "$JARVIS_HOME/VERSION"
[[ "$HERMES_HOME" == "$HOME/.hermes" ]] && rm -f "$HOME/.jarvis-home" || echo "$HERMES_HOME" > "$HOME/.jarvis-home"
cp "$JARVIS_SRC/config/HEARTBEAT.md" "$JARVIS_HOME/" 2>/dev/null || true
ok "HUD → $JARVIS_HOME/hud"

# ─── 6. конфигурация ──────────────────────────────────────────────────────
step "Конфигурация $HERMES_HOME/config.yaml"
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
  KEY="$(openssl rand -hex 24 2>/dev/null || "$VENV_PY" -c 'import secrets;print(secrets.token_hex(24))')"
  echo "API_SERVER_KEY=$KEY" >> "$ENV_FILE"; ok "сгенерирован API_SERVER_KEY"
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

# запись «что установлено» — по ней работает автообновление (jarvis update); настройки канала/режима сохраняются
"$VENV_PY" - "$JARVIS_HOME/install.json" "$JARVIS_VERSION" "${JARVIS_COMMIT:-}" "$JARVIS_REPO" "${JARVIS_CHANNEL:-}" "${JARVIS_AUTO_UPDATE:-}" <<'PY'
import json, sys, datetime, pathlib
p, ver, commit, repo, channel, auto = pathlib.Path(sys.argv[1]), *sys.argv[2:7]
old = {}
try: old = json.loads(p.read_text())
except Exception: pass
data = {**old, "version": ver, "commit": commit or old.get("commit", ""), "repo": repo, "author": "ERTGYKI",
        "channel": channel or old.get("channel", "stable"), "auto_update": auto or old.get("auto_update", "check"),
        "installed_at": datetime.datetime.now().replace(microsecond=0).isoformat()}
p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
ok "install.json: версия $JARVIS_VERSION"

if [[ $INSTALL_LAUNCHD -eq 1 ]] && ask "Настроить автозапуск HUD и gateway при входе в систему (launchd)?"; then
  LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA" "$HERMES_HOME/logs"
  for plist in ai.jarvis.hud ai.jarvis.gateway ai.jarvis.updater; do
    sed -e "s#__HERMES_HOME__#$HERMES_HOME#g" -e "s#__PYTHON__#$VENV_PY#g" -e "s#__HERMES_BIN__#$(command -v hermes)#g" \
        -e "s#__HOME__#$HOME#g" "$JARVIS_SRC/config/launchd/$plist.plist" > "$LA/$plist.plist"
    launchctl unload "$LA/$plist.plist" >/dev/null 2>&1 || true
    launchctl load -w "$LA/$plist.plist" && ok "launchd: $plist" || warn "не удалось загрузить $plist"
  done
fi

# ─── 8b. JARVIS.app — приложение строки меню ─────────────────────────────
if [[ $INSTALL_APP -eq 1 ]]; then
  step "JARVIS.app (строка меню: статус, HUD, голос, обновления)"
  APP_PATH=""
  if command -v swiftc >/dev/null 2>&1; then
    APP_PATH="$(bash "$JARVIS_SRC/app/build.sh" "$HOME/Applications/JARVIS.app" 2>&1 | tail -1)" || APP_PATH=""
  fi
  if [[ ! -d "$APP_PATH" && -f "$JARVIS_SRC/app/prebuilt/JARVIS.app.zip" ]]; then
    # готовая сборка из релиза (для тех, у кого нет Xcode CLT); ad-hoc подпись переставляем локально
    mkdir -p "$HOME/Applications"; rm -rf "$HOME/Applications/JARVIS.app"
    if ditto -x -k "$JARVIS_SRC/app/prebuilt/JARVIS.app.zip" "$HOME/Applications" 2>/dev/null && [[ -d "$HOME/Applications/JARVIS.app" ]]; then
      xattr -dr com.apple.quarantine "$HOME/Applications/JARVIS.app" 2>/dev/null || true
      codesign --force --deep --sign - "$HOME/Applications/JARVIS.app" >/dev/null 2>&1 || true
      APP_PATH="$HOME/Applications/JARVIS.app"
    fi
  fi
  if true; then
    if [[ -d "$APP_PATH" ]]; then
      ok "$APP_PATH"
      if [[ $INSTALL_LAUNCHD -eq 1 ]]; then
        LA="$HOME/Library/LaunchAgents"; mkdir -p "$LA"
        sed -e "s#__HOME__#$HOME#g" "$JARVIS_SRC/config/launchd/ai.jarvis.app.plist" > "$LA/ai.jarvis.app.plist"
        launchctl unload "$LA/ai.jarvis.app.plist" >/dev/null 2>&1 || true
        launchctl load -w "$LA/ai.jarvis.app.plist" >/dev/null 2>&1 && ok "JARVIS.app будет запускаться при входе"
      fi
      [[ "${JARVIS_QUIET:-0}" == "1" ]] || open -a "$APP_PATH" 2>/dev/null || true
    else
      warn "JARVIS.app не установлено (нет swiftc и нет готовой сборки app/prebuilt/). Всё работает через команду jarvis; приложение: xcode-select --install && jarvis app build"
    fi
  fi
fi

# ─── 9. cron-задачи JARVIS ────────────────────────────────────────────────
step "Фоновые задачи (утренний брифинг, контроль батареи)"
if [[ $INSTALL_CRON -eq 1 ]] && ask "Создать cron-задачи JARVIS (брифинг 08:00, вечерний итог 21:00, ночная ревизия 03:30, heartbeat)?"; then
  bash "$JARVIS_SRC/scripts/setup_cron.sh" || warn "cron не настроен — можно позже: bash ~/.hermes/jarvis/setup_cron.sh"
fi

if [[ "${JARVIS_QUIET:-0}" == "1" ]]; then echo "JARVIS $JARVIS_VERSION установлен (тихий режим updater)"; exit 0; fi

# ─── 10. модель ───────────────────────────────────────────────────────────
step "Провайдер LLM"
if hermes config get model >/dev/null 2>&1 && [[ -n "$(hermes config get model 2>/dev/null | tr -d '[:space:]')" ]]; then
  ok "модель: $(hermes config get model 2>/dev/null)"
  # Настроенная ≠ рабочая: короткий ping. Пустой ответ/ошибка провайдера — самая частая причина «ничего не работает».
  printf "  ${CD}… проверяю, что модель отвечает${C0}\n"
  PING="$(perl -e 'alarm 90; exec @ARGV' hermes chat -q 'Ответь одним словом: ok' 2>&1 | tail -c 400 || true)"  # perl alarm: в macOS нет timeout
  if [[ -z "$PING" ]] || echo "$PING" | grep -qiE "error code|http [45][0-9][0-9]|traceback|\b(401|403|405|429)\b"; then
    warn "модель настроена, но НЕ отвечает: ${PING:-пустой ответ}"
    if [[ $ASSUME_YES -eq 0 ]] && ask "Открыть мастер выбора модели сейчас (рекомендую OpenRouter или Ollama)?"; then hermes model || true; fi
  else ok "модель отвечает"; fi
else
  warn "Модель не настроена. Сейчас откроется мастер — выберите провайдера (OpenRouter / Anthropic / OpenAI / Nous Portal / Ollama)."
  [[ $ASSUME_YES -eq 1 ]] || hermes model || true
fi

# ─── 11. доктор ───────────────────────────────────────────────────────────
step "Диагностика"
if ! hermes plugins list 2>/dev/null | grep -qi jarvis-core; then
  hermes plugins enable jarvis-core jarvis-macos jarvis-brain >/dev/null 2>&1 && ok "плагины включены" \
    || warn "плагины не отображаются — выполните: hermes plugins enable jarvis-core jarvis-macos jarvis-brain"
fi
hermes plugins list 2>/dev/null | grep -i jarvis || true
"$VENV_PY" "$JARVIS_HOME/doctor.py" --quick --fix 2>/dev/null || true

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
   ${CB}jarvis doctor --fix${C0} — если что-то не работает: проверит и починит
   ${CB}jarvis vault open${C0} — папка ~/JARVIS: кладите файлы и проекты, JARVIS их читает
   ${CD}J.A.R.V.I.S. by ERTGYKI · github.com/debug999-cyber${C0}

 Документация: $JARVIS_SRC/docs/  (README.md → начните с него)
${CG}══════════════════════════════════════════════════════════════════════${C0}
EOF
