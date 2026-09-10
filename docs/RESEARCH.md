# Исследование: откуда взяты идеи

Перед проектированием были изучены документация Hermes Agent и популярные open-source
«Джарвисы» (GitHub, YouTube). Ниже — что и почему заимствовано.

## Hermes Agent (NousResearch/hermes-agent) — основа

Что уже есть «из коробки» и что мы **не** переписывали:
- агентский цикл с tool calling, любые провайдеры LLM, prompt caching;
- **голосовой режим**: wake word (openWakeWord `hey_jarvis` ✔, Porcupine, sherpa open-vocabulary с русским), локальный faster-whisper, TTS Edge/ElevenLabs/OpenAI/Piper, барж-ин, стоп-фразы;
- память (MEMORY.md/USER.md, Honcho), самообучающиеся навыки (`skills/`), FTS-поиск сессий;
- 40+ инструментов: терминал, файлы, браузер (Playwright), поиск, vision, image gen, TTS, todo, делегирование субагентам;
- gateway: Telegram, Discord (+ голосовые каналы), WhatsApp, Slack, Signal, iMessage, Email, Matrix, Mattermost… + OpenAI-совместимый API-сервер;
- cron с доставкой в чат, `/heartbeat`, hooks (`gateway:startup` → BOOT.md), MCP-клиент, computer-use;
- готовые навыки для macOS: `apple-notes`, `apple-reminders`, `imessage`, `findmy`, `obsidian`;
- плагины: `plugin.yaml` + `register(ctx)`; хуки `pre_llm_call` (возвращает `{"context": ...}`), `pre/post_tool_call`, `on_stream_delta` и др.

Ключевые уроки из документации, применённые в коде:
- **SOUL.md — слот #1 системного промпта**, отдельный от `system_prompt` в конфиге → личность JARVIS не ломает механику агента.
- **Контекст хода через `pre_llm_call`**, а не через системный промпт → prompt cache остаётся валидным.
- **BOOT.md через gateway-хук** — официальный паттерн для «проснулся → проверил всё → молчи, если [SILENT]».
- **Slash-команды** — Hermes отдаёт «сырой» текст; мы парсим `/timer 10 чай` сами.
- **Плагиновые хуки никогда не должны падать** — обёрнуты в try/except с логированием.
- **Merge, а не overwrite** конфига пользователя (`hermes config` уважает `${ENV}`; списки объединяем).

## eadmin2/jarvis_ai (GitHub) — HUD и «клиент к Hermes»

Проект «HUD + push-to-talk поверх Hermes на Mac». Заимствовано:
- идея **HUD как отдельного лёгкого сервера**, который получает события от агента и стримит в браузер (SSE);
- разговор с Hermes через **OpenAI-совместимый API :8642** (`API_SERVER_KEY`), а не через внутренние классы;
- **launchd**-автозапуск и cinematic boot sequence;
- **live tool feed** — видеть, что агент делает прямо сейчас (мы получили это дешевле — через хуки плагина, а не парсинг логов).

Отличие: их HUD — отдельное приложение с собственным STT/TTS (faster-whisper + ElevenLabs). Мы делегируем голос Hermes (единый конвейер и в TUI, и в Telegram), а в HUD оставили Web Speech API как лёгкий запасной вариант.

## nixfred/MacOS_Mark-XXXV — управление Mac

Голосовой контроль macOS через AppleScript. Заимствовано:
- **osascript как универсальный канал** к System Events, приложениям, уведомлениям;
- перечень необходимых **разрешений** (Accessibility, Automation, Screen Recording) и понимание, что их нужно просить заранее → `jarvis perms` и таблица в INSTALL.md;
- паттерн «фраза → инструмент» превращён у нас в **JSON-схемы для LLM** (модель сама выбирает инструмент — не нужны регулярки).

## Классические «Jarvis на Python» (YouTube, десятки репозиториев)

Типичный набор: `speech_recognition` + `pyttsx3` + `if 'открой' in query:`. Что взяли:
- **фразы-намерения**, на которые пользователи реально рассчитывают (открой/закрой, громкость, музыка, «что на экране», «который час», таймеры, напоминания, «расскажи анекдот») → покрыты инструментами и SKILL.md `mac-control`;
- ожидание **утреннего брифинга** «погода + календарь + новости» → навык `briefing` + cron;
- **режим тишины / ночной режим**.
Что сознательно **не** взяли: свои циклы распознавания, `webbrowser.open` вместо нормальной автоматизации, хардкод ключевых слов.

## Видео «Self-Hosted JARVIS» (YouTube, dOeFE4fBrEg) и подобные

Идеи: локальная LLM через Ollama, wake word без облака, приватность как фича, домашняя автоматизация. Отражено в INSTALL.md (офлайн-профиль) и навыке `home-automation` (Home Assistant toolset Hermes + HomeKit через Shortcuts).

## OpenClaw / OpenInterpreter / Open-Assistant-подобные проекты

