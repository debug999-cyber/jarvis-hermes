# Changelog

## 1.5.0 — новый дизайн: HUD «Cinematic Glass», иконка, баннер
### HUD (`hud/static/index.html`, переписан)
- Арк-реактор рисуется на canvas: состояния standby / listening / processing / speaking, реагирует на громкость микрофона.
- Спокойная композиция: по центру реактор и диалог, всё остальное — стеклянные карточки по требованию:
  **A** Activity (лента tool calls), **S** System (батарея, модель, сессия), **K** BRAIN (поиск по базе знаний), **T** TTS, **B** boot, **/** ввод, **Esc** очистить.
- Центральная панель агента (график/картинка/видео/веб) аккуратно «отодвигает» реактор вверх вместо перекрытия.
- Пилюля-композер с индикатором волны, тосты алертов, чип «доступно обновление», прогресс-бар загрузки.
- Deep links `/#brain`, `/#activity`, `/#system`. Палитра: cyan `#37E6FF`, amber `#FFB547`, фон `#070A10`; типографика Inter/SF Pro.
- Совместимость сохранена: те же SSE-события, `/api/chat`, `/api/brain`, `jarvis_hud` (kind/position/ttl), режим `night`.
### JARVIS.app
- Новая иконка в стиле HUD (стеклянный squircle, сегментированные кольца, янтарное ядро) — `app/make_icon.py`.
### Документация
- Баннер и скриншоты в README (`docs/img/`), таблица горячих клавиш HUD в `docs/USAGE.md`.

## 1.4.1 — JARVIS.app: первый запуск понятнее, без UserNotifications
- Уведомления через `osascript` вместо фреймворка UserNotifications (он мог ронять приложение, собранное без Xcode).
- Подсказка при первом запуске «ищите ◉ в строке меню»; повторный клик по приложению открывает его меню.
- `jarvis app status|debug` для диагностики.

## 1.4.0 — полноценное приложение и автообновление

### JARVIS.app
- Приложение строки меню (`app/JarvisMenuBar.swift`, собирается `swiftc` в установщике, без Xcode-проекта): индикатор состояния
  HUD/API, меню (HUD, голос, «Спросить…», база знаний, старт/стоп, брифинг, selftest, обновления, откат, настройки, логи),
  автозапуск при входе (`ai.jarvis.app`), иконка-реактор. `jarvis app [open|build|quit]`.

### Автообновление
- `scripts/update.py` → `~/.hermes/jarvis/update.py`: `check / apply / rollback / auto / status / set`.
  Каналы `stable` (GitHub Releases) и `main`; режимы `off / check / auto`. Бэкап перед установкой (3 последних), smoke-test
  скачанного кода, автоматический откат при ошибке, перезапуск сервисов, уведомление macOS + HUD.
- launchd-агент `ai.jarvis.updater` (ежедневно 11:15). `install.json` / `update.json` / `VERSION`.
- Инструмент `jarvis_update` — «обнови себя» / «откати обновление» голосом (apply/rollback только с confirmed).
  Контекст хода сообщает агенту о доступном обновлении.
- `jarvis update [--check|--status|--rollback|--force|--channel|--auto|--hermes]`, `jarvis version`.
- `.github/workflows/release.yml` — релиз создаётся автоматически при изменении `VERSION` в main.
- `install.sh`: флаги `--no-cron`, `--no-app`, тихий режим `JARVIS_QUIET=1` для updater'а; `~/.jarvis-home` для нестандартного `HERMES_HOME`.

### Тесты
- 65 тестов: `tests/test_updater.py` (сравнение версий, check с fallback на main, бэкап/откат, apply с проваленным install.sh → откат,
  smoke-test, инструмент jarvis_update).

## 1.3.1 — первый прогон на реальном Mac (macOS 26.5, Apple Silicon)

`jarvis selftest`: 17 из 21 проверок прошли сразу. Исправлено по его результатам:
- macOS отдаёт ошибки AppleScript **на языке системы**: «Функции Упрощенного доступа для osascript не разрешены» не распознавалось
  как отсутствие прав Accessibility. Теперь `mac.py` и классификатор selftest понимают русские и английские формулировки и коды (-1719, -1743).
- selftest сначала берёт плагин из репозитория/`~/.hermes/jarvis`, а не установленную (возможно старую) копию; устаревшие
  инструменты помечаются `↻ требуют переустановки плагина` вместо «✖ сломано»; флаг `--plugin <путь>`.
- 58 тестов, включая реальный текст ошибки с Mac пользователя.

## 1.3.0 — третье ревью: память во времени, самодиагностика, тихая проактивность

### База знаний (schema v2, миграция автоматическая)
- **Темпоральные факты**: `valid_from/valid_until`, `status=superseded`; `brain_history(supersede|history)`, `/brain history`,
  операция `supersede` в ночной ревизии — изменившиеся факты закрываются датой, а не удаляются (Graphiti/Zep).
- **Авто-привязка** заметок к карточкам по упоминанию имени (с русскими словоформами).
- **Ментальные модели**: `stale_entity_summaries` в плане + операция `entity_summary` — резюме карточек живут и обновляются.
- **Журнал сбоев** инструментов (`failures`, хук `post_tool_call`) → `brain_review failures`, `tool_failures` в ночном плане,
  `resolve_failure`; автозакрытие через 30 дней; на HUD.
- **`brain_reflect(question)`** — заметки + карточки + эпизоды + история одним вызовом (Hindsight reflect).
- **`brain_review verify`** — 1–2 сомнительных факта для уточнения утром, тег `verify_asked`.
- `backup_mirror` — зеркало последнего бэкапа (например, в iCloud Drive).
- FTS-индекс перестраивается, если создан поверх существующей базы.

### Голос
- `pre_transcription`: имена людей/проектов из базы → подсказка Whisper.
- `transform_llm_output`: на голосовых платформах — без markdown, ≤4 предложений.

### Проактивность
- **Heartbeat**: навык `jarvis/heartbeat`, `~/.hermes/jarvis/HEARTBEAT.md`, cron каждые 45 мин, `NO_REPLY` по умолчанию (OpenClaw).
- Watchdog: **справка из базы знаний к встрече** (участники, обещания) на HUD; **Focus macOS → режим JARVIS**.
- Утренний брифинг уточняет один сомнительный факт.

### macOS
- `mac_contacts` (search/recent/list), `mac_focus` (get/set через Shortcuts) — 29 инструментов.
- **`jarvis selftest [--fix]`** — прогон всех инструментов в режиме чтения с таблицей прав.
- **`jarvis brain import`** — первичный импорт контактов/участников встреч/проектов с подтверждением.

### Тесты
- 57 тестов (supersede/история, авто-привязка, reflect, журнал сбоев, полировка голоса, словарь, verify, ментальные модели,
  миграция v1→v2, Focus-синхронизация, справка к встрече, selftest-классификатор).

## 1.2.0 — второе ревью

### Безопасность
- **CSRF на HUD**: убран `Access-Control-Allow-Origin: *`; POST принимаются только same-origin (`Origin` = `Host`) и только `application/json`;
  `X-Frame-Options: SAMEORIGIN`. Раньше любая вкладка браузера могла отправить команду в `/api/chat`.
- **Редакция секретов** в базе знаний: токены, hex-ключи, `пароль: …`, номера карт, PEM — заменяются на `[скрыто]` до записи (заметки и журнал ходов).
- `.env` → `chmod 600`.

### Исправлено
- `install.sh` копировал `.env.example` как `.env` с пустым `API_SERVER_KEY=` → ключ не генерировался → HUD-чат не работал. Теперь проверяется непустое значение, пример кладётся в `~/.hermes/jarvis/env.example`.
- HUD: чтение `API_SERVER_KEY` из `.env` игнорирует inline-комментарии.
- `pre_tool_call` без `session_id` помечал все сессии — теперь только активные.
- `review_plan`: список дней без хака через `digest_queue(max_chars=1)`.
- Порог дедупликации поднят 0.6 → 0.75: «живёт в Цюрихе»/«живёт в Берне» больше не сливаются молча.

### База знаний
- **Сигнал о конфликте при записи** (`possible_conflicts` + подсказка), `previous` при обновлении.
- **Поиск по дневнику**: `brain_recall(scope=episodes|all)`, `/brain diary`.
- **Рефлексия**: тип `insight`, `reflection_material` в плане ревизии, шаг «0–2 наблюдения» в навыке.
- **Профиль**: `brain_review profile/export` → `PROFILE.md` (importance ≥4 + insights); еженедельный cron сверяет его с памятью Hermes.
- **Откат**: `brain_review restore`, `/brain restore`, `jarvis brain restore` (с сохранением текущей базы).
- Авто-захват переводит «я/у меня/мой» в третье лицо; ночная ревизия перефразирует точно (`auto_captured_to_rephrase`).
- Механизм миграций схемы (`Brain._migrate`, `meta.schema_version`).
- **HUD-панель базы знаний** (клавиша K / клик по BRAIN): типы, карточки, последние записи, дневник, последняя ревизия — read-only `/api/brain`.
- Брифинг использует `brain_recall` (обещания/дедлайны) и пишет факты дня.
- +5 тестов (всего 44).

## 1.1.0 — ревью и база знаний

### Добавлено
- **Плагин `jarvis-brain`** — самообслуживаемая база знаний (SQLite + FTS5): таксономия типов, карточки сущностей и связи,
  заметки с важностью/уверенностью/сроком годности, журнал ходов → дневник по дням, полный changelog, бэкапы.
  Инструменты `brain_remember / brain_recall / brain_forget / brain_entity / brain_review`, команды `/remember /recall /brain`,
  хук контекста `[JARVIS memory]`, авто-захват «запомни …», навыки `brain-usage` и `brain-nightly-review`.
- **Ночная ревизия** (cron 03:30): автоуборка без LLM → дневник дня → план кандидатов → решения модели → транзакционный apply → export → отчёт.
- **Watchdog** в `jarvis-core`: контроль батареи (и, опционально, напоминания о встречах за 10 минут) локально, без вызовов LLM.
  Заменяет cron-задачу «контроль батареи» (48 вызовов LLM в сутки).
- `mac_file_manage` — файловые операции с удалением только в Корзину (в SECURITY.md упоминался, но отсутствовал).
- `jarvis brain …` в CLI; панель BRAIN и события `brain.*`, `alert` на HUD; skill-bundle `/jarvis`; `config/.env.example`, `config/AGENTS.md`.
- `docs/BRAIN.md`, `docs/CHANGELOG.md`; 11 новых тестов (всего 39).

### Исправлено (по результатам ревью)
- `jarvis_mode` падал бы на машинах без `shortcuts` (Linux/старые macOS) — вызов обёрнут, добавлена схема имён «JARVIS Mode <mode>».
- `mac_reminders add`: удалён мёртвый код с заглушкой `props`.
- `_events_for_day`: удалены неиспользуемые переменные.
- `auto_maintenance`: точные дубликаты сравниваются в Python (SQLite `lower()` не понимает кириллицу).
- Установщик/CLI/конфиг/бандл знают о третьем плагине; cron «обслуживание памяти» теперь переносит факты в базу знаний.
- SOUL.md: принцип «Помни» переписан под `brain_*`.
