"""Регистрация плагинов, валидность схем, поведение обработчиков вне macOS."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import platform

import pytest
import yaml

from conftest import PLUGINS, load_plugin

IS_MAC = platform.system() == "Darwin"


# ─────────────────────────── манифесты и схемы ─────────────────────────────

@pytest.mark.parametrize("name", ["jarvis-core", "jarvis-macos", "jarvis-brain"])
def test_manifest_valid(name):
    m = yaml.safe_load((PLUGINS / name / "plugin.yaml").read_text())
    assert m["name"] == name
    assert "version" in m and "description" in m
    assert isinstance(m.get("provides_tools", []), list)


def test_macos_manifest_lists_all_tools():
    macos = load_plugin("jarvis-macos")
    m = yaml.safe_load((PLUGINS / "jarvis-macos" / "plugin.yaml").read_text())
    declared = set(m["provides_tools"])
    actual = {s["name"] for s in macos.schemas.ALL_SCHEMAS}
    assert declared == actual, f"manifest≠schemas: {declared ^ actual}"
    assert actual == set(macos.tools.HANDLERS), "у каждой схемы должен быть обработчик"


def _check_schema(schema: dict):
    assert schema["name"].isidentifier()
    assert len(schema["description"]) > 20
    params = schema["parameters"]
    assert params["type"] == "object"
    for req in params.get("required", []):
        assert req in params["properties"], f"{schema['name']}: required {req} без описания"
    for pname, p in params["properties"].items():
        assert "type" in p, f"{schema['name']}.{pname}: нет type"


def test_all_schemas_well_formed():
    macos = load_plugin("jarvis-macos")
    core = load_plugin("jarvis-core")
    for s in macos.schemas.ALL_SCHEMAS:
        _check_schema(s)
    for s in (core.schemas.JARVIS_HUD, core.schemas.JARVIS_TIMER, core.schemas.JARVIS_MODE, core.schemas.JARVIS_UPDATE):
        _check_schema(s)


# ─────────────────────────── регистрация ───────────────────────────────────

def test_macos_register(ctx):
    macos = load_plugin("jarvis-macos")
    macos.register(ctx)
    assert len(ctx.tools) == len(macos.schemas.ALL_SCHEMAS)
    assert all(t["toolset"] == "jarvis_macos" for t in ctx.tools.values())
    assert {"screen", "vol", "sysinfo", "lock"} <= set(ctx.commands)


def test_core_register(ctx):
    core = load_plugin("jarvis-core")
    core.register(ctx)
    assert {"jarvis_hud", "jarvis_timer", "jarvis_mode", "jarvis_update"} == set(ctx.tools)
    for hook in ("pre_llm_call", "post_llm_call", "pre_tool_call", "post_tool_call", "on_session_start"):
        assert hook in ctx.hooks
    assert {"brief", "focus", "timer"} <= set(ctx.commands)
    assert {"morning-briefing", "mac-control"} <= set(ctx.skills)
    for p in ctx.skills.values():
        assert p.exists() and p.read_text().startswith("---")


# ─────────────────────────── обработчики ───────────────────────────────────

def test_handlers_always_return_json(ctx):
    """Любой обработчик на любой платформе возвращает валидный JSON и не бросает."""
    macos = load_plugin("jarvis-macos")
    for name, handler in macos.tools.HANDLERS.items():
        out = handler({}, task_id="t")  # намеренно пустые аргументы
        data = json.loads(out)
        assert "success" in data, name
        if not IS_MAC:
            assert data["success"] is False and "macOS" in data["error"], name


def test_power_requires_confirmation():
    macos = load_plugin("jarvis-macos")
    if not IS_MAC:
        pytest.skip("macOS only")
    out = json.loads(macos.tools.mac_power({"action": "shutdown"}))
    assert out["success"] is False and "подтвержд" in out["error"]


def test_applescript_disabled_by_default():
    macos = load_plugin("jarvis-macos")
    if not IS_MAC:
        pytest.skip("macOS only")
    out = json.loads(macos.tools.mac_applescript({"script": "return 1"}))
    assert out["success"] is False and "allow_raw_applescript" in out["error"]


def test_resolve_target():
    macos = load_plugin("jarvis-macos")
    r = macos.mac.resolve_target
    assert r("youtube.com") == "https://youtube.com"
    assert r("https://a.b/c") == "https://a.b/c"
    assert r("Загрузки").endswith("/Downloads")
    assert r("~/x.txt").endswith("/x.txt") and not r("~/x.txt").startswith("~")


def test_as_str_escapes():
    macos = load_plugin("jarvis-macos")
    assert macos.mac.as_str('a"b\\c') == '"a\\"b\\\\c"'


# ─────────────────────────── ядро: состояние, таймеры, контекст ────────────

def test_state_mode_and_timers():
    core = load_plugin("jarvis-core")
    st = core.state
    assert st.get_mode() == "normal"
    st.set_mode("focus")
    assert st.get_mode() == "focus"
    target = dt.datetime.now() + dt.timedelta(minutes=5)
    st.add_timer("чай", target)
    assert st.timer_alive("чай", target)
    timers = st.active_timers()
    assert timers[0]["label"] == "чай" and "мин" in timers[0]["remaining_h"]
    assert st.cancel_timer("ча") == 1
    assert st.active_timers() == []


def test_timer_tool_validation():
    core = load_plugin("jarvis-core")
    out = json.loads(core.tool_jarvis_timer({"action": "set"}))
    assert out["success"] is False
    out = json.loads(core.tool_jarvis_timer({"action": "set", "minutes": 1, "label": "тест"}))
    assert out["success"] and out["label"] == "тест"
    assert json.loads(core.tool_jarvis_timer({"action": "cancel", "label": "тест"}))["cancelled"] == 1


def test_mode_tool():
    core = load_plugin("jarvis-core")
    assert json.loads(core.tool_jarvis_mode({"mode": "bogus"}))["success"] is False
    out = json.loads(core.tool_jarvis_mode({"mode": "night"}))
    assert out["success"] and core.state.get_mode() == "night"


def test_pre_llm_context_injection(ctx):
    core = load_plugin("jarvis-core")
    core.register(ctx)
    core.state.set_mode("focus")
    res = core.hook_pre_llm_call(session_id="s1", user_message="привет")
    assert res and "[JARVIS context]" in res["context"]
    assert "focus" in res["context"]
    assert "сэр" in res["context"]


def test_hud_tool_returns_panel():
    core = load_plugin("jarvis-core")
    out = json.loads(core.tool_jarvis_hud({"kind": "video", "title": "Arc", "content": "https://youtu.be/dQw4w9WgXcQ"}))
    assert out["success"] and out["panel"]["kind"] == "video"
    assert json.loads(core.tool_jarvis_hud({"action": "clear"}))["success"]


def test_hooks_accept_kwargs(ctx):
    """Хуки должны переживать неизвестные поля (forward-compat контракт Hermes)."""
    core = load_plugin("jarvis-core")
    core.register(ctx)
    for name, fns in ctx.hooks.items():
        for fn in fns:
            fn(unknown_field=1, another="x")  # не должно падать


# ─────────────────────────── watchdog и файлы ──────────────────────────────

def test_watchdog_battery_alert(monkeypatch):
    core = load_plugin("jarvis-core")
    wd = core.Watchdog(battery_threshold=20, watch_calendar=False)
    notified = []
    monkeypatch.setattr(wd, "notify", lambda title, text: notified.append(text))
    monkeypatch.setattr(wd, "battery_state", lambda: (15, False))
    assert len(wd.tick(now=1000.0)) == 1 and "15%" in notified[0]
    # не чаще раза в час
    assert wd.tick(now=1500.0) == []
    assert len(wd.tick(now=1000.0 + 3700)) == 1
    # на зарядке — молчим
    monkeypatch.setattr(wd, "battery_state", lambda: (5, True))
    assert wd.tick(now=1000.0 + 9000) == []
    # неизвестная батарея (Linux) — молчим
    monkeypatch.setattr(wd, "battery_state", lambda: (None, False))
    assert wd.tick(now=1000.0 + 20000) == []


def test_watchdog_calendar_dedupe(monkeypatch):
    core = load_plugin("jarvis-core")
    wd = core.Watchdog(watch_calendar=True)
    monkeypatch.setattr(wd, "notify", lambda *a: None)
    monkeypatch.setattr(wd, "battery_state", lambda: (None, False))
    monkeypatch.setattr(wd, "upcoming_events", lambda: [{"title": "Standup", "start": "10:00:00"}])
    assert len(wd.tick()) == 1
    assert wd.tick() == []  # то же событие второй раз не дёргаем


def test_file_manage_safe_ops(tmp_path, monkeypatch):
    macos = load_plugin("jarvis-macos")
    monkeypatch.setattr(macos.tools.mac, "IS_MAC", True)  # логика путей платформонезависима
    f = tmp_path / "a.txt"; f.write_text("x")
    out = json.loads(macos.tools.mac_file_manage({"action": "rename", "path": str(f), "new_name": "b.txt"}))
    assert out["success"] and (tmp_path / "b.txt").exists()
    out = json.loads(macos.tools.mac_file_manage({"action": "rename", "path": str(tmp_path / "b.txt"), "new_name": "../evil"}))
    assert out["success"] is False
    out = json.loads(macos.tools.mac_file_manage({"action": "mkdir", "path": str(tmp_path / "sub")}))
    assert out["success"] and (tmp_path / "sub").is_dir()
    out = json.loads(macos.tools.mac_file_manage({"action": "move", "path": str(tmp_path / "b.txt"), "destination": str(tmp_path / "sub")}))
    assert out["success"] and (tmp_path / "sub" / "b.txt").exists()
    out = json.loads(macos.tools.mac_file_manage({"action": "list", "path": str(tmp_path)}))
    assert out["success"] and out["items"][0]["name"] == "sub"
    out = json.loads(macos.tools.mac_file_manage({"action": "trash", "path": str(tmp_path / "nope")}))
    assert out["success"] is False


def test_watchdog_focus_sync(monkeypatch, tmp_path):
    core = load_plugin("jarvis-core")
    wd = core.Watchdog(follow_focus=True)
    monkeypatch.setattr(wd, "notify", lambda *a: None)
    monkeypatch.setattr(wd, "battery_state", lambda: (None, False))
    # эмулируем ~/Library/DoNotDisturb/DB
    db = tmp_path / "Library" / "DoNotDisturb" / "DB"; db.mkdir(parents=True)
    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: tmp_path))
    (db / "ModeConfigurations.json").write_text(json.dumps({"data": [{"modeConfigurations": {
        "com.apple.focus.work": {"mode": {"name": "Работа"}}, "com.apple.sleep.sleep-mode": {"mode": {"name": "Сон"}}}}]}))
    (db / "Assertions.json").write_text(json.dumps({"data": [{"storeAssertionRecords": []}]}))
    assert wd.tick() == [] and wd.macos_focus() == ""          # фокуса нет — не событие
    (db / "Assertions.json").write_text(json.dumps({"data": [{"storeAssertionRecords": [
        {"assertionStartDateTimestamp": 1, "assertionDetails": {"assertionDetailsModeIdentifier": "com.apple.focus.work"}}]}]}))
    assert wd.tick() == ["focus:Работа->focus"] and core.state.get_mode() == "focus"
    assert wd.tick() == []                                       # без изменений — тишина
    (db / "Assertions.json").write_text(json.dumps({"data": [{"storeAssertionRecords": []}]}))
    assert wd.tick() == ["focus:->normal"] and core.state.get_mode() == "normal"
    assert wd.map_focus("Sleep") == "night" and wd.map_focus("Unknown mode") is None
    (db / "Assertions.json").unlink()
    assert wd.macos_focus() is None                              # нет доступа → None, без исключений


def test_meeting_prep_uses_brain(monkeypatch):
    core = load_plugin("jarvis-core")
    brain = load_plugin("jarvis-brain")  # регистрируется в sys.modules как *jarvis_brain
    brain.tool_brain_entity({"action": "upsert", "name": "Анна", "kind": "person", "summary": "тимлид Atlas"})
    brain.tool_brain_remember({"content": "Обещал Анне прислать смету до пятницы", "kind": "goal", "entity": "Анна", "importance": 4})
    prep = core.meeting_prep_from_brain({"title": "Синк с Анна", "start": "10:00"})
    assert prep and "Анна" in prep and "смету" in prep
    wd = core.Watchdog(watch_calendar=True, follow_focus=False, meeting_prep=core.meeting_prep_from_brain)
    notes = []
    monkeypatch.setattr(wd, "notify", lambda t, m: notes.append(m))
    monkeypatch.setattr(wd, "battery_state", lambda: (None, False))
    monkeypatch.setattr(wd, "upcoming_events", lambda: [{"title": "Синк с Анна", "start": "10:00"}])
    wd.tick()
    assert "смету" in notes[0]
    assert core.meeting_prep_from_brain({"title": "Ничего похожего zzz", "start": "1"}) is None


def test_mac_contacts_and_focus_tools_registered():
    macos = load_plugin("jarvis-macos")
    names = {s["name"] for s in macos.schemas.ALL_SCHEMAS}
    assert {"mac_contacts", "mac_focus"} <= names and set(macos.tools.HANDLERS) == names
    out = json.loads(macos.tools.mac_contacts({"action": "search"}))
    assert out["success"] is False  # нужен query (или «только macOS» на Linux) — но не исключение


def test_selftest_classifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location("selftest", "scripts/selftest.py")
    st = importlib.util.module_from_spec(spec); spec.loader.exec_module(st)
    assert st.classify({"success": True}) == ("ok", "", None)
    s, note, url = st.classify({"success": False, "error": "osascript is not allowed assistive access", "hint": "Включите Accessibility"})
    assert s == "perm" and "Универсальный доступ" in note and url.endswith("Privacy_Accessibility")
    assert st.classify({"success": False, "error": "Не удалось прочитать Focus", "hint": "нужен Full Disk Access"})[0] == "perm"
    # macOS 26 отдаёт ошибки по-русски (реальный вывод с Mac пользователя)
    s, note, _ = st.classify({"success": False, "error": "40:44: execution error: Получена ошибка от «System Events»: "
                                                       "Функции Упрощенного доступа для «osascript» не разрешены. (-1719)"})
    assert s == "perm" and "Универсальный доступ" in note
    assert st.CANDIDATES[0].name == "jarvis-macos" and "plugins" in str(st.CANDIDATES[0])
    assert st.classify({"success": False, "error": "что-то странное"})[0] == "fail"
    assert all(h in load_plugin("jarvis-macos").tools.HANDLERS for h, _, _ in st.CHECKS)


def test_osascript_errors_localized(monkeypatch):
    macos = load_plugin("jarvis-macos")
    mac = macos.tools.mac
    monkeypatch.setattr(mac, "IS_MAC", True)
    for raw in ("execution error: System Events got an error: osascript is not allowed assistive access. (-1719)",
                "40:44: execution error: Получена ошибка от «System Events»: Функции Упрощенного доступа для «osascript» не разрешены. (-1719)"):
        def boom(cmd, timeout=10, _raw=raw):
            raise mac.MacError(_raw)
        monkeypatch.setattr(mac, "run", boom)
        with pytest.raises(mac.MacError) as ei:
            mac.osascript("tell application \"System Events\" to get name of every window")
        assert "Accessibility" in str(ei.value)


def test_mac_type_rejects_unknown_modifiers(monkeypatch):
    """Модификаторы клавиш уходят в AppleScript как есть → строго allow-list, иначе это инъекция скрипта."""
    macos = load_plugin("jarvis-macos")
    mac = macos.tools.mac
    monkeypatch.setattr(mac, "IS_MAC", True)
    sent = []
    monkeypatch.setattr(macos.tools, "osascript", lambda script, **kw: sent.append(script) or "")
    out = json.loads(macos.tools.mac_type({"action": "keystroke", "text": "a", "modifiers": ["command down} \n do shell script \"rm -rf ~\" --"]}))
    assert out["success"] is False and "модификатор" in out["error"] and not sent
    out = json.loads(macos.tools.mac_type({"action": "keystroke", "text": "a", "modifiers": ["cmd", "Shift"]}))
    assert out["success"] is True and sent[-1].endswith("using {command down, shift down}")


def test_triggers_inbox_return_and_quiet(tmp_path, monkeypatch):
    core = load_plugin("jarvis-core")
    vault = tmp_path / "JARVIS"; (vault / "inbox").mkdir(parents=True)
    notes, emits, prompts = [], [], []
    mode = {"m": "normal"}
    tr = core.Triggers(state_file=tmp_path / "t.json", vault_root=vault, notify=lambda t, x: notes.append(x),
                       emit=lambda e, d: emits.append((e, d)), get_mode=lambda: mode["m"],
                       runner=lambda p: prompts.append(p) or "Это договор с Acme.")
    monkeypatch.setattr(tr, "detect_disk", lambda now: [])
    # первый тик — только запоминаем содержимое inbox, не шумим
    (vault / "inbox" / "old.md").write_text("x")
    assert tr.tick(now=1000.0, idle=0) == []
    # новый файл → событие + модель
    (vault / "inbox" / "договор.pdf").write_text("y")
    assert tr.tick(now=1100.0, idle=0) == ["vault.inbox"]
    import time as _t; _t.sleep(0.05)
    assert prompts and "договор.pdf" in prompts[0] and "Это договор с Acme." in notes[-1]
    # cooldown: ещё файл через минуту — событие есть, модель не зовём
    (vault / "inbox" / "ещё.txt").write_text("z")
    assert tr.tick(now=1160.0, idle=0) == ["vault.inbox"] and len(prompts) == 1
    # возвращение утром → брифинг (после cooldown)
    import datetime as dt
    morning = dt.datetime(2026, 9, 10, 8, 30).timestamp()
    assert tr.tick(now=morning, idle=100 * 60) == []          # ушёл
    assert tr.tick(now=morning + 5, idle=1) == ["user.returned"]
    _t.sleep(0.05); assert "брифинг" in prompts[-1]
    # в режиме focus не-срочное подавляется
    mode["m"] = "focus"
    (vault / "inbox" / "n3.txt").write_text("q")
    assert tr.tick(now=morning + 4000, idle=0) == []
    # power: отключили при 25 % → уведомление без LLM
    mode["m"] = "normal"; n0 = len(prompts)
    tr.tick(battery=(25, True), now=morning + 5000, idle=0)
    assert tr.tick(battery=(25, False), now=morning + 5060, idle=0) == ["power.unplugged"]
    assert len(prompts) == n0 and any("Питание" in n for n in notes)
    # состояние переживает рестарт: новый объект не считает старые файлы новыми
    tr2 = core.Triggers(state_file=tmp_path / "t.json", vault_root=vault, notify=lambda t, x: None, emit=lambda e, d: None, runner=lambda p: None)
    monkeypatch.setattr(tr2, "detect_disk", lambda now: [])
    assert tr2.tick(now=morning + 9000, idle=0) == []


def test_screen_context_detects_phrases_and_injects(monkeypatch):
    core = load_plugin("jarvis-core")
    for ok in ("что у меня на экране?", "посмотри сюда, что это за ошибка на экране", "Jarvis, переведи текст на экране", "what's on my screen"):
        assert core.wants_screen(ok), ok
    for no in ("какая погода", "открой экранную клавиатуру", "запомни, что экран монитора 27 дюймов"):
        assert not core.wants_screen(no), no
    monkeypatch.setattr(core, "capture_screen", lambda: "/tmp/shot.png")
    emitted = []
    monkeypatch.setattr(core._hud, "emit", lambda e, d=None: emitted.append((e, d)) or True)
    out = core.hook_pre_llm_call(session_id="s", user_message="глянь на экран, что тут написано?")
    assert "[JARVIS screen]" in out["context"] and "/tmp/shot.png" in out["context"] and "vision_analyze" in out["context"]
    assert any(e == "panel.show" for e, _ in emitted)
    # без права на запись экрана — объясняем, как выдать
    monkeypatch.setattr(core, "capture_screen", lambda: None)
    out = core.hook_pre_llm_call(session_id="s", user_message="что на экране?")
    assert "Запись экрана" in out["context"]
    # cron-ход никогда не снимает экран
    out = core.hook_pre_llm_call(session_id="cron_1", user_message="что на экране?")
    assert "[JARVIS screen]" not in out["context"]


# ─────────────────────────── нативные CLI (ical / remindctl / peekaboo) ────

def _fake_cli(monkeypatch, native, binaries: dict[str, object]):
    """Подменяем which/run/run_json: binaries = {'ical': json_or_callable, ...}. Возвращает список вызовов."""
    calls: list[list[str]] = []
    monkeypatch.setattr(native, "which", lambda name: f"/opt/homebrew/bin/{name}" if name in binaries else None)

    def run(cmd, timeout=40):
        calls.append(cmd)
        return 0, ""

    def run_json(cmd, timeout=40):
        calls.append(cmd)
        val = binaries[Path(cmd[0]).name]
        return val(cmd) if callable(val) else val

    monkeypatch.setattr(native, "run", run)
    monkeypatch.setattr(native, "run_json", run_json)
    return calls


def test_native_calendar_prefers_ical_and_falls_back(monkeypatch):
    macos = load_plugin("jarvis-macos")
    monkeypatch.setattr(macos.tools.mac, "IS_MAC", True)
    ev = [{"id": "X1", "title": "Синк", "start_date": "2026-09-12T15:00:00+03:00", "end_date": "2026-09-12T16:00:00+03:00",
           "all_day": False, "calendar": "Work"},
          {"id": "X2", "title": "День рождения", "start_date": "2026-09-12T00:00:00+03:00", "end_date": "2026-09-13T00:00:00+03:00",
           "all_day": True, "calendar": "Birthdays"}]
    calls = _fake_cli(monkeypatch, macos.native, {"ical": ev})
    out = json.loads(macos.tools.mac_calendar({"action": "on_date", "date": "2026-09-12"}))
    assert out["success"] and out["backend"] == "ical", out
    assert [e["title"] for e in out["events"]] == ["День рождения", "Синк"]  # весь день — первым
    assert out["events"][1]["start"] == "15:00" and out["events"][1]["end"] == "16:00"
    assert calls[0][1:3] == ["list", "-f"] and "-o" in calls[0]
    # без ical → старый AppleScript-путь
    monkeypatch.setattr(macos.native, "which", lambda name: None)
    monkeypatch.setattr(macos.tools, "_events_for_day", lambda d: [{"title": "AS", "start": "10:00"}])
    out = json.loads(macos.tools.mac_calendar({"action": "today"}))
    assert out["backend"] == "applescript" and out["events"][0]["title"] == "AS"


def test_native_reminders_roundtrip(monkeypatch):
    macos = load_plugin("jarvis-macos")
    monkeypatch.setattr(macos.tools.mac, "IS_MAC", True)
    items = [{"id": "R1", "title": "Позвонить маме", "listName": "Личное", "dueDate": "2026-09-12T09:00:00+03:00", "isCompleted": False}]
    calls = _fake_cli(monkeypatch, macos.native, {"remindctl": items})
    out = json.loads(macos.tools.mac_reminders({"action": "list"}))
    assert out["backend"] == "remindctl" and out["reminders"] == ["Позвонить маме"] and out["items"][0]["due"] == "2026-09-12 09:00"
    out = json.loads(macos.tools.mac_reminders({"action": "add", "title": "Купить молоко", "due": "2026-09-13 10:00", "list_name": "Дом"}))
    assert out["success"] and out["backend"] == "remindctl"
    assert calls[-1][1:3] == ["add", "Купить молоко"] and "--due" in calls[-1] and "--list" in calls[-1]
    out = json.loads(macos.tools.mac_reminders({"action": "complete", "title": "маме"}))
    assert out["success"] and out["completed"] == "Позвонить маме" and calls[-1][1:] == ["complete", "R1"]


def test_native_window_and_screenshot(monkeypatch, tmp_path):
    macos = load_plugin("jarvis-macos")
    monkeypatch.setattr(macos.tools.mac, "IS_MAC", True)
    monkeypatch.setattr(macos.tools.mac, "frontmost_app", lambda: "Safari")
    monkeypatch.setattr(macos.tools, "osascript", lambda script, **kw: "0, 0, 1440, 900")
    wins = {"data": {"windows": [{"window_id": 7, "title": "GitHub", "is_frontmost": True}]}}
    calls = _fake_cli(monkeypatch, macos.native, {"peekaboo": wins})
    out = json.loads(macos.tools.mac_window({"action": "list"}))
    assert out["backend"] == "peekaboo" and out["windows"] == ["GitHub"] and out["items"][0]["id"] == 7
    out = json.loads(macos.tools.mac_window({"action": "left_half"}))
    assert out["backend"] == "peekaboo" and calls[-1][1:3] == ["window", "set-bounds"] and calls[-1][calls[-1].index("--width") + 1] == "720"
    assert macos.tools._window_geometry("center", 1000, 1000) == (150, 150, 700, 700)
    assert macos.tools._window_geometry("zzz", 1, 1) is None
    # скриншот: peekaboo see --no-elements, файл появился → backend peekaboo
    monkeypatch.setenv("JARVIS_CACHE_DIR", str(tmp_path))

    def run_touch(cmd, timeout=40):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"png")
        return 0, ""

    monkeypatch.setattr(macos.native, "run", run_touch)
    out = json.loads(macos.tools.mac_screenshot({"mode": "front_window"}))
    assert out["success"] and out["backend"] == "peekaboo" and "frontmost" in calls[-1]


def test_native_available_and_hints():
    macos = load_plugin("jarvis-macos")
    av = macos.native.available()
    assert set(av) == {"ical", "remindctl", "peekaboo"} == set(macos.native.INSTALL_HINTS)
    assert macos.native.run_json(["definitely-not-a-binary-xyz"]) is None


def test_screenshot_ocr_via_peekaboo(monkeypatch, tmp_path):
    """ocr=true → peekaboo see --ocr, текст из ui_elements без дублей; без peekaboo — обычный путь."""
    macos = load_plugin("jarvis-macos")
    monkeypatch.setattr(macos.tools.mac, "IS_MAC", True)
    monkeypatch.setenv("JARVIS_CACHE_DIR", str(tmp_path))
    see = {"success": True, "data": {"application_name": "Xcode", "window_title": "main.swift", "element_count": 3, "ui_elements": [
        {"role": "staticText", "title": "error: cannot find 'foo'"}, {"role": "button", "label": "Build"}, {"role": "staticText", "value": "Build"}]}}
    calls = _fake_cli(monkeypatch, macos.native, {"peekaboo": see})
    out = json.loads(macos.tools.mac_screenshot({"mode": "front_window", "ocr": True}))
    assert out["backend"] == "peekaboo" and out["app"] == "Xcode" and out["lines"] == ["error: cannot find 'foo'", "Build"]
    assert "--ocr" in calls[-1] and "frontmost" in calls[-1]
    # контекст экрана в jarvis-core тоже подхватывает OCR
    core = load_plugin("jarvis-core")
    monkeypatch.setattr(core.shutil, "which", lambda n: None)
    assert core.screen_ocr("/tmp/x.png") == []


def test_triggers_push_to_hermes_webhook(tmp_path):
    """Алерт триггера уходит в webhook-маршрут Hermes (deliver_only) с подписью generic HMAC V2 — как её проверяет gateway/platforms/webhook.py."""
    import hashlib
    import hmac
    import http.server
    import threading
    core = load_plugin("jarvis-core")
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.update(path=self.path, body=body, ts=self.headers["X-Webhook-Timestamp"], sig=self.headers["X-Webhook-Signature-V2"], rid=self.headers["X-Request-ID"])
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a): pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tr = core.Triggers(state_file=tmp_path / "t.json", vault_root=tmp_path, webhook_url=f"http://127.0.0.1:{srv.server_port}/webhooks/jarvis-alerts", webhook_secret="s3cret")
        assert tr.push("disk.low", "Мало места: 12 ГБ") is True
    finally:
        srv.shutdown()
    payload = json.loads(got["body"])
    assert got["path"] == "/webhooks/jarvis-alerts" and payload["event"] == "disk.low" and "12 ГБ" in payload["text"] and got["rid"].startswith("jarvis-disk.low-")
    assert got["sig"] == hmac.new(b"s3cret", got["ts"].encode() + b"." + got["body"], hashlib.sha256).hexdigest()
    # не настроено → тихий no-op; недоступный адрес → False без исключения
    assert core.Triggers(state_file=tmp_path / "t2.json", vault_root=tmp_path).push("x", "y") is False
    assert core.Triggers(state_file=tmp_path / "t3.json", vault_root=tmp_path, webhook_url="http://127.0.0.1:1/w").push("x", "y") is False
