# Источники: что было прочитано и что из этого взято

Краткий, «ссылочный» список всех сайтов, статей и репозиториев, изученных при создании проекта.
Подробный разбор идей — в [RESEARCH.md](RESEARCH.md); здесь — только адрес → что взято.
Код ни из одного стороннего проекта не копировался: заимствованы идеи, форматы и приёмы.

## 0. Аудит «своё → готовое» (1.9.0)

Полная таблица — [AUDIT.md](AUDIT.md). Источники этого прохода:

| Источник | Что взято |
|---|---|
| https://github.com/BRO3886/ical | `ical list/add -o json` (EventKit) → бэкенд `mac_calendar`; формат полей `start_date/end_date/all_day/calendar` |
| https://github.com/openclaw/remindctl | `remindctl open/add/search/complete --json` → бэкенд `mac_reminders` (тот же CLI, что в навыке Hermes `apple-reminders`) |
| https://github.com/openclaw/Peekaboo | `peekaboo see --no-elements --mode …`, `window list/set-bounds/minimize --app … --json` → бэкенды `mac_screenshot`/`mac_window` |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers | 9 провайдеров памяти Hermes (holographic/mem0/hindsight/openviking…) — план замены части `jarvis-brain` (1.10) |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/extending-the-dashboard | Темы и плагины `hermes dashboard` — план темы JARVIS и вкладки Brain (1.10) |
| https://hermes-agent.nousresearch.com/docs/user-guide/profile-distributions | `distribution.yaml`, `hermes profile install/update` — план поставки JARVIS как дистрибутива профиля (1.10) |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/computer-use | `computer_use` toolset (cua-driver, фоновой режим) — замена `mac_type`/кликов (1.9.1) |
| https://hermes-agent.nousresearch.com/docs/reference/skills-catalog | Бандл-навыки `apple/*` (`memo`, `remindctl`, `imsg`) и `weather` |
| https://github.com/NousResearch/hermes-agent/tree/main/optional-mcps | Каталог MCP Hermes (`hermes mcp install …`) — проверено: Apple-приложений там нет, поэтому CLI-путь |
| https://github.com/steipete/macos-automator-mcp | Резерв: MCP с 200+ рецептами AppleScript/JXA, если понадобится произвольная автоматизация вместо `mac_applescript` |

## 1. Hermes Agent — ядро, на котором всё построено

| Источник | Что взято |
|---|---|
| https://github.com/NousResearch/hermes-agent | Сам агент (MIT). `install.sh` ставит его официальным скриптом; JARVIS — набор плагинов, навыков и конфигов поверх него |
| https://hermes-agent.nousresearch.com/docs/getting-started/installation | Пути `~/.hermes/…`, `~/.local/bin/hermes`, требования (Python 3.11, uv) |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins | Формат `plugin.yaml` + `register(ctx)`, `register_tool/hook/command`, хранилище `plugin_data_dir` |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks (+ raw `website/docs/user-guide/features/hooks.md`) | Таблица хуков и их сигнатуры: `pre_llm_call` → `{"context":…}` (контекст хода без порчи prompt-cache), `post_tool_call` (журнал сбоев), `pre_transcription` → `{"prompt":…}` (словарь для Whisper), `transform_llm_output` (полировка голосового ответа) |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/tts, …/voice | Ключи `voice/stt/tts/wake_word` в конфиге, `brew install portaudio ffmpeg…`, `/voice on` |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/skills | Frontmatter `SKILL.md`, `skill-bundles/*.yaml`, готовые навыки `apple-notes/reminders/imessage/findmy` |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server | OpenAI-совместимый сервер `:8642` (`API_SERVER_KEY`) — через него HUD говорит с агентом |
| https://hermes-agent.nousresearch.com/docs/user-guide/features/cron, …/gateway | `hermes cron create "every day at 08:00" …`, gateway-хуки (`gateway:startup` → BOOT.md), `/heartbeat` |
| https://hermes-agent.nousresearch.com/docs/user-guide/configuration | Приоритет CLI > config.yaml > .env, `${VAR}`, `approvals.mode: smart` → merge-стратегия `scripts/merge_config.py` |

## 2. Готовые «Джарвисы» и управление Mac

