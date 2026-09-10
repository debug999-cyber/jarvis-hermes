"""HUD 2.0: виджеты на реальных данных — /api/dashboard, таймеры, режим, replay панелей."""

from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hud"))
import server as hud  # noqa: E402
import sysinfo  # noqa: E402


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sysinfo, "STATE_FILE", tmp_path / "state.json")
    return tmp_path


@pytest.fixture(scope="module")
def srv():
    hud.DASH = sysinfo.Collector(demo=True)
    s = ThreadingHTTPServer(("127.0.0.1", 0), hud.Handler)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, json.loads(r.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read())


def test_dashboard_demo_has_all_widgets(srv):
    status, d = _get(srv + "/api/dashboard")
    assert status == 200
    assert d["battery"]["percent"] == 82 and d["focus"]["active"]
    assert len(d["calendar"]) == 3 and d["brain"]["notes"] == 214
    assert d["demo"] is True


def test_timers_shared_state_file(state_dir):
    """HUD и плагин jarvis-core пишут в один state.json с одинаковой схемой."""
    target = dt.datetime.now() + dt.timedelta(minutes=5)
    sysinfo.add_timer("чай", target)
    raw = json.loads((state_dir / "state.json").read_text())
    assert raw["timers"] == [{"label": "чай", "target": target.isoformat()}]
    alive = sysinfo.timers()
    assert alive[0]["label"] == "чай" and 290 <= alive[0]["seconds"] <= 300
    assert sysinfo.cancel_timer("чай", target.isoformat()) == 1
    assert sysinfo.timers() == []


def test_timer_api_set_and_cancel(srv, state_dir):
    status, r = _post(srv + "/api/timer", {"action": "set", "minutes": 3, "label": "тест"})
    assert status == 200 and r["ok"] and r["label"] == "тест"
    assert any(t["label"] == "тест" for t in sysinfo.timers())
    status, r = _post(srv + "/api/timer", {"action": "cancel", "label": "тест", "target": r["target"]})
    assert r["cancelled"] == 1


def test_timer_api_rejects_garbage(srv, state_dir):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(srv + "/api/timer", {"action": "set", "minutes": 0})
    assert e.value.code == 400


def test_mode_api_writes_state(srv, state_dir):
    status, r = _post(srv + "/api/mode", {"mode": "focus"})
    assert r["mode"] == "focus" and sysinfo.get_mode() == "focus"
    _post(srv + "/api/mode", {"mode": "garbage"})
    assert sysinfo.get_mode() == "normal"


def test_timer_watcher_fires_once(state_dir):
    fired = []
    sysinfo.add_timer("past", dt.datetime.now() - dt.timedelta(seconds=10))
    w = sysinfo.TimerWatcher(fired.append)
    # прогоняем один цикл руками, без потока
    for t in json.loads((state_dir / "state.json").read_text())["timers"]:
        if sysinfo.cancel_timer(t["label"], t["target"]):
            w.on_fire(t["label"])
    assert fired == ["past"] and sysinfo.timers() == []


def test_subscribe_replays_only_sticky_panels():
    bus = hud.EventBus()
    bus.publish({"event": "tool.start", "data": {"tool": "x"}})
    bus.publish({"event": "panel.show", "data": {"kind": "text", "content": "old", "position": "center"}})
    bus.publish({"event": "panel.show", "data": {"kind": "text", "content": "new", "position": "center"}})
    bus.publish({"event": "panel.show", "data": {"kind": "text", "content": "tmp", "position": "right", "ttl": 30}})
    bus.publish({"event": "panel.show", "data": {"id": "boot", "kind": "markdown", "content": "a", "position": "right"}})
    bus.publish({"event": "panel.show", "data": {"id": "boot", "kind": "markdown", "content": "b", "position": "right"}})
    bus.publish({"event": "mode.set", "data": {"mode": "focus"}})
    q = bus.subscribe()
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    names = [(i["event"], i["data"].get("content") or i["data"].get("mode")) for i in items]
    assert names == [("mode.set", "focus"), ("panel.show", "new"), ("panel.show", "b")]
    assert all(i.get("replay") for i in items)


def test_panel_clear_drops_replay():
    bus = hud.EventBus()
    bus.publish({"event": "panel.show", "data": {"kind": "text", "content": "x"}})
    bus.publish({"event": "panel.clear", "data": {}})
    assert bus.subscribe().empty()


def test_hermes_model_parser(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("plugins:\n  enabled: []\nmodel:\n  default: anthropic/claude-sonnet-4.5  # комментарий\n  fallback: x\n")
    monkeypatch.setattr(sysinfo, "CONFIG_YAML", cfg)
    assert sysinfo.hermes_model() == "anthropic/claude-sonnet-4.5"
    cfg.write_text('model: "gpt-4o"\n')
    assert sysinfo.hermes_model() == "gpt-4o"


def test_hush_endpoint_publishes_speech_stop(srv, monkeypatch):
    monkeypatch.setattr(hud.sys, "platform", "linux")  # без pkill
    q = hud.BUS.subscribe()
    status, r = _post(srv + "/api/hush", {})
    assert status == 200 and r["ok"]
    events = []
    while not q.empty():
        events.append(q.get_nowait()["event"])
    hud.BUS.unsubscribe(q)
    assert "speech.stop" in events


def test_plugin_turn_events_dropped_while_proxy_chat_in_flight(srv):
    """Пока идёт чат через /api/chat, дубли turn.*/stream.* от плагина отбрасываются."""
    hud._PROXY_TURNS = 1
    try:
        _, r = _post(srv + "/api/event", {"event": "stream.delta", "data": {"delta": "x"}})
        assert r.get("dropped")
        _, r = _post(srv + "/api/event", {"event": "turn.end", "data": {"text": "bg", "source": "cron"}})
        assert not r.get("dropped")  # фоновые задачи проходят
        _, r = _post(srv + "/api/event", {"event": "tool.start", "data": {"tool": "x"}})
        assert not r.get("dropped")  # инструменты проходят
    finally:
        hud._PROXY_TURNS = 0
    _, r = _post(srv + "/api/event", {"event": "stream.delta", "data": {"delta": "x"}})
    assert not r.get("dropped")
