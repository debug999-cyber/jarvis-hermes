---
name: jarvis-research-brief
description: Быстрое исследование темы с выводом сводки на HUD
version: 1.0.0
metadata:
  hermes:
    tags: [jarvis, research, web]
    category: research
---

# Исследование по запросу

## When to Use
«Разберись с…», «найди всё про…», «что известно о…», «сравни X и Y».

## Procedure
1. `web_search` — 2–3 запроса с разными формулировками (рус + англ).
2. `web_extract` для 2–4 лучших источников.
3. Сводка: 5 тезисов + 1 вывод + источники.
4. Голосом — только вывод и 2 главных тезиса; полную сводку — `jarvis_hud kind=markdown`.
5. Если тема заслуживает — предложи сохранить в Notes (`mac_notes create`) или память.

## Verification
На HUD есть панель с источниками; в голосовом ответе ≤ 3 предложений.
