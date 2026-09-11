---
name: jarvis-weather
description: Погода и прогноз без API-ключа через wttr.in (web_extract или curl). «Какая погода», «брать ли зонт», «погода в Москве».
version: 1.0.0
metadata:
  hermes:
    tags: [jarvis, weather, wttr]
    category: productivity
---

# Погода (wttr.in)

Готовый приём из навыка `weather` OpenClaw (openclaw/openclaw, MIT), адаптирован для JARVIS. Собственного инструмента погоды
у JARVIS больше нет — принцип «сначала готовое».

## When to Use
Любой вопрос о погоде/прогнозе/одежде/зонте. Город — из вопроса; если не назван — из USER.md/контекста; если неизвестен — спроси.

## Procedure
1. `web_extract` (или `terminal`: `curl -fsS --max-time 15 "https://wttr.in/<Город>?format=j2&lang=ru"`).
   Кириллицу в URL кодируй (`Москва` → `%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0`) или используй латиницу (`Moscow`).
2. Поля: `current_condition[0]` → `temp_C`, `FeelsLikeC`, `lang_ru[0].value` (описание), `precipMM`, `windspeedKmph`, `humidity`;
   `weather[0]` → `maxtempC`, `mintempC`, `hourly[].chanceofrain`.
3. Ответ — одна-две фразы в стиле JARVIS: температура, ощущается, осадки/ветер, совет («возьмите зонт, сэр»).
4. Если wttr.in не отвечает — повтори на `https://wttr.is/`, затем честно скажи, что сервис недоступен.

## Pitfalls
- Не пересказывай JSON. Не выдумывай прогноз при ошибке сети.
- Для утреннего брифинга — строго одна строка про погоду.
