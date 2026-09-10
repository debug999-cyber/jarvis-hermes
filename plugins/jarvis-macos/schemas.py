"""
Схемы инструментов плагина jarvis-macos.

Это то, что «видит» языковая модель: по описанию (description) она решает,
КОГДА вызывать инструмент, а по parameters — С КАКИМИ аргументами.

Правила хорошей схемы:
  * description — короткое, конкретное, с примерами фраз пользователя;
  * параметры — минимальный набор, с enum там, где значения фиксированы;
  * все схемы — обычные dict в формате JSON Schema (как у OpenAI function calling).
"""

# ─────────────────────────────── Приложения ────────────────────────────────

MAC_APP = {
    "name": "mac_app",
    "description": (
        "Управление приложениями macOS: открыть, закрыть, скрыть, показать, "
        "переключиться, получить список запущенных. Используй для фраз вроде "
        "«открой Safari», «закрой Telegram», «какие приложения запущены»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "quit", "force_quit", "hide", "activate", "list_running", "is_running"],
                "description": "Действие над приложением",
            },
            "app": {
                "type": "string",
                "description": "Имя приложения (Safari, Telegram, Visual Studio Code, Музыка…). "
                               "Не нужно для list_running.",
            },
        },
        "required": ["action"],
    },
}

MAC_OPEN = {
    "name": "mac_open",
    "description": (
        "Открыть URL, файл или папку стандартным приложением (аналог команды `open`). "
        "Примеры: «открой youtube.com», «открой папку Загрузки», «открой этот PDF»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "URL (https://…), путь к файлу/папке или имя стандартной папки (Downloads, Desktop, Documents)"},
            "app": {"type": "string", "description": "Необязательно: открыть конкретным приложением (например, 'Google Chrome')"},
        },
        "required": ["target"],
    },
}

MAC_SPOTLIGHT = {
    "name": "mac_spotlight",
    "description": "Поиск файлов на Mac через Spotlight (mdfind). Примеры: «найди презентацию про бюджет», «где файл отчёт.xlsx».",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Поисковый запрос (имя файла или содержимое)"},
            "limit": {"type": "integer", "description": "Максимум результатов (по умолчанию 20)", "default": 20},
            "only_dir": {"type": "string", "description": "Ограничить поиск папкой (путь)"},
        },
        "required": ["query"],
    },
}

MAC_FINDER = {
    "name": "mac_finder",
    "description": "Показать файл или папку в Finder, открыть Корзину, очистить Корзину (с подтверждением пользователя!).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["reveal", "open_trash", "empty_trash", "new_window"]},
            "path": {"type": "string", "description": "Путь для reveal/new_window"},
        },
        "required": ["action"],
    },
}

# ─────────────────────────────── Система ───────────────────────────────────

MAC_VOLUME = {
    "name": "mac_volume",
    "description": (
        "Громкость Mac: установить (0–100), прибавить/убавить, включить/выключить звук, узнать текущую. "
        "Примеры: «сделай громкость 30», «потише», «выключи звук»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "up", "down", "mute", "unmute", "toggle_mute", "get"]},
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Уровень для set / шаг для up|down (по умолчанию 10)"},
        },
        "required": ["action"],
    },
}

MAC_BRIGHTNESS = {
    "name": "mac_brightness",
    "description": "Яркость экрана: установить (0–100), прибавить/убавить. «Сделай экран ярче», «яркость 50 процентов».",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "up", "down"]},
            "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Уровень для set (0–100) или число шагов для up/down"},
        },
        "required": ["action"],
    },
}

MAC_DARK_MODE = {
    "name": "mac_dark_mode",
    "description": "Тёмная тема macOS: включить, выключить, переключить, узнать состояние.",
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["on", "off", "toggle", "get"]}},
        "required": ["action"],
    },
}

MAC_POWER = {
    "name": "mac_power",
    "description": (
        "Питание и блокировка: заблокировать экран, усыпить, выключить экран, перезагрузить, выключить Mac, "
        "запустить заставку. Для restart/shutdown ОБЯЗАТЕЛЬНО сначала переспроси пользователя."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["lock", "sleep", "display_sleep", "screensaver", "restart", "shutdown", "logout"]},
            "confirmed": {"type": "boolean", "description": "true только если пользователь явно подтвердил restart/shutdown/logout", "default": False},
        },
        "required": ["action"],
    },
}

MAC_WIFI = {
    "name": "mac_wifi",
    "description": "Wi-Fi: включить, выключить, узнать текущую сеть/состояние.",
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["on", "off", "status"]}},
        "required": ["action"],
    },
}

MAC_BLUETOOTH = {
    "name": "mac_bluetooth",
    "description": "Bluetooth: включить, выключить, статус, список подключённых устройств (требует утилиту blueutil, иначе — только статус).",
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["on", "off", "status", "devices"]}},
        "required": ["action"],
    },
}

