"""Регистрация плагинов, валидность схем, поведение обработчиков вне macOS."""

from __future__ import annotations

import datetime as dt
import json
import platform
from pathlib import Path

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
    for s in (core.schemas.JARVIS_HUD, core.schemas.JARVIS_TIMER, core.schemas.JARVIS_MODE, core.schemas.JARVIS_WEATHER):
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
    assert {"jarvis_hud", "jarvis_timer", "jarvis_mode", "jarvis_weather", "jarvis_update"} == set(ctx.tools)
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
