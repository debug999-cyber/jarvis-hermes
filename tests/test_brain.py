"""Тесты jarvis-brain: хранилище, инструменты, хуки, ночная ревизия."""

from __future__ import annotations

import json

import pytest

from conftest import FakeCtx, load_plugin


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_BRAIN_DIR", str(tmp_path))
    mod = load_plugin("jarvis-brain")
    mod._brain = None  # свежая БД на каждый тест
    mod._vault = None
    yield mod
    mod._vault = None
    if mod._brain:
        mod._brain.close()
        mod._brain = None


def _j(s: str) -> dict:
    return json.loads(s)


def test_register(brain):
    ctx = FakeCtx()
    brain.register(ctx)
    assert {"brain_remember", "brain_recall", "brain_forget", "brain_entity", "brain_review", "brain_reflect", "brain_history",
            "vault_search", "vault_read", "vault_manage"} == set(ctx.tools)
    assert {"pre_llm_call", "post_llm_call", "pre_tool_call"} <= set(ctx.hooks)
    assert {"remember", "recall", "brain"} <= set(ctx.commands)
    assert "brain-nightly-review" in ctx.skills


def test_remember_recall_dedupe(brain):
    r1 = _j(brain.tool_brain_remember({"content": "Пользователь предпочитает кофе без сахара", "kind": "preference", "tags": "food"}))
    assert r1["success"] and r1["action"] == "created"
    # почти то же самое → обновление, а не дубль
    r2 = _j(brain.tool_brain_remember({"content": "Пользователь предпочитает кофе без сахара и молока", "kind": "preference", "importance": 4}))
    assert r2["action"] == "updated" and r2["id"] == r1["id"]
    assert brain.brain().stats()["notes_active"] == 1
    hits = _j(brain.tool_brain_recall({"query": "какой кофе любит?"}))["results"]
    assert hits and hits[0]["id"] == r1["id"] and hits[0]["importance"] == 4
    # другая тема — не находится
    assert _j(brain.tool_brain_recall({"query": "python тесты"}))["count"] == 0


def test_entities_and_relations(brain):
    brain.tool_brain_remember({"content": "Анна — руководитель проекта Atlas", "kind": "person", "entity": "Анна", "tags": "work"})
    brain.tool_brain_remember({"content": "Atlas пишется на Go и деплоится в k8s", "kind": "project", "entity": "Atlas"})
    rel = _j(brain.tool_brain_entity({"action": "relate", "src": "Анна", "dst": "Atlas", "type": "leads"}))
    assert rel["success"]
    card = _j(brain.tool_brain_entity({"action": "get", "name": "atlas"}))  # без учёта регистра
    assert card["success"] and card["entity"]["name"] == "Atlas"
    assert len(card["entity"]["notes"]) == 1
    assert any(r["other"] == "Анна" and r["type"] == "leads" for r in card["entity"]["relations"])
    lst = _j(brain.tool_brain_entity({"action": "list"}))["entities"]
    assert {e["name"] for e in lst} == {"Анна", "Atlas"}


def test_forget_and_correct(brain):
    nid = _j(brain.tool_brain_remember({"content": "Пользователь живёт в Цюрихе", "kind": "place"}))["id"]
    fixed = _j(brain.tool_brain_forget({"id": nid, "new_content": "Пользователь живёт в Берне"}))
    assert fixed["action"] == "corrected" and "Берне" in fixed["note"]
    arch = _j(brain.tool_brain_forget({"query": "где живёт", "reason": "переехал"}))
    assert arch["action"] == "archived" and arch["id"] == nid
    assert brain.brain().stats()["notes_active"] == 0
    assert brain.brain().stats()["notes_archived"] == 1
    # история сохранена
    ops = [c["op"] for c in brain.brain().changelog(10)]
    assert "note_archive" in ops and "note_update" in ops and "note_create" in ops