MAC_BATTERY = {
    "name": "mac_battery",
    "description": "Состояние батареи: заряд в процентах, заряжается ли, оставшееся время.",
    "parameters": {"type": "object", "properties": {}},
}

MAC_SYSTEM_INFO = {
    "name": "mac_system_info",
    "description": "Сводка о системе: модель Mac, версия macOS, CPU, память, диск, аптайм, IP-адрес, топ процессов по CPU.",
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": ["all", "hardware", "memory", "disk", "network", "processes", "uptime"],
                "default": "all",
            }
        },
    },
}

# ─────────────────────────────── Медиа ─────────────────────────────────────

MAC_MEDIA = {
    "name": "mac_media",
    "description": (
        "Управление музыкой (Apple Music или Spotify): play, pause, next, previous, что играет, "
        "включить трек/плейлист по названию. Примеры: «включи музыку», «следующий трек», «что сейчас играет», "
        "«включи плейлист Chill»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["play", "pause", "toggle", "next", "previous", "now_playing", "play_track", "play_playlist", "shuffle_on", "shuffle_off"]},
            "query": {"type": "string", "description": "Название трека/исполнителя/плейлиста для play_track / play_playlist"},
            "player": {"type": "string", "enum": ["auto", "music", "spotify"], "default": "auto"},
        },
        "required": ["action"],
    },
}

MAC_SAY = {
    "name": "mac_say",
    "description": (
        "Произнести текст системным голосом macOS (`say`) или ОСТАНОВИТЬ речь. НЕ используй для обычных ответов — "
        "их уже озвучивает TTS Hermes, и получится дубль. action=stop — «замолчи», «стоп», «хватит»: глушит say/afplay и озвучку HUD."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["say", "stop"], "default": "say"},
            "text": {"type": "string"},
            "voice": {"type": "string", "description": "Имя голоса (Milena, Yuri, Samantha, Daniel…). По умолчанию — системный."},
            "rate": {"type": "integer", "description": "Слов в минуту (например 180)"},
        },
        "required": ["text"],
    },
}

MAC_NOTIFY = {
    "name": "mac_notify",
    "description": "Показать системное уведомление macOS (баннер в Центре уведомлений).",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"},
            "subtitle": {"type": "string"},
            "sound": {"type": "boolean", "default": True},
        },
        "required": ["title", "message"],
    },
}

MAC_SCREENSHOT = {
    "name": "mac_screenshot",
    "description": (
        "Сделать скриншот экрана (весь экран или конкретное окно) и вернуть путь к PNG. "
        "После этого можно вызвать vision_analyze с этим путём, чтобы «посмотреть» на экран. "
        "Примеры: «что у меня на экране?», «сделай скриншот»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["screen", "front_window"], "default": "screen"},
            "display": {"type": "integer", "description": "Номер дисплея (1 — основной)", "default": 1},
        },
    },
}

MAC_CAMERA_SNAP = {
    "name": "mac_camera_snap",
    "description": "Сделать снимок с веб-камеры (требует утилиту imagesnap: brew install imagesnap). Возвращает путь к JPG — далее vision_analyze.",
    "parameters": {"type": "object", "properties": {"warmup": {"type": "number", "default": 1.0, "description": "Секунды прогрева камеры"}}},
}

MAC_WALLPAPER = {
    "name": "mac_wallpaper",
    "description": "Сменить обои рабочего стола на указанный файл изображения.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
}

# ─────────────────────────────── Продуктивность ────────────────────────────

MAC_CALENDAR = {
    "name": "mac_calendar",
    "description": (
        "Календарь macOS: события на сегодня/завтра/дату, создать событие. "
        "Примеры: «что у меня сегодня по плану», «поставь встречу завтра в 15:00 на час»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["today", "tomorrow", "on_date", "create"]},
            "date": {"type": "string", "description": "Дата YYYY-MM-DD для on_date/create"},
            "title": {"type": "string", "description": "Название события (create)"},
            "start_time": {"type": "string", "description": "HH:MM (create)"},
            "duration_min": {"type": "integer", "default": 60},
            "calendar": {"type": "string", "description": "Имя календаря; по умолчанию — первый доступный"},
        },
        "required": ["action"],
    },
}

MAC_REMINDERS = {
    "name": "mac_reminders",
    "description": "Напоминания macOS: список активных, добавить напоминание (с датой/временем), отметить выполненным. «Напомни завтра в 9 позвонить маме».",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "complete"]},
            "title": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD HH:MM (необязательно)"},
            "list_name": {"type": "string", "description": "Имя списка напоминаний; по умолчанию — список по умолчанию"},
        },
        "required": ["action"],
    },
}

