---
name: jarvis-briefing
description: Брифинг JARVIS для cron — утро/вечер, доставка в чат
version: 1.0.0
platforms: [macos]
metadata:
  hermes:
    tags: [jarvis, cron, briefing]
    category: productivity
---

# Брифинг (cron-вариант)

## When to Use
Задача запущена планировщиком (нет живого диалога) или пользователь вызвал `/brief`.

## Procedure
1. Загрузи навык `jarvis-core:morning-briefing` через `skill_view` и следуй ему.
2. Формат для доставки в мессенджер: 4–6 коротких строк, без markdown-таблиц.
3. Если данных нет ни по одному пункту (выходной, пустой календарь, погода недоступна) — всё равно поздоровайся и дай одну рекомендацию.
4. Не задавай вопросов — в cron некому отвечать.

## Verification
Ответ не длиннее 700 символов и не содержит служебного JSON.
