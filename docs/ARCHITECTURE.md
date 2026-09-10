# Архитектура

## Общая картина

```
┌─────────────────────────── ВВОД ────────────────────────────┐
│  🎙 микрофон (wake word / Ctrl+B)   ⌨ TUI/CLI   🌐 HUD-чат   │
│  📱 Telegram · Discord VC · WhatsApp · Slack · iMessage       │
└───────────────┬───────────────────────────┬──────────────────┘
                │                           │ HTTP :8642 (OpenAI-compatible)
                ▼                           ▼
┌──────────────────────── HERMES AGENT (~/.hermes) ───────────────────────┐
│  AIAgent loop ── prompt_builder ── provider (LLM) ── tool dispatch      │
│     ▲ SOUL.md (личность JARVIS)      ▲ memory / skills / sessions      │
│     │                                │                                 │
│  ┌──┴──────────── PluginManager ─────┴───────────────────────────────┐  │
│  │  jarvis-core   │  jarvis-brain        │ jarvis-macos    │ Hermes  │  │
│  │  context hook, │  SQLite+FTS5 brain,  │ 29 mac_* tools  │ tools:  │  │
│  │  HUD events,   │  brain_* tools,      │ (osascript /    │ terminal│  │
│  │  timers, modes,│  memory context hook,│  shell)         │ web,    │  │
│  │  weather,      │  turn journal,       │ /screen /vol…   │ browser,│  │
│  │  watchdog      │  nightly review      │                 │ memory… │  │
│  └─────────┬───────────────┴────────────────────────┴────────────────┘  │
│            │ HTTP POST /api/event (fire-and-forget)                     │
│  gateway ──┼── hooks/jarvis-boot (BOOT.md, зеркало активности)         │
│  cron ─────┘   (брифинг, батарея, память)                              │
└────────────┼────────────────────────────────────────────────────────────┘
             ▼
┌──────────── JARVIS HUD (hud/server.py, :8765, stdlib) ─────────────────┐
│  EventBus → SSE /events → браузер (index.html)                          │
│  /api/chat → прокси к Hermes /v1/chat/completions (stream)             │
│  /file → безопасная выдача скриншотов/картинок                          │
└─────────────────────────────────────────────────────────────────────────┘
             ▼
┌──────────────────────────── ВЫВОД ──────────────────────────────────────┐
│  🔊 TTS (Edge/ElevenLabs/OpenAI/Piper)   🖥 HUD-панели   🔔 уведомления │
│  📱 ответ в мессенджер                    🖱 действия в macOS            │
└─────────────────────────────────────────────────────────────────────────┘
```

## Почему именно так

**Hermes — не «библиотека», а полноценная агентская платформа.** Вместо того чтобы писать свой цикл
LLM ↔ инструменты, память, wake word, STT/TTS, планировщик и 20 адаптеров мессенджеров (как делают
типичные YouTube-«Джарвисы» на 500 строк), мы используем всё это из коробки и добавляем только то,
чего в Hermes нет:

1. **Личность** — `SOUL.md` занимает слот #1 системного промпта (официальный механизм Hermes).
2. **Руки на Mac** — плагин с инструментами; Hermes сам решает, когда их вызвать, по `description` схем.
3. **Ситуационный контекст** — хук `pre_llm_call` подмешивает время, батарею, активное приложение, режим и таймеры в каждый ход, не трогая системный промпт (кэш промпта не инвалидируется).
4. **Визуализация** — хуки `pre/post_tool_call` и `on_stream_delta` шлют события на HUD.
5. **Автономность** — cron Hermes + gateway-хук `gateway:startup` → BOOT.md.

Ничего не форкается: Hermes обновляется своим `hermes update`, наши плагины — копированием.

## Компоненты

### `plugins/jarvis-macos`
| Файл | Роль |
|---|---|
| `plugin.yaml` | манифест v2: имя, список инструментов, `config_schema` |
| `schemas.py` | JSON-схемы для LLM — единственное место, где описано «когда и как вызывать» |
| `tools.py` | обработчики `handler(args, **kwargs) -> JSON-str`; декоратор `@guarded` гарантирует «никогда не бросает, всегда JSON» |
| `mac.py` | низкий уровень: `osascript()`, `run()`, экранирование, алиасы папок, перевод типичных ошибок macOS в понятные подсказки |
| `__init__.py` | `register(ctx)`: инструменты → toolset `jarvis_macos`, slash-команды |

Каналы к системе: **AppleScript/JXA** (`osascript`) для приложений и System Events, **shell-утилиты**
(`open`, `pmset`, `screencapture`, `mdfind`, `networksetup`, `shortcuts`, `pbcopy`), опциональные
brew-утилиты (`blueutil`, `imagesnap`, `brightness`).