Взято понимание «компьютер как инструмент агента»: computer-use, browser automation, делегирование. Всё это есть в Hermes (`hermes computer-use install`, `browser` toolset, `delegate_task`), мы лишь включили нужные toolsets в конфиг и описали в SOUL.md, когда их применять.

## Раунд 3 — исследование памяти агентов и проактивности (сентябрь 2026)

### Фреймворки памяти: Mem0, Graphiti/Zep, Letta, Cognee, ReMe, Hindsight
Обзоры (evermind.ai, cognee.ai, vellum.ai) сходятся на одном наборе идей, часть которых мы уже реализовали, часть — взяли теперь:

| Идея | Откуда | Что сделано в JARVIS |
|---|---|---|
| Факт — интервал времени, а не строка; при смене «закрывается», а не удаляется | **Graphiti / Zep** (temporal knowledge graph, `valid_at/invalid_at`) | `valid_from/valid_until/superseded_by`, `supersede()`, `brain_history`, схема v2 с миграцией |
| Entity linking при записи без вызова модели | **Mem0** | `_auto_link()` — заметка сама цепляется к карточке (с русскими словоформами) |
| «Ментальные модели» — живые документы про сущность, обновляемые по мере накопления памяти | **Hindsight** (Vectorize, MIT, github.com/vectorize-io/hindsight) | `stale_summaries()` в ночном плане + операция `entity_summary` |
| Три операции: retain / recall / **reflect** — синтез ответа из памяти, а не список результатов | **Hindsight** | `brain_reflect` собирает заметки+карточки+эпизоды+историю, синтез делает основная модель |
| Раздельные «наблюдения» (inferred, evolving) и «факты» | Hindsight, Cognee «memify» | у нас это `kind=insight` с confidence 0.6 + ночная рефлексия (было с v1.2) |
| Проактивные «reach-outs» с дедупликацией | Vellum, OpenClaw | heartbeat + журнал предупреждений в базе |

Что **не** взяли и почему: векторные БД/эмбеддинги (Mem0, Cognee) — для личной базы в тысячи заметок FTS5 + карточки
покрывают 90 % запросов, а лишний сервис ломает принцип «скачал и запустил»; отдельный LLM-вызов на каждую запись
(Mem0 extraction) — дорого и медленно в голосовом диалоге, у нас извлечение делает та же модель прямо в ходе.

### OpenClaw — heartbeat
docs.openclaw.ai/gateway/heartbeat: агент периодически (по умолчанию 30 мин) читает `HEARTBEAT.md`, выполняет проверки
в **основной** сессии (помнит, о чём уже предупреждал) и отвечает `NO_REPLY`, если сказать нечего. Перенесено как навык
`jarvis/heartbeat` + файл `~/.hermes/jarvis/HEARTBEAT.md` + cron каждые 45 минут; дедупликация — через эпизоды базы знаний.
Сравнение с нашим Watchdog: Watchdog — 0 LLM-вызовов для «механических» проверок, heartbeat — для проверок, требующих
суждения («есть ли к этой встрече незакрытые обещания?»).

### Определение Focus macOS без публичного API
Gist drewkerr + обсуждение в Macjutsu/super #155: состояние Focus лежит в `~/Library/DoNotDisturb/DB/Assertions.json`
(`storeAssertionRecords[].assertionDetails.assertionDetailsModeIdentifier`), имя режима — в `ModeConfigurations.json`.
Использовано в `Watchdog.macos_focus()` и `mac_focus get`. Установка режима публичного API тоже не имеет — через Shortcuts.

### Хуки Hermes (user-guide/features/hooks.md)
Подтверждены и задействованы `post_tool_call` (статус/ошибка каждого инструмента), `pre_transcription` (возвращает
`{"prompt": …}` — подсказка Whisper), `transform_llm_output` (замена финального ответа; первый непустой выигрывает).

## Что уникального добавлено в этом проекте

1. Плагин `jarvis-macos` — 29 типизированных инструментов с единым форматом ошибок и подсказками по разрешениям.
2. Ситуационный контекст `[JARVIS context]` (время, батарея, активное окно, режим, таймеры) на каждом ходе.
3. Режимы `focus/night/presentation`, влияющие и на поведение модели, и на систему (через Shortcuts).
4. Таймеры/будильники, переживающие рестарт.
5. HUD без зависимостей с панелями `text/markdown/image/video/web/chart`, которые агент открывает сам через инструмент.
6. Один установщик, который вливается в существующую установку Hermes, не ломая её.
7. Самообслуживаемая база знаний (`jarvis-brain`): структурированная память с таксономией, карточками и связями,
   релевантный контекст на каждом ходе, дневник по дням и ночная реструктуризация агентом по явным правилам —
   идея заимствована из подходов MemGPT/Letta («агент управляет своей памятью») и Generative Agents («reflection»),
   реализована на SQLite+FTS5 без внешних сервисов.
8. Темпоральная память, журнал сбоев инструментов (агент ночью учится на собственных ошибках), словарь имён для STT,
   справка из базы перед встречей, синхронизация с Focus macOS, `jarvis selftest` — проверка всех интеграций без LLM.
