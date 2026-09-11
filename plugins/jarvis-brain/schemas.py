"""JSON-схемы инструментов jarvis-brain — то, что видит модель."""

KINDS_HINT = (
    "Тип знания (kind): fact, preference, person, project, place, habit, decision, howto, goal, idea, event, device, insight. "
    "Можно использовать свой тип — он будет создан автоматически."
)

BRAIN_REMEMBER = {
    "name": "brain_remember",
    "description": (
        "Сохранить в личную базу знаний JARVIS устойчивый факт о пользователе, его окружении, предпочтениях, проектах, "
        "решениях или проверенных способах делать что-либо. Используй, когда пользователь говорит «запомни», сообщает о себе "
        "что-то важное, принимает решение, или когда ты сам выяснил нечто, что пригодится в будущем. НЕ сохраняй: "
        "секреты/пароли, сиюминутное («сейчас открыт Safari»), содержимое одного разговора без долгосрочной ценности. "
        "Одна заметка — одна мысль. Похожие заметки автоматически объединяются. " + KINDS_HINT
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Формулировка знания, полное предложение в третьем лице («Пользователь предпочитает…»)"},
            "kind": {"type": "string", "default": "fact"},
            "entity": {"type": "string", "description": "К какой карточке привязать: имя человека, проекта, места, устройства (опционально)"},
            "tags": {"type": "string", "description": "Теги через запятую: work, family, python, health…"},
            "importance": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3, "description": "1 — мелочь, 5 — критически важно"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8, "description": "Насколько уверен: 1.0 — сказал сам пользователь, 0.5 — вывод/догадка"},
            "valid_until": {"type": "string", "description": "ISO-дата, после которой знание устаревает (для временных фактов), опционально"},
        },
        "required": ["content"],
    },
}

BRAIN_RECALL = {
    "name": "brain_recall",
    "description": (
        "Найти в базе знаний JARVIS то, что уже известно по теме: предпочтения, людей, проекты, прошлые решения, «как обычно "
        "делаем». Вызывай ПЕРЕД тем, как переспрашивать пользователя о том, что он мог уже говорить, и перед задачами, где "
        "важен контекст (письмо коллеге, настройка проекта, рекомендации). Релевантные знания также подмешиваются в контекст "
        "автоматически — используй инструмент для целенаправленного поиска."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Тема или вопрос свободным текстом"},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "Ограничить типами (опционально)"},
            "entity": {"type": "string", "description": "Только заметки этой карточки (опционально)"},
            "limit": {"type": "integer", "default": 6, "maximum": 20},
            "scope": {"type": "string", "enum": ["notes", "episodes", "all"], "default": "notes",
                      "description": "notes — знания; episodes — дневник по дням («что было вчера/на прошлой неделе»); all — и то и другое"},
        },
        "required": ["query"],
    },
}

BRAIN_FORGET = {
    "name": "brain_forget",
    "description": (
        "Архивировать или исправить заметку в базе знаний. Используй, когда пользователь говорит «забудь», «это уже не так», "
        "«теперь по-другому». Заметка не удаляется физически — переводится в архив с сохранением истории (можно откатить). "
        "Для исправления передай new_content — старая версия будет заменена."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "ID заметки (из brain_recall)"},
            "query": {"type": "string", "description": "Если ID неизвестен — текст для поиска; будет затронута лучшая находка только при высокой уверенности"},
            "new_content": {"type": "string", "description": "Новая формулировка (исправление вместо архивации)"},
            "reason": {"type": "string"},
        },
    },
}