| Источник | Что взято |
|---|---|
| https://github.com/eadmin2/jarvis_ai | HUD как отдельный лёгкий сервер с SSE-лентой действий агента; разговор с Hermes через API :8642; launchd-автозапуск; cinematic boot |
| https://github.com/nixfred/MacOS_Mark-XXXV | osascript/System Events как универсальный канал к macOS; список разрешений (Accessibility, Automation, Screen Recording) → `jarvis perms`, `jarvis selftest` |
| YouTube «Self-Hosted JARVIS» https://www.youtube.com/watch?v=dOeFE4fBrEg | Локальная LLM (Ollama), wake word без облака, приватность как фича → офлайн-профиль в INSTALL.md |
| Десятки «Jarvis на Python» (speech_recognition + pyttsx3) на GitHub/YouTube | Список фраз-намерений, которых пользователи ожидают (открой/закрой, громкость, «что на экране», таймеры, брифинг) → покрыты инструментами `mac_*` и навыком `mac-control`. Их архитектура (регулярки, свой цикл распознавания) — сознательно **не** взята |
| https://github.com/legnoh/focus-cli, gist https://gist.github.com/drewkerr/0f2b61ce34e2b9e3ce0ec6a92ab05c18, https://github.com/Macjutsu/super/discussions/237 | Как прочитать активный режим Focus macOS без публичного API: `~/Library/DoNotDisturb/DB/Assertions.json` + `ModeConfigurations.json` (нужен Full Disk Access) → `Watchdog.macos_focus()`, `mac_focus` |

## 3. Память агентов (база знаний `jarvis-brain`)

| Источник | Что взято |
|---|---|
| https://github.com/vectorize-io/hindsight и https://hindsight.vectorize.io/blog/2026/03/04/mcp-agent-memory | Три операции retain/recall/**reflect**; «ментальные модели» — живые резюме сущностей; разделение фактов и выводимых наблюдений → `brain_reflect`, `stale_entity_summaries`/`entity_summary`, `kind=insight` |
| https://ai.miraheze.org/wiki/Hindsight | Схема recall: семантика + BM25 + граф + временной фильтр параллельно, затем rerank → у нас FTS5 + карточки + дневник, rerank отложен до schema v3 |
| Graphiti / Zep — https://github.com/getzep/graphiti, обзоры https://evermind.ai/blog и https://www.cognee.ai/blog (сравнения фреймворков памяти 2026) | Темпоральный граф: факт имеет `valid_at/invalid_at`, при изменении закрывается, а не удаляется → `valid_from/valid_until/superseded_by`, `supersede()`, `brain_history` |
| Mem0 — https://github.com/mem0ai/mem0 (через те же обзоры) | Entity linking при записи; дедупликация на входе → `_auto_link()`, Jaccard-дедуп 0.75 и `possible_conflicts`. Отдельный LLM-вызов на каждую запись — **не** взят (дорого в голосовом диалоге) |
| Letta / MemGPT — https://github.com/letta-ai/letta | «Агент сам управляет своей памятью» → ночная ревизия по явным правилам навыка, операции `merge/archive/rekind/kind_rename` |
| Generative Agents (Park et al., 2023) — https://arxiv.org/abs/2304.03442 | Reflection: из потока наблюдений периодически выводятся обобщения → шаг «рефлексия» в ночной ревизии (0–2 insight за ночь) |
| https://www.vellum.ai/blog (агентная память и проактивные reach-outs) | Проактивные обращения с дедупликацией → heartbeat + запись предупреждений в базу |
| ReMe (file-first markdown memory) — через обзор cognee.ai | Человекочитаемый снимок памяти → `BRAIN.md`/`PROFILE.md`, экспортируемые ночью |

## 4. Проактивность

| Источник | Что взято |
|---|---|
| https://docs.openclaw.ai/gateway/heartbeat (+ гайд на skywork.ai) | Периодический тик читает `HEARTBEAT.md`, отвечает `NO_REPLY`, если сказать нечего, помнит прошлые предупреждения → навык `jarvis/heartbeat`, файл `config/HEARTBEAT.md`, cron каждые 45 мин |

## 5. macOS-специфика и безопасность

| Источник | Что взято |
|---|---|
| Apple Support — «Controlling app access to files in macOS», документация `osascript`, `shortcuts run`, `pmset`, `mdfind`, `screencapture` | Поведение утилит, на которых построены `mac_*` инструменты; какие права нужны каждому |
| OWASP — CSRF Prevention Cheat Sheet | Same-origin проверка и JSON-only POST на HUD (без CORS `*`) |

## Что сознательно не взято и почему

- **Векторные БД / эмбеддинги** (Mem0, Cognee, Hindsight): для личной базы в тысячи заметок FTS5 с карточками закрывает подавляющее большинство запросов, а лишний сервис ломает принцип «скачал и запустил». Место под это оставлено (миграции `Brain._migrate`).
- **Свой агентский цикл / STT / TTS**: всё это есть в Hermes и работает одинаково в терминале, Telegram и Discord.
- **Регулярки «фраза → действие»** из классических Jarvis: выбор инструмента делает модель по JSON-схемам.
