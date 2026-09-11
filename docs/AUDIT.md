# Аудит «своё → готовое»

Правило автора проекта (ERTGYKI): **сначала GitHub, потом свой код**. Этот документ — живой реестр того, что мы
написали сами, что из этого уже заменено готовыми решениями, что заменяется в ближайших релизах, а что остаётся своим
и почему. Обновляется при каждом релизе. Критерии оценки кандидата: звёзды и активность за 3 месяца, лицензия,
поддержка macOS, наличие CLI/JSON или MCP, совпадение с тем, что уже использует сам Hermes.

Легенда: ✅ заменено · 🔜 запланировано (номер релиза) · 🧩 остаётся своим (тонкий адаптер) · ❌ кандидатов нет

## Сводная таблица

| Наш компонент | Что делает | Лучшее готовое решение | Оценка кандидата | Статус |
|---|---|---|---|---|
| `mac_calendar` (AppleScript) | события/создание | **BRO3886/ical** — EventKit CLI, JSON, натуральные даты | 84★, MIT, релизы, brew tap, активен 07.2026 | ✅ 1.9.0 (AppleScript — запасной путь) |
| `mac_reminders` (AppleScript) | список/добавить/закрыть | **openclaw/remindctl** — тот же CLI, что в бандл-навыке Hermes `apple-reminders` | 362★, MIT, brew tap steipete, активен 09.2026 | ✅ 1.9.0 |
| `mac_window`, `mac_screenshot` | окна, скриншоты | **openclaw/Peekaboo** — Accessibility/ScreenCaptureKit CLI + MCP | 5.1k★, MIT, macOS 15+, активен 09.2026 | ✅ 1.9.0 (без peekaboo — `screencapture`/System Events) |
| `mac_notes` (AppleScript) | заметки | **antoniorodr/memo** — CLI из бандл-навыка Hermes `apple-notes` | 342★, Apache-2.0 | 🔜 1.9.1 — `memo` интерактивный (`$EDITOR`), для агента наш AppleScript пока удобнее; добавим `memo notes -s` для поиска |
| `mac_type`, `mac_click` (System Events) | ввод, клики | **Hermes `computer_use`** (cua-driver, фоновой режим, без кражи курсора) | официальная фича Hermes; MIT; 22k★ trycua/cua | ✅ 1.9.1 — toolset `computer_use` включён в конфиге, `install.sh` ставит cua-driver; `mac_type` остаётся для простых нажатий |
| `jarvis-brain` memory (`db.py`, FTS5, trust, feedback) | долговременная память | **Hermes memory providers**: Holographic (локальный SQLite FTS5 + trust/feedback — почти 1-в-1 наш дизайн), Mem0 OSS (65k★), Hindsight local (23k★) | официальные провайдеры Hermes, один активный | ✅ 1.10 — `memory.provider: holographic` включён по умолчанию; каждая `brain_remember` зеркалится в `fact_store` (`facts.py`), `brain_recall` ищет и там; старые заметки переносит `jarvis brain migrate` (идемпотентно, без потерь). У себя оставили карточки, дневник, историю, ревизию, vault |
| `brain_reflect` | «всё по вопросу одним вызовом» | **Hindsight** `hindsight_reflect` (наш был смоделирован с него) | 23k★, MIT, локальный режим требует LLM-ключ | 🧩 остаётся: наш `brain_reflect` собирает карточки+дневник+историю, чего у Holographic нет; Hindsight требует LLM-ключ и второй провайдер (Hermes допускает один) |
| `vault.py` (индекс файлов, коннекторы, авторезюме) | файлы/проекты пользователя | **OpenViking** (36k★, AGPL, `viking://`, tiered L0/L1/L2, ingest URL/doc) — самое близкое; обычные файлы — `@file`/`@url` Hermes + MCP filesystem | AGPL и отдельный сервер — тяжело для «скачал и запустил» | 🧩 остаётся: наш индекс — SQLite без демонов; добавим опцию `vault.backend: openviking` в 1.10 |
| `triggers.py` + `Watchdog` | события без LLM → HUD/LLM | Hermes `/heartbeat`, cron, gateway-хуки, **webhooks** (`platforms.webhook.routes`) | официальные механизмы | 🔜 1.9.1 — cron/heartbeat уже используются; локальные детекторы (батарея, диск, inbox) специфичны для Mac — остаются тонким адаптером |
| `hud/` (orb, SSE, dashboard) | визуальный HUD | **Hermes Dashboard plugins/themes** (`~/.hermes/dashboard-themes`, `manifest.json` + JS, FastAPI-роуты `/api/plugins/<name>`), пример `strike-freedom-cockpit` | официальный SDK; но требует `hermes dashboard` и свой веб-стек | 🧩 HUD остаётся (пользователь выбрал концепцию B); ✅ 1.10 — **вкладка «База знаний» в панели Hermes** (`plugins/jarvis-brain/dashboard/`: manifest + JS на Plugin SDK + FastAPI `/api/plugins/jarvis-brain/overview`) и **тема `jarvis`** (`dashboard-themes/jarvis.yaml`) |
| `hud/tts.py` | озвучка на HUD | Hermes `tts` (11 провайдеров, edge по умолчанию — тот же движок) | официально | ✅ уже edge-tts из venv Hermes; свой код — только кэш и `say`-fallback |
| `app/JarvisMenuBar.swift` | меню-бар | **Hermes Desktop** (`hermes desktop`, macOS, голос, Cmd+K, plugin SDK) | официальный клиент | ✅ 1.9.1 — пункты меню «Hermes Desktop» и «Панель Hermes»; своё приложение остаётся лаунчером HUD/гейтвея |
| `scripts/doctor.py` | диагностика | `hermes doctor` (ядро) + `hermes plugins doctor` | официально | 🧩 наш doctor проверяет то, чего Hermes не знает (launchd, HUD, права macOS, нативные CLI); вызывает `hermes doctor` внутри |
| `scripts/update.py` / `jarvis update` | автообновление JARVIS | **Hermes profile distributions** (`hermes profile install github.com/…`, `hermes profile update`) | официально; переносит SOUL/skills/cron/mcp/plugins, не трогает память | ✅ 1.10 — `distribution.yaml` в корне: `hermes profile install github.com/debug999-cyber/jarvis-hermes --alias` / `hermes profile update jarvis`; `jarvis update` остаётся для HUD, приложения, launchd и brew-инструментов (Hermes-профиль этого не переносит) |
| `install.sh` / `get.sh` | установка одной командой | официальный `install.sh` Hermes (уже используется) + brew | — | 🧩 остаётся: ставит Hermes официальным скриптом, дальше только наши файлы |
| `mac_contacts` | контакты | Peekaboo нет; MCP `iMCP` (menu-bar app), `apple-pim` (54★) | слабые кандидаты | ❌ пока своё (AppleScript к Contacts.app) |
| `mac_focus` | Focus macOS | `DoNotDisturb/Assertions.json` — тот же метод, что в openclaw/`brabble`-скриптах | нет отдельного CLI | ❌ своё (30 строк) |
| `jarvis_weather` | погода | Hermes бандл-навык `weather` (openclaw) / `maps` | официально | ✅ 1.9.1 — `jarvis_weather` удалён; навык `skills/weather` (приём из openclaw, wttr.in через web_extract) |
| `screen context` (`screencapture` + `vision_analyze`) | «что на экране» | Peekaboo `see --ocr`/`--annotate` | 5.1k★ | ✅ 1.9.1 — `mac_screenshot(ocr=true)` и контекст экрана отдают текст (Apple Vision через `peekaboo see --ocr`) без vision-модели |

## Что уже стоит на готовом (напоминание)

- Ядро агента, CLI, gateway, cron, память MEMORY.md/USER.md, voice/STT/TTS/wake word, vision — **Hermes Agent**.
- Заметки/напоминания/iMessage/FindMy — бандл-навыки Hermes `apple/*` (CLI `memo`, `remindctl`, `imsg`).
- Погода — навык Hermes; браузер — `browser` toolset; поиск — `web_search`.

## Как добавлять записи

Новая функция → сначала строка в этой таблице с кандидатом и оценкой, потом код. Если кандидат не найден —
запись «❌ кандидатов нет» с датой поиска, чтобы через полгода перепроверить.

_Последний полный проход: 2026-09-11 (релизы 1.9.0–1.10.0)._