def test_context_injection_and_auto_capture(brain):
    ctx = FakeCtx()
    brain.register(ctx)
    brain.tool_brain_remember({"content": "Пользователь работает в компании Acme над проектом Atlas", "kind": "project", "importance": 4})
    out = brain.hook_pre_llm_call(session_id="s1", user_message="напиши письмо коллегам из Acme про Atlas")
    assert out and "[JARVIS memory]" in out["context"] and "Acme" in out["context"]
    # нерелевантное сообщение — контекста нет
    assert brain.hook_pre_llm_call(session_id="s1", user_message="сколько будет два плюс два") is None
    # auto-capture: «запомни …» без вызова инструмента
    brain.hook_pre_llm_call(session_id="s2", user_message="Джарвис, запомни: мою собаку зовут Рекс")
    brain.hook_post_llm_call(session_id="s2", user_message="Джарвис, запомни: мою собаку зовут Рекс", assistant_response="Записал.")
    hits = _j(brain.tool_brain_recall({"query": "собака Рекс"}))["results"]
    assert hits and "собаку зовут Рекс" in hits[0]["content"] and hits[0]["content"].startswith("Пользователя")
    # если инструмент вызывался — второй раз не пишем
    brain.hook_pre_llm_call(session_id="s3", user_message="запомни: кот Барсик")
    brain.hook_pre_tool_call(tool_name="brain_remember", args={}, session_id="s3")
    n_before = brain.brain().stats()["notes_active"]
    brain.hook_post_llm_call(session_id="s3", user_message="запомни: кот Барсик", assistant_response="ок")
    assert brain.brain().stats()["notes_active"] == n_before
    # журнал ходов ведётся
    assert brain.brain().stats()["turns_pending"] >= 2


def test_nightly_review_cycle(brain):
    b = brain.brain()
    # заготовка: точные дубли, near-дубли, просроченное, эпизоды
    b.remember("Пользователь любит тёмную тему", kind="preference")
    b._conn.execute("INSERT INTO notes(content, kind, status, created_at, updated_at, importance, confidence) VALUES (?,?,?,?,?,3,0.8)",
                    ("пользователь любит тёмную тему", "preference", "active", "2024-01-01T00:00:00", "2024-01-01T00:00:00"))
    b.remember("Встреча с Иваном по поводу бюджета в пятницу", kind="event", valid_until="2020-01-01T00:00:00", dedupe_threshold=1.1)
    b.remember("Пользователь использует Neovim как основной редактор", kind="fact", dedupe_threshold=1.1)
    b.log_turn("s", "cli", "давай настроим nginx", "Готово, nginx настроен на порт 8080")
    b.log_turn("s", "cli", "спасибо", "Всегда пожалуйста, сэр")

    rep = _j(brain.tool_brain_review({"action": "maintain"}))["report"]
    assert rep["exact_duplicates_merged"] == 1 and rep["expired"] == 1 and rep["backup"].endswith(".db")

    days = _j(brain.tool_brain_review({"action": "digest_queue"}))["days"]
    assert len(days) == 1 and len(days[0]["turns"]) == 2
    day = days[0]["day"]
    ep = _j(brain.tool_brain_review({"action": "save_episode", "day": day, "summary": "Настроили nginx на 8080.", "highlights": "nginx:8080"}))
    assert ep["turns_digested"] == 2 and b.stats()["turns_pending"] == 0

    plan = _j(brain.tool_brain_review({"action": "plan"}))["plan"]
    assert any(u["content"].startswith("Пользователь использует Neovim") for u in plan["uncategorized"])
    nid = plan["uncategorized"][0]["id"]
    res = _j(brain.tool_brain_review({"action": "apply", "ops": [
        {"op": "rekind", "id": nid, "kind": "howto"},
        {"op": "retag", "id": nid, "tags": "dev, editor"},
        {"op": "link", "id": nid, "entity": "Neovim"},
        {"op": "kind_define", "name": "tooling", "description": "Инструменты разработки"},
        {"op": "kind_rename", "from": "howto", "to": "tooling"},
        {"op": "bogus", "id": 1},
    ]}))
    assert res["applied"] == 5 and len(res["errors"]) == 1
    note = b.get_note(nid)
    assert note["kind"] == "tooling" and note["tags"] == "dev,editor" and note["entity_id"]
    assert not any(k["name"] == "howto" for k in b.kinds())

    exp = _j(brain.tool_brain_review({"action": "export", "path": str(b.path.parent / "BRAIN.md")}))
    text = open(exp["path"], encoding="utf-8").read()
    assert "Neovim" in text and "Дневник" in text
    fin = _j(brain.tool_brain_review({"action": "finish", "report": "Слито 1, архивировано 1."}))
    assert fin["success"] and b.stats()["last_review"]["report"].startswith("Слито")
    # первый ход новой сессии получает вчерашний эпизод
    ctx_out = brain.hook_pre_llm_call(session_id="n", user_message="привет", is_first_turn=True)
    assert ctx_out and "nginx" in ctx_out["context"]


