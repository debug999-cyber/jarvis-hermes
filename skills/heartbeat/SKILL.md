---
name: jarvis-heartbeat
description: Периодическая проверка «нужно ли что-то сказать?» по чек-листу HEARTBEAT.md; молчит (NO_REPLY), если всё спокойно
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [jarvis, proactive, heartbeat]
    category: productivity
    requires_toolsets: [jarvis-core, jarvis-macos, jarvis-brain]
---

# Heartbeat — тихая проактивность

Идея заимствована у OpenClaw: агент периодически «просыпается», читает короткий чек-лист,
и если ничего не требует внимания — отвечает ровно `NO_REPLY` (сообщение не доставляется).
Это дешёвая альтернатива десяткам отдельных cron-задач: один прогон, один список, память о прошлых
предупреждениях в базе знаний.

## When to Use
- Запуск по cron (`jarvis heartbeat`, рекомендуемо каждые 30–60 минут в рабочее время) или из Shortcuts.
- Никогда — в ответ на живой вопрос пользователя.

## Procedure
1. Прочитай `~/.hermes/jarvis/HEARTBEAT.md` (`read_file`). Если файла нет или он пуст — ответь `NO_REPLY`.
2. Для каждого пункта чек-листа собери факт одним инструментом (`mac_battery`, `mac_calendar today`,
   `mac_reminders list`, `mac_system_info disk`, `brain_recall` и т. п.). Не больше 6 вызовов за прогон.
3. **Дедупликация.** Перед тем как что-то сообщить — `brain_recall(query="heartbeat <тема>", scope="episodes")`
   и `brain_review(action="episodes", limit=3)`. Если об этом уже предупреждали сегодня — молчи.
4. Если ни один пункт не сработал — ответ ровно `NO_REPLY` (без точки, без пояснений).
5. Если сработал — одно-два предложения в стиле JARVIS, без markdown. Затем
   `brain_remember(content="heartbeat: предупредил о …", kind="event", importance=1, tags="heartbeat")`,
   чтобы следующий прогон не повторялся.
6. В режиме `focus`/`night` (`jarvis_mode get`) сообщай только о том, что помечено в HEARTBEAT.md как `!срочно`.

## Notes
- Файл HEARTBEAT.md — пользовательский; JARVIS может предлагать в него пункты, но правит только по просьбе.
- Стоимость: один короткий вызов модели; при «NO_REPLY» gateway ничего не отправляет.
