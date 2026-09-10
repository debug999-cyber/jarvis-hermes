# Установка J.A.R.V.I.S. на macOS

## 0. Что понадобится

| | Минимум | Рекомендуется |
|---|---|---|
| macOS | 13 Ventura | 14 Sonoma / 15 Sequoia |
| Mac | любой с 8 ГБ RAM | Apple Silicon, 16 ГБ (локальный Whisper `small` + Ollama) |
| Диск | 3 ГБ | 10 ГБ (если Ollama-модели) |
| Интернет | для установки | для облачных LLM |
| LLM | ключ OpenRouter / Anthropic / OpenAI / Gemini **или** Ollama | OpenRouter (доступ к 300+ моделям одним ключом) |

> Полностью офлайн-вариант: Ollama (`qwen3:8b` / `llama3.1`) + локальный Whisper + Piper TTS.
> Тогда единственное, что уходит в сеть, — погода (wttr.in) и то, что вы явно попросите найти.

## 1. Скачать проект

Самый простой способ — одна команда в Terminal (Программы → Утилиты → Terminal), она сделает шаги 1 и 2 сама:

```bash
curl -fsSL https://raw.githubusercontent.com/debug999-cyber/jarvis-hermes/main/get.sh | bash
```

Она скачает последний релиз в `~/Downloads/jarvis-hermes` и запустит установщик с вопросами. Если Xcode Command Line
Tools ещё не стоят, macOS сначала предложит их поставить — дождитесь и запустите команду ещё раз.

Вручную: [zip последнего релиза](https://github.com/debug999-cyber/jarvis-hermes/releases/latest) (внутри `app/prebuilt/JARVIS.app.zip` —
готовое приложение, компилятор не нужен) → распаковать → открыть терминал в папке. Или:
```bash
git clone https://github.com/debug999-cyber/jarvis-hermes.git
cd jarvis-hermes
```

## 2. Запустить установщик

```bash
./install.sh
```

Что произойдёт (каждый шаг печатается на экран):

1. **Проверка системы** — macOS, Xcode Command Line Tools (если нет — откроется окно установки; после неё запустите скрипт снова), Homebrew (предложит поставить).
2. **brew-пакеты** — `git portaudio ffmpeg opus jq espeak-ng blueutil imagesnap brightness`.
   Последние три — опциональные утилиты для Bluetooth, камеры и точной яркости.
3. **Hermes Agent** — официальный установщик Nous Research (`uv`, Python 3.11, репозиторий в `~/.hermes/hermes-agent`, команда `hermes`). Если Hermes уже стоит — шаг пропускается.
4. **Голос** — `pip install -e ".[voice,wake]"`, `faster-whisper`, `edge-tts`. Модель Whisper скачается при первом использовании (~460 МБ для `small`).
5. **Плагины** — копируются в `~/.hermes/plugins/jarvis-core`, `jarvis-macos`, `jarvis-brain`.
6. **Личность и навыки** — `~/.hermes/SOUL.md` (ваш прежний сохраняется в `.bak`), `~/.hermes/skills/jarvis/*`, `~/.hermes/hooks/jarvis-boot`, `~/.hermes/BOOT.md`, HUD в `~/.hermes/jarvis/hud`.
7. **Конфиг** — `config/config.jarvis.yaml` **вливается** в `~/.hermes/config.yaml`: ваши существующие значения не перезаписываются, списки плагинов/toolsets объединяются. Бэкап: `config.yaml.bak.jarvis`.
8. **API-сервер** — в `~/.hermes/.env` добавляются `API_SERVER_ENABLED=true` и случайный `API_SERVER_KEY` (нужны HUD).
9. **Команда `jarvis`** — `~/.local/bin/jarvis` (+ PATH в `.zshrc`).
10. **launchd** (спросит) — автозапуск HUD и gateway при входе в систему.
11. **cron** (спросит) — брифинг 08:00, вечерний итог 21:00, ночная ревизия базы знаний 03:30, чистка памяти по воскресеньям, heartbeat каждые 45 минут (`JARVIS_HEARTBEAT=0` — не создавать).
12. **JARVIS.app** — собирается автоматически (нужны Xcode CLT), появляется в строке меню и запускается при входе; `--no-app` — пропустить.
13. **Автообновление** — агент `ai.jarvis.updater` раз в день проверяет GitHub и уведомляет (режим `check`); `jarvis update --auto auto` — ставить самому.
14. После установки выполните `jarvis selftest` — таблица покажет, каким интеграциям не хватает прав (`--fix` откроет панели).
    (Контроль батареи — локальный watchdog, без cron и без LLM.)
12. **Модель** — если не настроена, откроется `hermes model`.
13. **hermes doctor** — диагностика.

Флаги: `--yes` (без вопросов), `--no-launchd`, `--no-voice`, `--no-brew-tools`, `--no-cron`, `--no-app`, `--hermes-home=DIR`.

Если Hermes у вас живёт не в `~/.hermes` (например, `~/Documents/hermes/.hermes`) — передайте `--hermes-home=…` или задайте `HERMES_HOME`: установщик запишет путь в `~/.jarvis-home`, и команда `jarvis`, HUD, updater и JARVIS.app будут использовать его автоматически.

## 3. Разрешения macOS (обязательно)

```bash
jarvis perms      # откроет нужные панели Системных настроек
```

В **Системные настройки → Конфиденциальность и безопасность** добавьте ваш терминал (Terminal.app / iTerm2 / Warp — тот, из которого запускаете `jarvis`):

