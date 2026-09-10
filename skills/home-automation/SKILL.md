---
name: jarvis-home-automation
description: Умный дом через Home Assistant / Shortcuts / HomeKit
version: 1.0.0
metadata:
  hermes:
    tags: [jarvis, home, homeassistant, homekit]
    category: home
---

# Умный дом

## When to Use
«Включи свет», «выключи всё в гостиной», «какая температура дома», «закрой шторы».

## Procedure
1. Если доступен toolset `homeassistant` (`ha_*` инструменты) — используй его: `ha_list_entities` → `ha_call_service`.
2. Если Home Assistant не настроен, но у пользователя HomeKit — используй `mac_shortcut run "<имя>"`:
   пользователь создаёт в Shortcuts команды «Свет гостиная вкл/выкл», «Сцена Кино» и т.п.
   Список доступных: `mac_shortcut list`.
3. Нет ни того, ни другого — предложи настроить: `hermes tools` → Home Assistant, либо создать Shortcuts.

## Pitfalls
- Не угадывай entity_id — всегда сначала список.
- Массовые действия («выключи всё») — переспроси, если затрагивают > 5 устройств.

## Verification
Сервис вернул success; при возможности подтверди состоянием (`ha_get_state`).