BRAIN_ENTITY = {
    "name": "brain_entity",
    "description": (
        "Работа с карточками (сущностями) базы знаний: люди, проекты, места, устройства. "
        "get — карточка со всеми заметками и связями; upsert — создать/обновить описание; "
        "list — список карточек (можно по типу); relate — связать две карточки (src —type→ dst, например «Анна works_at Acme»)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "upsert", "list", "relate"]},
            "name": {"type": "string"},
            "kind": {"type": "string", "description": "person | project | place | device | org | topic …"},
            "summary": {"type": "string", "description": "Краткое описание карточки (1–2 предложения)"},
            "tags": {"type": "string"},
            "src": {"type": "string"}, "dst": {"type": "string"}, "type": {"type": "string"},
            "limit": {"type": "integer", "default": 30},
        },
        "required": ["action"],
    },
}

BRAIN_REVIEW = {
    "name": "brain_review",
    "description": (
        "Обслуживание и реструктуризация базы знаний (в основном для ночной ревизии, но можно и по просьбе пользователя). "
        "stats — состояние базы; maintain — автоматическая уборка без LLM (бэкап, точные дубли, просроченное, decay, VACUUM); "
        "plan — список кандидатов на объединение/архивацию/перекатегоризацию/конфликты (решения принимаешь ты); "
        "apply — применить пакет операций ops: "
        "[{op:'merge',keep:ID,drop:[ID..],content?}, {op:'archive',id,reason}, {op:'supersede',id,content}, {op:'entity_summary',name,summary}, "
        "{op:'resolve_failure',id}, {op:'update',id,content?,kind?,tags?,importance?,confidence?,entity?}, "
        "{op:'rekind',id,kind}, {op:'retag',id,tags}, {op:'link',id,entity}, {op:'kind_define',name,description}, "
        "{op:'kind_rename',from,to}, {op:'entity_merge',keep,drop}, {op:'entity_update',name,kind?,summary?,tags?}, "
        "{op:'relate',src,dst,type}]; "
        "digest_queue — непереваренные ходы диалогов по дням для составления дневника; "
        "save_episode — сохранить резюме дня (day, summary, highlights) и пометить ходы как переваренные; "
        "finish — записать отчёт о ревизии (report); export — выгрузить читаемый снимок базы (BRAIN.md) и краткий профиль (PROFILE.md); "
        "profile — самое важное о пользователе одним текстом; episodes — дневник (query опционально); "
        "changelog — последние изменения; failures — журнал сбоев инструментов (что ломалось, сколько раз); "
        "verify — 1–2 сомнительных факта, которые стоит уточнить у пользователя; restore — откатить базу из последнего бэкапа (только по явной просьбе)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["stats", "maintain", "plan", "apply", "digest_queue", "save_episode", "finish", "export", "profile", "episodes", "changelog", "failures", "verify", "restore"]},
            "query": {"type": "string", "description": "Для episodes"},
            "ops": {"type": "array", "items": {"type": "object"}},
            "day": {"type": "string", "description": "YYYY-MM-DD для save_episode"},
            "summary": {"type": "string"},
            "highlights": {"type": "string"},
            "report": {"type": "string", "description": "Краткий отчёт для finish"},
            "path": {"type": "string", "description": "Куда выгрузить Markdown для export (по умолчанию $HERMES_HOME/jarvis/BRAIN.md)"},
            "limit": {"type": "integer", "default": 30},
        },
        "required": ["action"],
    },
}

BRAIN_REFLECT = {
    "name": "brain_reflect",
    "description": (
        "Собрать ВСЁ, что база знаний знает по вопросу, в одном вызове: релевантные заметки, карточки с резюме и связями, "
        "эпизоды дневника и историю изменений («раньше было так, теперь иначе»). Используй для вопросов вида «что ты знаешь о X», "
        "«как у нас обстоят дела с проектом Y», «напомни контекст перед встречей с Z», «что изменилось за месяц». "
        "Вернувшийся материал синтезируй в связный ответ сам — не пересказывай списком."
    ),
    "parameters": {
        "type": "object",
        "properties": {"question": {"type": "string"}, "limit": {"type": "integer", "default": 12, "maximum": 30}},
        "required": ["question"],
    },
}

