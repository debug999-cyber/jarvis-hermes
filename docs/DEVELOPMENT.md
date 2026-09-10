# Разработка и расширение

## Запуск тестов

```bash
cd jarvis-hermes
python3 -m pytest tests -q          # работает на macOS и Linux (mac-специфичные тесты скипаются)
```

Тесты загружают плагины напрямую через `importlib` с фейковым `ctx` (`tests/conftest.py`),
поэтому Hermes для них не нужен. Состояние пишется во временную папку (`JARVIS_STATE_DIR`).

Проверка манифестов в Hermes: `hermes plugins doctor ~/.hermes/plugins/jarvis-macos --ci`.

## Быстрый цикл разработки

```bash
# 1. правим файлы в репозитории
# 2. синхронизируем в Hermes (без переустановки всего):
rsync -a plugins/ ~/.hermes/plugins/ && rsync -a hud/ ~/.hermes/jarvis/hud/
# 3. перезапускаем
jarvis hud restart; jarvis          # /plugins — убедиться, что загрузились
hermes logs --follow                # ошибки плагинов пишутся сюда
```

## Добавить новый инструмент в `jarvis-macos`

1. **Схема** в `schemas.py` — это то, что видит модель. Описывайте *когда* использовать и *что вернётся*:
```python
"mac_say": {
    "name": "mac_say",
    "description": "Произнести текст системным голосом macOS (say). Используй, когда TTS Hermes выключен.",
    "parameters": {"type": "object",
        "properties": {"text": {"type": "string"}, "voice": {"type": "string", "default": "Milena"}},
        "required": ["text"]},
},
```
2. **Обработчик** в `tools.py` — всегда через `@guarded`, всегда возвращает dict:
```python
@guarded
def mac_say(args, **kw):
    text = args["text"]
    voice = args.get("voice", "Milena")
    mac.run(["say", "-v", voice, text], timeout=60)
    return {"ok": True, "spoken": text[:80]}
```
3. Добавьте имя в `TOOLS` (`tools.py`) и в `tools:` списка `plugin.yaml`.
4. Тест в `tests/test_plugins.py` (замокайте `mac.run`).
5. Упомяните в `plugins/jarvis-core/skills/mac-control/SKILL.md` в таблице «Задача → инструмент».

Правила:
- Никаких `print` — только возвращаемый JSON и `logging`.
- Никаких исключений наружу: `@guarded` их ловит, но старайтесь возвращать осмысленное `error` + `hint`.
- Необратимое → параметр `confirmed: bool` и проверка `if not args.get("confirmed"): return {"needs_confirmation": True, ...}`.
- Таймауты у любых внешних вызовов.

## Добавить инструмент в `jarvis-core` (кроссплатформенный)

Аналогично, но регистрация в `__init__.py`:
```python
ctx.register_tool("jarvis_something", toolset="jarvis_core", schema=SCHEMAS["jarvis_something"], handler=tool_jarvis_something)
```
Для доступа к LLM внутри инструмента: `ctx.llm.complete(...)`; к конфигу плагина — `ctx.get_config("settings.key")`.

## Расширить базу знаний (`jarvis-brain`)

- Новая структурная операция для ночной ревизии → ветка в `Brain.apply_ops` + описание в `schemas.BRAIN_REVIEW` + правило в `skills/brain-nightly-review/SKILL.md`.
- Новый кандидат в плане ревизии → секция в `Brain.review_plan` (только чтение, решения принимает модель).
- Миграции схемы: увеличьте `SCHEMA_VERSION`, добавьте `ALTER TABLE` в `_init_schema` по значению `meta.schema_version`.
- Тесты: `tests/test_brain.py` (`JARVIS_BRAIN_DIR` → временная БД).

## Добавить slash-команду

```python
ctx.register_command("coffee", lambda raw: "☕ Заказано.", description="Заказать кофе")
```
Команда получает сырой текст после имени; возвращает строку для вывода пользователю.

## Добавить навык

`skills/<name>/SKILL.md`:
```markdown
---
name: my-skill
description: Одна строка — по ней Hermes решит, когда загрузить навык
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [jarvis]
    category: productivity
    requires_toolsets: [jarvis_macos]
---
## When to Use
## Procedure
## Pitfalls
## Verification
```
Установщик копирует `skills/*` в `~/.hermes/skills/jarvis/`. Проверка: `/skills` в чате.

Совет: попросите самого JARVIS: *«сделай навык из этой инструкции»* — Hermes умеет писать SKILL.md сам.

## Добавить событие/панель на HUD

Сервер не валидирует тип события — отправляйте любой JSON:
```python
from hud_client import hud
hud.send("my.event", {"foo": 1})
```
В `index.html` в функции `handleEvent(ev)` добавьте ветку `case "my.event":`.

Готовые панели: `jarvis_hud(action="show", kind="markdown|image|video|web|chart|text", position, ttl)`.

## Добавить gateway-хук

`hooks/<name>/HOOK.yaml`:
```yaml
name: my-hook
description: …
events: ["session:start", "agent:end"]
```
`handler.py`: `async def handle(event_type: str, context: dict): ...` — не блокируйте event loop, тяжёлое уносите в поток.

## Добавить MCP-сервер

Никакого кода: `hermes mcp install <name>` или в `config.yaml`:
```yaml
mcp_servers:
  github: {command: npx, args: ["-y", "@modelcontextprotocol/server-github"], env: {GITHUB_TOKEN: "${GITHUB_TOKEN}"}}
```
Инструменты появятся как `github_*`.

## Соглашения по коду

- Python 3.11+, только stdlib в плагинах и HUD (Hermes-venv может отличаться от системного Python).
- Docstring у каждого модуля: «что это и почему».
- Логгер `logging.getLogger("jarvis.<module>")`.
- Русский — в текстах для пользователя и документации; идентификаторы и комментарии — английский.
- Идемпотентность: `install.sh`, `setup_cron.sh`, `merge_config.py` можно запускать многократно.

## Структура состояния в `~/.hermes`

```
~/.hermes/
├── config.yaml            ← объединённый конфиг (наш фрагмент влит)
├── .env                   ← ключи (API_SERVER_KEY читается HUD)
├── SOUL.md  BOOT.md
├── plugins/jarvis-core  jarvis-macos
├── plugin-data/jarvis-core/state.json   ← режим, таймеры
├── skills/jarvis/*
├── hooks/jarvis-boot
├── jarvis/{hud/, scripts/, hud.pid}
├── logs/{agent.log, jarvis-hud.log, jarvis-gateway.log}
└── cron/jobs.json
```

## Идеи для развития

- Локальный wake word на своём имени (`wake_word.provider: sherpa`, `keywords: ["Джарвис"]`).
- Menu-bar приложение (SwiftBar/rumps) с индикатором и push-to-talk.
- Интеграция с Raycast: `jarvis ask "$1"` как Script Command.
- Голосовая идентификация говорящего (resemblyzer) → разные профили USER.md.
- Проактивные подсказки: хук `pre_llm_call` уже видит активное приложение — можно добавить «контекстные навыки» (в Xcode → загрузить навык `swift-dev`).