def test_slash_commands(brain):
    ctx = FakeCtx()
    brain.register(ctx)
    out = ctx.commands["remember"]["fn"]("люблю зелёный чай #food #preference")
    assert "Записано" in out
    assert "зелёный чай" in ctx.commands["recall"]["fn"]("чай")
    st = ctx.commands["brain"]["fn"]("stats")
    assert "1 заметок" in st
    assert ctx.commands["brain"]["fn"]("review") == "" and ctx.injected


def test_secret_redaction_and_conflict_signal(brain):
    r = _j(brain.tool_brain_remember({"content": "Токен GitHub: ghp_abcdefghijklmnopqrstuvwxyz0123456789", "kind": "device"}))
    assert r["success"] and "ghp_" not in r["note"] and "[скрыто]" in r["note"] and r.get("warning")
    # журнал ходов тоже редактируется
    b = brain.brain()
    b.log_turn("s", "cli", "мой пароль: hunter2secret", "Не буду это запоминать")
    row = b._conn.execute("SELECT user_text FROM turns ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert "hunter2secret" not in row
    # конфликт: тот же тип, похожая, но не идентичная формулировка
    brain.tool_brain_remember({"content": "Пользователь живёт в городе Цюрих", "kind": "place"})
    r2 = _j(brain.tool_brain_remember({"content": "Пользователь живёт в городе Берн", "kind": "place"}))
    assert r2["action"] == "created" and r2.get("possible_conflicts") and "Цюрих" in r2["possible_conflicts"][0]["content"]


def test_episodes_search_and_profile(brain):
    b = brain.brain()
    b.save_episode("2026-09-08", "Настроили nginx и обсудили миграцию на Postgres.", "nginx; postgres")
    b.save_episode("2026-09-07", "Спокойный день, планировали отпуск в Италии.", "отпуск")
    res = _j(brain.tool_brain_recall({"query": "что было с postgres", "scope": "episodes"}))
    assert res["episodes"] and res["episodes"][0]["day"] == "2026-09-08"
    brain.tool_brain_remember({"content": "Пользователя зовут Алексей", "importance": 5, "confidence": 1.0})
    brain.tool_brain_remember({"content": "Мелочь: любит синий цвет", "importance": 1})
    prof = _j(brain.tool_brain_review({"action": "profile"}))["profile"]
    assert "Алексей" in prof and "синий" not in prof


def test_restore_backup(brain):
    b = brain.brain()
    brain.tool_brain_remember({"content": "Пользователь любит чай", "kind": "preference"})
    b.backup()
    brain.tool_brain_remember({"content": "Пользователь любит какао", "kind": "preference", "importance": 2})
    assert b.stats()["notes_active"] == 2
    src = _j(brain.tool_brain_review({"action": "restore"}))["restored_from"]
    assert src.endswith(".db") and b.stats()["notes_active"] == 1
    assert list(b.path.parent.glob("*.pre-restore-*.db"))


def test_auto_capture_third_person(brain):
    assert brain._third_person("я живу в Берне").startswith("Пользователь ")
    assert brain._third_person("у меня аллергия").startswith("У пользователя ")
    assert brain._third_person("мою собаку зовут Рекс").startswith("Пользователя: ")
    assert brain._third_person("my wife is Anna").startswith("User's ")


def test_review_plan_has_reflection_material(brain):
    b = brain.brain()
    b.save_episode("2026-09-08", "Опять переносили дедлайн Atlas.", "")
    brain.tool_brain_remember({"content": "Решили перенести дедлайн Atlas на неделю", "kind": "decision"})
    plan = _j(brain.tool_brain_review({"action": "plan"}))["plan"]
    assert plan["reflection_material"]["recent_episodes"] and plan["reflection_material"]["recent_decisions"]
    assert "pending_turns_days" in plan and "auto_captured_to_rephrase" in plan


def test_supersede_keeps_history(brain):
    old = _j(brain.tool_brain_remember({"content": "Пользователь живёт в Цюрихе", "kind": "place", "entity": "Дом"}))["id"]
    res = _j(brain.tool_brain_history({"action": "supersede", "id": old, "new_content": "Пользователь живёт в Берне"}))
    assert res["success"] and res["new_id"] != old
    b = brain.brain()
    assert b.get_note(old)["status"] == "superseded" and b.get_note(old)["valid_until"]
    assert b.get_note(res["new_id"])["entity_id"] == b.get_note(old)["entity_id"]
    hist = _j(brain.tool_brain_history({"action": "history", "entity": "Дом"}))["history"]
    assert hist and "Цюрихе" in hist[0]["content"] and hist[0]["superseded_by"] == res["new_id"]
    # активный поиск видит только новое
    hits = _j(brain.tool_brain_recall({"query": "где живёт"}))["results"]
    assert len(hits) == 1 and "Берне" in hits[0]["content"]


def test_auto_link_and_reflect(brain):
    brain.tool_brain_entity({"action": "upsert", "name": "Atlas", "kind": "project", "summary": "Сервис на Go"})
    r = _j(brain.tool_brain_remember({"content": "Дедлайн Atlas перенесли на ноябрь", "kind": "event"}))
    b = brain.brain()
    assert b.get_note(r["id"])["entity_id"] == b.entity_get("Atlas", with_notes=False)["id"]
    # словоформа: «Атласа» → карточка «Атлас»
    brain.tool_brain_entity({"action": "upsert", "name": "Атлас", "kind": "project"})
    r2 = _j(brain.tool_brain_remember({"content": "Бюджет Атласа согласован", "kind": "decision"}))
    assert b.get_note(r2["id"])["entity_id"] == b.entity_get("Атлас", with_notes=False)["id"]
    b.save_episode("2026-09-08", "Обсуждали дедлайн Atlas.", "")
    mat = _j(brain.tool_brain_reflect({"question": "как дела с Atlas"}))
    assert mat["notes"] and any(e["name"] == "Atlas" for e in mat["entities"]) and mat["episodes"]


def test_failures_journal(brain):
    ctx = FakeCtx()
    brain.register(ctx)
    assert "post_tool_call" in ctx.hooks and "transform_llm_output" in ctx.hooks and "pre_transcription" in ctx.hooks
    for _ in range(3):
        brain.hook_post_tool_call(tool_name="mac_calendar", args={"action": "today"},
                                  result=json.dumps({"success": False, "error": "Нет прав Accessibility"}))
    brain.hook_post_tool_call(tool_name="mac_power", result=json.dumps({"success": False, "error": "Действие требует явного подтверждения"}))
    f = _j(brain.tool_brain_review({"action": "failures"}))["failures"]
    assert len(f) == 1 and f[0]["count"] == 3 and f[0]["tool"] == "mac_calendar"
    plan = _j(brain.tool_brain_review({"action": "plan"}))["plan"]
    assert plan["tool_failures"][0]["tool"] == "mac_calendar"
    _j(brain.tool_brain_review({"action": "apply", "ops": [{"op": "resolve_failure", "id": f[0]["id"]}]}))
    assert _j(brain.tool_brain_review({"action": "failures"}))["failures"] == []


def test_voice_polish_and_vocabulary(brain):
    text = "## Итоги\n\n- **Первое**: сделано `make deploy`.\n- Второе: см. [док](https://x.y).\n\n```bash\nls -la\n```\nТретье предложение. Четвёртое. Пятое. Шестое."
    out = brain.polish_for_voice(text)
    assert "**" not in out and "```" not in out and "http" not in out and "#" not in out
    assert out.endswith("Подробности — в текстовом ответе.")
    assert brain.hook_transform_llm_output(response_text=text, platform="cli") is None
    assert brain.hook_transform_llm_output(response_text=text, platform="voice")
    brain.tool_brain_remember({"content": "Анна — тимлид Atlas", "kind": "person", "entity": "Анна", "importance": 4})
    brain.tool_brain_remember({"content": "Проект Atlas на Go", "kind": "project", "entity": "Atlas"})
    v = brain.brain().vocabulary()
    assert "Анна" in v and "Atlas" in v
    hint = brain.hook_pre_transcription(provider="local", prompt=None)
    assert hint and "Anna" not in hint["prompt"] and "Анна" in hint["prompt"]


def test_verify_low_confidence_once(brain):
    b = brain.brain()
    b._conn.execute("INSERT INTO notes(content, kind, status, importance, confidence, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("Пользователь, кажется, перешёл на Neovim", "fact", "active", 3, 0.4, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    items = _j(brain.tool_brain_review({"action": "verify"}))["to_verify"]
    assert len(items) == 1
    assert _j(brain.tool_brain_review({"action": "verify"}))["to_verify"] == []  # второй раз не спрашиваем


def test_stale_summaries_and_entity_summary_op(brain):
    b = brain.brain()
    for txt in ("Zeta написана на Rust и работает на ARM", "Заказчик Zeta — банк, дедлайн в декабре", "У Zeta три разработчика и один тестировщик"):
        brain.tool_brain_remember({"content": txt, "kind": "project", "entity": "Zeta"})
    plan = _j(brain.tool_brain_review({"action": "plan"}))["plan"]
    assert plan["stale_entity_summaries"] and plan["stale_entity_summaries"][0]["name"] == "Zeta"
    _j(brain.tool_brain_review({"action": "apply", "ops": [{"op": "entity_summary", "name": "Zeta", "summary": "Проект из трёх фактов"}]}))
    assert b.entity_get("Zeta", with_notes=False)["summary"] == "Проект из трёх фактов"
    assert _j(brain.tool_brain_review({"action": "plan"}))["plan"]["stale_entity_summaries"] == []


def test_schema_migration_from_v1(tmp_path, monkeypatch):
    import sqlite3
    import importlib.util
    spec = importlib.util.spec_from_file_location("bdb", "plugins/jarvis-brain/db.py")
    db = importlib.util.module_from_spec(spec); spec.loader.exec_module(db)
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript(db.SCHEMA.replace("valid_from TEXT, ", ""))  # схема v1 без valid_from
    c.execute("INSERT INTO meta VALUES ('schema_version','1')")
    c.execute("INSERT INTO notes(content, kind, status, created_at, updated_at) VALUES ('старая', 'fact', 'active', '2025-01-01T00:00:00', '2025-01-01T00:00:00')")
    c.commit(); c.close()
    b = db.Brain(p)
    assert b._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "2"
    assert b.get_note(1)["valid_from"] == "2025-01-01T00:00:00"
    b.close()


def test_memory_feedback_lowers_confidence(brain):
    plug, b = brain, brain.brain()
    n = b.remember("Пользователь пьёт кофе без сахара", kind="preference", importance=4)
    plug.hook_pre_llm_call(session_id="s1", user_message="какой кофе я люблю без сахара?")
    assert n["id"] in plug._last_injected["s1"]
    # «это не так» → уверенность падает, появляется просьба уточнить
    out = plug.hook_pre_llm_call(session_id="s1", user_message="Нет, это не так — я давно пью с сахаром")
    note = b.get_note(n["id"])
    assert note["confidence"] < 0.6 and "verify" in note["tags"]
    assert "опроверг" in (out or {}).get("context", "")
    # подтверждение возвращает доверие
    plug._last_injected["s1"] = [n["id"]]
    plug.hook_pre_llm_call(session_id="s1", user_message="верно")
    assert b.get_note(n["id"])["confidence"] > note["confidence"]