BRAIN_HISTORY = {
    "name": "brain_history",
    "description": (
        "Что было верно раньше: закрытые/заменённые заметки с интервалами валидности («жил в Цюрихе 2024-01 → 2026-09»). "
        "supersede — темпоральная замена факта: старая заметка закрывается датой, новая создаётся; используй вместо brain_forget, "
        "когда важно помнить прошлое значение (переезд, смена работы, смена стека)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["history", "supersede"], "default": "history"},
            "query": {"type": "string"}, "entity": {"type": "string"},
            "id": {"type": "integer", "description": "Для supersede — id старой заметки"},
            "new_content": {"type": "string", "description": "Для supersede — новая формулировка"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["action"],
    },
}

ALL = [BRAIN_REMEMBER, BRAIN_RECALL, BRAIN_FORGET, BRAIN_ENTITY, BRAIN_REVIEW, BRAIN_REFLECT, BRAIN_HISTORY]

# ── Vault: файлы и проекты пользователя ─────────────────────────────────────
VAULT_SEARCH = {
    "name": "vault_search",
    "description": (
        "Поиск по СОДЕРЖИМОМУ файлов и проектов пользователя в хранилище JARVIS (~/JARVIS и подключённые папки): "
        "документы, заметки, PDF, таблицы, исходный код. Возвращает файлы, номер строки и фрагмент. "
        "Используй, когда вопрос касается «моих файлов/документов/проекта/заметок/договора/кода». "
        "Дальше читай файл через vault_read или штатный read_file по возвращённому path — доступ полный, "
        "править можно обычными инструментами (write_file, terminal) по этим путям."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Ключевые слова (по-русски/по-английски), как в поиске"},
            "limit": {"type": "integer", "default": 8, "maximum": 30},
            "in": {"type": "string", "description": "Ограничить папкой хранилища, например projects/foo или inbox"},
        },
        "required": ["query"],
    },
}

VAULT_READ = {
    "name": "vault_read",
    "description": (
        "Прочитать файл из хранилища (в т.ч. PDF, DOCX, PPTX, XLSX — с извлечением текста) кусками по ~6000 символов. "
        "path — абсолютный или относительно ~/JARVIS. Для папки вернёт дерево. Для длинных файлов передавай next_offset."
    ),
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "offset": {"type": "integer", "default": 0}, "limit": {"type": "integer", "default": 6000}},
        "required": ["path"],
    },
}

VAULT_MANAGE = {
    "name": "vault_manage",
    "description": (
        "Управление хранилищем ~/JARVIS и файлами в нём. Чтение: status; list (prefix, recent); tree; pending — новые файлы без резюме. "
        "Проекты: add — подключить папку (path, name) как projects/<name>; remove; connect — подключить стандартный источник "
        "(what: icloud|desktop|documents|downloads|notes-obsidian); reindex. "
        "Запись (только внутри хранилища/проектов): write (path, content, mode=overwrite|append) — создать/изменить текстовый файл; "
        "mkdir (path); move (path, to) — переместить/переименовать, напр. разложить inbox по папкам; trash (path) — в Корзину "
        "(необратимого удаления нет). summarized (file_id, note_id) — отметить файл как разобранный после brain_remember."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["status", "list", "tree", "pending", "add", "remove", "connect", "reindex",
                                                  "write", "mkdir", "move", "trash", "summarized"]},
            "path": {"type": "string"}, "name": {"type": "string"}, "prefix": {"type": "string"},
            "content": {"type": "string"}, "mode": {"type": "string", "enum": ["overwrite", "append"], "default": "overwrite"},
            "to": {"type": "string"}, "what": {"type": "string"}, "file_id": {"type": "integer"}, "note_id": {"type": "integer"},
            "recent": {"type": "boolean", "default": False}, "depth": {"type": "integer", "default": 2},
        },
        "required": ["action"],
    },
}

ALL = [*ALL, VAULT_SEARCH, VAULT_READ, VAULT_MANAGE]