MAC_NOTES = {
    "name": "mac_notes",
    "description": "Заметки Apple Notes: создать заметку, найти по тексту, показать содержимое. «Запиши в заметки: …».",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "search", "read"]},
            "title": {"type": "string"},
            "body": {"type": "string"},
            "query": {"type": "string"},
            "folder": {"type": "string", "description": "Папка заметок (по умолчанию Notes)"},
        },
        "required": ["action"],
    },
}

MAC_CLIPBOARD = {
    "name": "mac_clipboard",
    "description": "Буфер обмена: прочитать текст из буфера или положить текст в буфер.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "text": {"type": "string"},
        },
        "required": ["action"],
    },
}

MAC_TYPE = {
    "name": "mac_type",
    "description": (
        "Напечатать текст в активное окно или нажать сочетание клавиш (через System Events; нужны права Accessibility). "
        "Примеры: «напечатай …», «нажми cmd+s»."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["type_text", "keystroke"]},
            "text": {"type": "string", "description": "Текст для type_text или клавиша для keystroke (например 's', 'return', 'tab', 'space')"},
            "modifiers": {
                "type": "array",
                "items": {"type": "string", "enum": ["command", "shift", "option", "control"]},
                "description": "Модификаторы для keystroke",
            },
        },
        "required": ["action", "text"],
    },
}

MAC_WINDOW = {
    "name": "mac_window",
    "description": "Окна: развернуть на весь экран, свернуть, расположить активное окно слева/справа/по центру, список окон приложения.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["maximize", "minimize", "left_half", "right_half", "center", "list"]},
            "app": {"type": "string", "description": "Приложение (по умолчанию — активное)"},
        },
        "required": ["action"],
    },
}

MAC_SHORTCUT = {
    "name": "mac_shortcut",
    "description": "Запустить команду из приложения «Быстрые команды» (Shortcuts) по имени, при необходимости передав текст на вход. Также умеет перечислить доступные команды.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["run", "list"], "default": "run"},
            "name": {"type": "string", "description": "Имя быстрой команды"},
            "input": {"type": "string", "description": "Текст на вход команды"},
        },
    },
}

MAC_CONTACTS = {
    "name": "mac_contacts",
    "description": (
        "Контакты macOS: search — найти человека по имени (email, телефон, организация, заметка); "
        "recent — люди из событий календаря за 30 дней (для первичного импорта в базу знаний); list — контакты с организацией. "
        "Персональные данные — не передавай наружу без нужды."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "recent", "list"], "default": "search"},
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["action"],
    },
}

MAC_FOCUS = {
    "name": "mac_focus",
    "description": (
        "Режим Focus macOS («Не беспокоить», «Работа», «Сон»…): get — какой активен; set — включить по имени "
        "(через Быструю команду «JARVIS Focus <имя>», off — выключить). Режим JARVIS синхронизируется с Focus автоматически."
    ),
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["get", "set"], "default": "get"}, "name": {"type": "string"}},
        "required": ["action"],
    },
}

MAC_FILE_MANAGE = {
    "name": "mac_file_manage",
    "description": (
        "Файловые операции на Mac: trash (в Корзину — единственный способ удаления), rename, move, mkdir, list (содержимое папки), "
        "info. Понимает алиасы папок: загрузки, рабочий стол, документы. Для чтения/записи содержимого используй file-инструменты Hermes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["trash", "rename", "move", "mkdir", "list", "info"]},
            "path": {"type": "string", "description": "Путь или алиас (~/Downloads, «рабочий стол»)"},
            "new_name": {"type": "string", "description": "Для rename"},
            "destination": {"type": "string", "description": "Папка назначения для move"},
            "limit": {"type": "integer", "default": 30},
        },
        "required": ["action"],
    },
}

MAC_APPLESCRIPT = {
    "name": "mac_applescript",
    "description": (
        "Выполнить произвольный AppleScript или JXA. ВКЛЮЧАЕТСЯ настройкой allow_raw_applescript. "
        "Используй только когда ни один специализированный mac_* инструмент не подходит."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "script": {"type": "string"},
            "language": {"type": "string", "enum": ["applescript", "javascript"], "default": "applescript"},
        },
        "required": ["script"],
    },
}

ALL_SCHEMAS = [
    MAC_APP, MAC_OPEN, MAC_SPOTLIGHT, MAC_FINDER,
    MAC_VOLUME, MAC_BRIGHTNESS, MAC_DARK_MODE, MAC_POWER, MAC_WIFI, MAC_BLUETOOTH, MAC_BATTERY, MAC_SYSTEM_INFO,
    MAC_MEDIA, MAC_SAY, MAC_NOTIFY, MAC_SCREENSHOT, MAC_CAMERA_SNAP, MAC_WALLPAPER, MAC_FILE_MANAGE, MAC_CONTACTS, MAC_FOCUS,
    MAC_CALENDAR, MAC_REMINDERS, MAC_NOTES, MAC_CLIPBOARD, MAC_TYPE, MAC_WINDOW, MAC_SHORTCUT, MAC_APPLESCRIPT,
]
