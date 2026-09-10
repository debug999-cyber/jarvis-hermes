# Диагностика и решение проблем

Первое, что стоит запустить при любой проблеме:
```bash
jarvis doctor --fix      # 10 проверок (модель отвечает? плагины, API, gateway, HUD, launchd, права, хранилище, версия) + автопочинка
jarvis doctor --quick    # то же без ping модели и selftest прав (2 секунды)
jarvis status            # что запущено
hermes logs --follow     # живой лог агента (ошибки плагинов тоже здесь)
jarvis hud log           # лог HUD
```

## Что чинит `doctor --fix` сам
- нет `API_SERVER_ENABLED`/`API_SERVER_KEY` в `.env` → допишет (затем `jarvis gateway restart && jarvis hud restart`);
- плагины не включены → `hermes plugins enable …`; gateway/HUD не запущены → запустит; launchd-агенты выгружены → загрузит;
- папки `~/JARVIS` нет → создаст; нет прав macOS → откроет нужные панели (`selftest --fix`).
Что НЕ чинит: неверный ключ провайдера/пустой баланс (покажет ответ модели и предложит `hermes model`), права — их выдаёте вы.

## Установка

| Симптом | Причина / решение |
|---|---|
| `xcode-select: note: install requested` и скрипт вышел | Дождитесь установки Command Line Tools, запустите `./install.sh` снова |
| `brew: command not found` после установки Homebrew | Apple Silicon: `eval "$(/opt/homebrew/bin/brew shellenv)"`, затем повторить |
| `hermes: command not found` | `export PATH="$HOME/.local/bin:$PATH"`, откройте новый терминал; проверьте `~/.zshrc` |
| `uv pip install ".[voice,wake]"` падает на `pyaudio` | `brew install portaudio` и повторить: `cd ~/.hermes/hermes-agent && uv pip install -e ".[voice,wake]"` |
| Установщик просит `python3` | `brew install python@3.12` — нужен для HUD и merge_config (Hermes использует свой venv) |
| Плагины не видны в `/plugins` | `hermes config get plugins.enabled` должно содержать оба; `hermes plugins doctor ~/.hermes/plugins/jarvis-macos` |

## Голос

| Симптом | Решение |
|---|---|
| `/voice on` → «no audio device» / тишина | Микрофон разрешён для терминала? Системные настройки → Конфиденциальность → Микрофон. Перезапустите терминал |
| Wake word не срабатывает | `/wake status`; снизьте `wake_word.sensitivity` до 0.4; произносите «хей джАрвис» слитно; проверьте, что установлен `[wake]` extra |
| Ложные срабатывания | `wake_word.sensitivity: 0.7–0.8` |
| Распознаёт по-английски | `stt.local.language: ru` (или уберите — автоопределение) |
| Медленное распознавание | Модель `small` → `base`; на Intel Mac — `tiny`. Или облако: `stt.provider: groq` + `GROQ_API_KEY` (очень быстро) |
| Нет голоса в ответ | `tts.provider: edge` требует интернет. Офлайн: `tts.provider: piper` или `kittentts`. Проверить: `/tts тест` |
| Голос «робот»/не русский | `tts.edge.voice: ru-RU-DmitryNeural` (список: `edge-tts --list-voices`) |
| JARVIS перебивает сам себя | Используйте наушники или включите `voice.barge_in: false` |
| «Стоп» не останавливает | Стоп-фразы в `voice.stop_phrases` — добавьте свои |

## Управление Mac

| Симптом | Решение |
|---|---|
| `osascript is not allowed assistive access` / `-1719` | Универсальный доступ → добавьте терминал (и `python3`, если через launchd). После добавления перезапустите терминал |
| `Not authorized to send Apple events to Calendar` (`-1743`) | Конфиденциальность → Автоматизация → Terminal → включите Calendar/Reminders/Notes/Music/System Events. Если пункта нет — вызовите действие ещё раз, macOS покажет диалог |
| Скриншот чёрный/пустой | Запись экрана → добавьте терминал |
| `mac_bluetooth`: blueutil not found | `brew install blueutil` |
| `mac_camera_snap` не работает | `brew install imagesnap` + разрешение Камера |
| Яркость не меняется | `brew install brightness`; на внешних мониторах не поддерживается |
| Shortcuts не запускаются | Имя должно совпадать точно; проверьте `shortcuts list` |
| «Активное приложение» не определяется | Нужен Универсальный доступ для System Events |
| Действие выполнилось, но JARVIS говорит «не удалось» | Смотрите `hermes logs` — часто это таймаут osascript при первом запросе разрешения. Повторите |

## HUD

| Симптом | Решение |
|---|---|
| `jarvis hud` — порт занят | `lsof -i :8765`; `jarvis hud stop`; либо `JARVIS_HUD_PORT=8770 jarvis hud` (и `hud_url` в `plugins.entries.jarvis-core/jarvis-brain/jarvis-macos.settings`) |
| HUD пишет «Модель вернула пустой ответ» | Провайдер ответил без текста (часто у нестандартных прокси-моделей). `hermes model` → выберите рабочую модель; проверьте `hermes chat -q привет` в терминале |
| Открылся, но лента пустая | Плагин `jarvis-core` не загружен или `hud_url` не совпадает. Проверьте `/plugins` и `curl localhost:8765/api/status` |
| Чат в HUD: «Hermes API недоступен» | Запустите `jarvis gateway`; в `~/.hermes/.env` должен быть `API_SERVER_ENABLED=true` и `API_SERVER_KEY`; `curl localhost:8642/health` |
| 401 из API | Ключ в `.env` изменился после запуска HUD — `jarvis hud restart` |
| Видео на панели не играет | YouTube-embed требует интернет; локальные mp4 — только из белого списка папок (`~/Desktop`, `~/Downloads`, `~/Pictures`, `~/.hermes`) |
| Голос в HUD не работает | Web Speech API есть в Safari/Chrome, нет в Firefox; нужен `https` или `localhost` |

