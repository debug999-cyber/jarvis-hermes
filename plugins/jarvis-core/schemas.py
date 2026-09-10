"""Схемы инструментов ядра JARVIS."""

JARVIS_HUD = {
    "name": "jarvis_hud",
    "description": (
        "Показать что-то на голографическом HUD JARVIS (веб-экран): текст/markdown, картинку (URL или путь), "
        "видео YouTube, веб-страницу (iframe) или очистить экран. Используй, когда пользователь говорит "
        "«покажи на экране», «выведи на HUD», «покажи видео про…», «очисти экран»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["show", "clear"], "default": "show"},
            "kind": {"type": "string", "enum": ["text", "markdown", "image", "video", "web", "chart"], "default": "text"},
            "title": {"type": "string"},
            "content": {
                "type": "string",
                "description": "Текст/markdown; URL картинки или локальный путь; URL YouTube/видео; URL страницы; "
                               "для chart — JSON вида {\"labels\":[...],\"values\":[...]}",
            },
            "position": {"type": "string", "enum": ["center", "left", "right"], "default": "center"},
            "ttl": {"type": "integer", "description": "Секунд до автоскрытия (0 — пока не очистят)", "default": 0},
        },
    },
}

JARVIS_TIMER = {
    "name": "jarvis_timer",
    "description": (
        "Таймеры и будильники: поставить на N минут или на время HH:MM, список, отмена. "
        "«Поставь таймер на 10 минут», «разбуди в 7:30», «отмени таймер»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "list", "cancel"]},
            "minutes": {"type": "number", "description": "Через сколько минут (для set)"},
            "at": {"type": "string", "description": "Время HH:MM (для set — будильник)"},
            "label": {"type": "string", "description": "Название таймера («чай», «созвон»)"},
        },
        "required": ["action"],
    },
}

JARVIS_MODE = {
    "name": "jarvis_mode",
    "description": (
        "Переключить режим JARVIS: normal (обычный), focus (не беспокоить, короткие ответы, без болтовни), "
        "night (тихий режим: тёмная тема, приглушённая громкость, короткие ответы), presentation (никаких уведомлений, "
        "не трогать окна). «Включи режим фокуса», «ночной режим», «обычный режим»."
    ),
    "parameters": {
        "type": "object",
        "properties": {"mode": {"type": "string", "enum": ["normal", "focus", "night", "presentation"]}},
        "required": ["mode"],
    },
}

JARVIS_WEATHER = {
    "name": "jarvis_weather",
    "description": "Текущая погода и прогноз на сегодня для города (без API-ключа). «Какая погода?», «погода в Москве».",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "Город; по умолчанию — из настроек"}},
    },
}