Опасные действия (выключение, перезагрузка, выход) требуют `confirmed=true`, который модель
может передать только после явного подтверждения пользователя (это закреплено и в схеме, и в SOUL.md,
и в навыке `mac-control`). Произвольный AppleScript выключен по умолчанию.

### `plugins/jarvis-core`
| Файл | Роль |
|---|---|
| `__init__.py` | хуки, инструменты `jarvis_hud/timer/mode/weather`, команды `/brief /focus /timer`, регистрация бандл-скиллов |
| `state.py` | JSON-состояние (режим, таймеры) в `~/.hermes/plugin-data/jarvis-core/`, потокобезопасно, переживает рестарты |
| `hud_client.py` | неблокирующая очередь → HTTP POST на HUD; при недоступности HUD молчит 10 с |
| `skills/*/SKILL.md` | процедуры для агента: брифинг, таблица выбора mac-инструментов |

Таймеры: фоновый поток спит до срока, затем уведомление macOS + звук + событие HUD. После рестарта
живые таймеры восстанавливаются из state.json.

### `plugins/jarvis-brain`
| Файл | Роль |
|---|---|
| `db.py` | класс `Brain`: SQLite (WAL) + FTS5, таблицы kinds/entities/notes/relations/turns/episodes/changelog/reviews; дедуп при записи, ранжирование, `auto_maintenance`, `review_plan`, `apply_ops` (транзакция), `export_markdown` |
| `__init__.py` | хуки `pre_llm_call` (релевантные знания → `[JARVIS memory]`), `post_llm_call` (журнал ходов + авто-захват «запомни…»), инструменты `brain_*`, команды `/remember /recall /brain` |
| `skills/brain-usage` | что и когда запоминать/искать/исправлять |
| `skills/brain-nightly-review` | процедура ночной ревизии для cron |

Подробно: [BRAIN.md](BRAIN.md).

### `hud/`
Сервер — 300 строк stdlib (`http.server`, `ThreadingHTTPServer`), чтобы запускаться любым `python3`
и не зависеть от venv Hermes. Интерфейс — один HTML-файл без сборки и внешних CDN (работает офлайн).

Поток данных HUD:
```
плагин → POST /api/event → EventBus.publish → очереди SSE-клиентов → браузер
браузер → POST /api/chat → Hermes /v1/chat/completions (stream:true) → SSE обратно + дубль в EventBus
```

### `hooks/jarvis-boot`
Gateway-хук (YAML + async `handle`). На `gateway:startup` запускает одноразового `AIAgent` с
инструкциями `BOOT.md` (паттерн из официальной документации Hermes) в фоновом потоке; на
`agent:start/end` зеркалирует активность gateway-сессий на HUD.

### `config/config.jarvis.yaml` + `scripts/merge_config.py`
Фрагмент конфига **вливается** в пользовательский `config.yaml`: словари рекурсивно, скаляры
пользователя не перезаписываются, списки `plugins.enabled` и `toolsets.*` объединяются.
Это позволяет переустанавливать JARVIS поверх любой существующей настройки Hermes.

## Поток одного голосового хода

```
1. openWakeWord слышит «hey jarvis» (локально, ONNX) → Hermes открывает микрофон
2. VAD: 2 с тишины → WAV → faster-whisper (small, ru) → текст
3. pre_llm_call (jarvis-core) → "[JARVIS context] Сейчас … Батарея 71% … Активное: Safari … Режим: focus"
   pre_llm_call (jarvis-brain) → "[JARVIS memory] - #14 [person] (Анна) …" (только если релевантно)
4. prompt = SOUL.md + tools + skills index + memory + context files + [контекст] + сообщение
5. LLM → tool_calls: mac_volume{set,30} → pre_tool_call → HUD "▶ mac_volume" → osascript → JSON → post_tool_call → HUD "✔"
6. LLM → финальный текст → on_stream_delta → HUD печатает по токенам
7. post_llm_call → HUD turn.end; brain пишет ход в журнал (сырьё для ночного дневника); Hermes TTS (Edge) → воспроизведение
8. wake word снова слушает
```

## Безопасность (кратко, подробнее в SECURITY.md)

- Всё выполняется локально от имени пользователя; HUD и API слушают только `127.0.0.1`.
- Hermes `approvals.mode: smart` — опасные shell-команды требуют подтверждения.
- `mac_power` необратимые действия — только с `confirmed=true`; `mac_applescript` — выключен.
- `/file` на HUD отдаёт файлы только из белого списка папок.
- API-ключ Hermes читается из `~/.hermes/.env`, в браузер не передаётся (прокси на сервере HUD).