## База знаний (jarvis-brain)

| Симптом | Решение |
|---|---|
| `/brain stats` — «0 заметок», хотя просили запомнить | Проверьте `/plugins` (jarvis-brain включён?) и `hermes logs` на ошибки `jarvis-brain`. Модель должна вызывать `brain_remember`; страховка auto_capture ловит только фразы, начинающиеся с «запомни/запиши» |
| Контекст `[JARVIS memory]` не появляется | `min_score` слишком высок (снизьте до 0.8) или в запросе нет общих слов с заметками — используйте `brain_recall` явно |
| Ночная ревизия не идёт | Она выполняется процессом gateway (`hermes cron list`); gateway должен быть запущен ночью (launchd). Запустить вручную: `jarvis brain review` |
| «database is locked» | Одновременная запись из двух процессов при VACUUM — редко и само проходит (WAL). Если стабильно — `jarvis gateway stop`, повторить |
| Ревизия что-то сломала | `jarvis brain log 50` — увидеть изменения; откат из `brain.bak-*.db` (см. docs/BRAIN.md) |
| Нет FTS5 в python-sqlite | Плагин деградирует до LIKE-поиска (`stats.fts=false`); `brew install python@3.12` даёт SQLite с FTS5 |

## Gateway / мессенджеры

| Симптом | Решение |
|---|---|
| Бот не отвечает | `hermes gateway status`; `~/.hermes/logs/jarvis-gateway.log`; в `.env` — `TELEGRAM_BOT_TOKEN`; ваш user id в `allowed_users` (`hermes gateway setup`) |
| Голосовые в Telegram не распознаются | Нужен `ffmpeg` (`brew install ffmpeg`) и рабочий STT |
| BOOT.md не выполняется | Хук `~/.hermes/hooks/jarvis-boot` на месте? Логи: строки `jarvis-boot` в gateway.log |
| Cron не срабатывает | Cron исполняется процессом gateway — он должен быть запущен (launchd). `hermes cron list` |

## LLM

| Симптом | Решение |
|---|---|
| JARVIS повторяет одно и то же / говорит дважды | Ответ шёл на HUD двумя путями (плагин + прокси чата) и модель вызывала `mac_say` поверх TTS Hermes. С 1.6.1 дубли режутся на сервере, `mac_say` только по явной просьбе. Если повторы остались — это модель: `hermes model` → выберите другую |
| Не остановить речь | `jarvis hush` (или `Esc` в HUD, «Замолчать» в меню ◉, голосом «стоп»). Hermes TTS: барж-ин — просто начните говорить; `voice.stop_phrases` в config.yaml |
| «No API key» | `hermes model` или `hermes config set OPENROUTER_API_KEY …` |
| Модель не вызывает инструменты (только болтает) | Возьмите модель с хорошим tool-calling: Claude Sonnet, GPT-4.1, Gemini 2.5, Qwen3 ≥ 14b. Для Ollama проверьте, что модель поддерживает tools |
| Медленный первый ответ | Короче SOUL.md; меньше toolsets в `config.yaml`; провайдер с prompt caching (Anthropic) |
| Ответы на английском | В SOUL.md уже сказано отвечать на языке пользователя; добавьте `/personality` или явно «говори по-русски» — запомнится в памяти |

## Автозапуск (launchd)

```bash
launchctl list | grep ai.jarvis                      # статус
jarvis hud restart   /   jarvis gateway restart      # перезапуск (сами понимают, что сервис под launchd)
jarvis hud stop      /   jarvis gateway stop         # остановить до следующего входа в систему (агент выгружается, KeepAlive не поднимет его снова)
launchctl load -w ~/Library/LaunchAgents/ai.jarvis.hud.plist   # вернуть агент вручную раньше
tail -f "$HERMES_HOME"/logs/jarvis-hud.log             # (или ~/.hermes/logs/…)
```
Если после обновления macOS разрешения «слетели» — удалите терминал из списков и добавьте заново.

## Полный сброс JARVIS (без потери памяти Hermes)

```bash
./install.sh --yes --no-brew-tools --no-launchd    # переустановит плагины/скиллы/HUD/конфиг
rm ~/.hermes/plugin-data/jarvis-core/state.json    # сбросить режим и таймеры (или $HERMES_HOME/plugin-data/…)
```

## Куда смотреть в логах

| Лог | Что там |
|---|---|
| `~/.hermes/logs/agent.log` | ход агента, tool calls, ошибки плагинов (`plugins.jarvis-*`) |
| `~/.hermes/logs/jarvis-hud.log` | HTTP-запросы HUD, прокси к API |
| `~/.hermes/logs/jarvis-gateway.log` | gateway, cron, хуки |
| `hermes doctor` | окружение, ключи, зависимости |