| Раздел | Зачем |
|---|---|
| **Микрофон** | wake word, push-to-talk |
| **Универсальный доступ** | `mac_type` (набор текста, хоткеи), `mac_window`, блокировка экрана |
| **Запись экрана** | `mac_screenshot` → «что у меня на экране?» |
| **Автоматизация** | появится автоматически при первом обращении к Calendar / Reminders / Notes / Music / System Events — нажмите «Разрешить» |
| **Камера** | `mac_camera_snap` (опционально) |

Если запускаете через launchd (gateway в фоне), разрешения запросит процесс `python`/`hermes` — тоже подтвердите.

## 4. Выбор LLM-провайдера

```bash
hermes model              # интерактивный выбор
# или напрямую:
hermes config set OPENROUTER_API_KEY sk-or-...
hermes config set model anthropic/claude-sonnet-4
```

Рекомендации для голосового ассистента (важна скорость первого токена):

| Провайдер | Модель | Комментарий |
|---|---|---|
| OpenRouter | `anthropic/claude-sonnet-4`, `google/gemini-2.5-flash` | лучший баланс качество/скорость/tool-calling |
| Anthropic | `claude-sonnet-4` | отличное следование SOUL.md |
| OpenAI | `gpt-4.1-mini` | быстро и дёшево |
| Nous Portal | `hermes setup --portal` | одна подписка: модель + поиск + TTS + браузер |
| Ollama (офлайн) | `qwen3:8b`, `llama3.1:8b` | `hermes model` → Custom endpoint `http://localhost:11434/v1` |

Для cron-задач можно назначить более дешёвую модель: `hermes config set cron.model google/gemini-2.5-flash`.

## 5. Первый запуск

```bash
jarvis
```

В TUI:
```
/voice on          включить голос (микрофон + TTS)
/wake on           слушать «Hey Jarvis» в фоне
/brief             брифинг
```
Скажите: *«Hey Jarvis, открой Safari и сделай громкость тридцать»*.

HUD:
```bash
jarvis hud         # запустит сервер и откроет http://127.0.0.1:8765
```
HUD показывает живую активность агента (какие инструменты вызываются), стрим ответа, панели.
Чат в HUD работает через API Hermes — для него должен быть запущен `jarvis gateway` (или `jarvis up`).

## 6. Мессенджеры (опционально)

```bash
hermes gateway setup       # мастер: Telegram / Discord / WhatsApp / Slack / iMessage / Email
jarvis gateway             # запустить
```
Голосовые сообщения в Telegram распознаются автоматически; `/voice tts` — отвечать голосом.
Discord: бот может сидеть в голосовом канале (`/voice join`) — полноценный «Джарвис в комнате».

## 7. Голос: тонкая настройка

`~/.hermes/config.yaml` (или `hermes config set …`):

```yaml
stt:
  local: {model: small, language: ru}     # tiny|base|small|medium|large-v3
tts:
  provider: edge                          # edge | elevenlabs | openai | piper | kittentts
  edge: {voice: ru-RU-DmitryNeural}       # en-GB-RyanNeural — «классический» Jarvis
wake_word:
  sensitivity: 0.6                        # выше = меньше ложных срабатываний
  openwakeword: {model: hey_jarvis}
```

Премиум-голос (ElevenLabs): `hermes config set ELEVENLABS_API_KEY …` и `tts.provider: elevenlabs`.
Клонировать «тот самый» голос можно в ElevenLabs Voice Lab → подставить `voice_id`.

Список голосов Edge: `edge-tts --list-voices | grep ru-RU`.

## 8. Автозапуск и фон

| Компонент | Как | Логи |
|---|---|---|
| HUD | `launchctl load -w ~/Library/LaunchAgents/ai.jarvis.hud.plist` | `~/.hermes/logs/jarvis-hud.log` |
| gateway | `launchctl load -w ~/Library/LaunchAgents/ai.jarvis.gateway.plist` | `~/.hermes/logs/jarvis-gateway.log` |
| остановить | `launchctl unload …` | |

Wake word в фоне без открытого терминала: используйте **Hermes Desktop** (`hermes desktop`) — там есть «ухо» в композере, либо держите `jarvis` в отдельной вкладке tmux.

## 9. Обновление

```bash
jarvis update          # hermes update + переустановка плагинов JARVIS
```
или вручную: `hermes update && cd jarvis-hermes && git pull && ./install.sh --yes --no-launchd --no-brew-tools`.

## 10. Удаление

```bash
launchctl unload ~/Library/LaunchAgents/ai.jarvis.*.plist; rm ~/Library/LaunchAgents/ai.jarvis.*.plist
rm -rf ~/.hermes/plugins/jarvis-core ~/.hermes/plugins/jarvis-macos ~/.hermes/plugins/jarvis-brain ~/.hermes/jarvis ~/.hermes/hooks/jarvis-boot ~/.hermes/skills/jarvis ~/.hermes/skill-bundles/jarvis.yaml
# база знаний (сделайте копию, если жалко): ~/.hermes/plugin-data/jarvis-brain/
rm ~/.local/bin/jarvis
hermes plugins disable jarvis-core jarvis-macos jarvis-brain
# вернуть прежнюю личность:
mv ~/.hermes/SOUL.md.bak.* ~/.hermes/SOUL.md
# Hermes целиком: rm -rf ~/.hermes ~/.local/bin/hermes
```

## Linux (кратко)

Плагин `jarvis-macos` работает только на macOS (все инструменты вернут понятную ошибку), но
`jarvis-core`, HUD, навыки и голос — кроссплатформенны. Установите Hermes официальным скриптом,
скопируйте `plugins/jarvis-core`, `hud/`, `config/SOUL.md`, примените `scripts/merge_config.py`
и уберите `jarvis_macos` из `toolsets`.
