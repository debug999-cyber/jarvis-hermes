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


def test_tts_endpoint_falls_back_and_serves_audio(srv, monkeypatch):
    import tts
    # без движка → 204 и заголовок с именем движка: HUD озвучит браузером
    monkeypatch.setattr(tts, "synthesize", lambda text: None)
    monkeypatch.setattr(tts, "engine", lambda: "none")
    req = urllib.request.Request(srv + "/api/tts", data='{"text":"Слушаю, сэр"}'.encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": srv})
    with urllib.request.urlopen(req) as r:
        assert r.status == 204 and r.headers["X-JARVIS-TTS"] == "none"
    # с движком → байты и mime
    monkeypatch.setattr(tts, "synthesize", lambda text: (b"ID3fake", "audio/mpeg"))
    with urllib.request.urlopen(req) as r:
        assert r.status == 200 and r.headers["Content-Type"] == "audio/mpeg" and r.read() == b"ID3fake"
    # пустой текст → 400
    bad = urllib.request.Request(srv + "/api/tts", data=b'{"text":"  "}', method="POST",
                                 headers={"Content-Type": "application/json", "Origin": srv})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(bad)
    assert e.value.code == 400


def test_tts_clean_and_cache(tmp_path, monkeypatch):
    import tts
    assert tts.clean("**Готово**, см. https://a.b/c и `код`") == "Готово, см. ссылка и код"
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path / "tts")
    monkeypatch.setattr(tts, "engine", lambda: "edge-tts-cli")
    calls = []

    def fake_cli(text, voice, speed, out):
        calls.append(text); out.write_bytes(b"mp3"); return True
    monkeypatch.setattr(tts, "_edge_cli", fake_cli)
    monkeypatch.setattr(tts.shutil, "which", lambda n: "/usr/bin/edge-tts" if n == "edge-tts" else None)
    assert tts.synthesize("Привет") == (b"mp3", "audio/mpeg")
    assert tts.synthesize("Привет") == (b"mp3", "audio/mpeg")
    assert calls == ["Привет"], "второй вызов должен прийти из кэша"


def test_timer_extend_api(srv, state_dir):
    """Кнопки +5/+15 на HUD: action=extend сдвигает target общего state.json; неизвестный таймер → 404."""
    import json as _j
    import urllib.request
    def post(body):
        req = urllib.request.Request(srv + "/api/timer", data=_j.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, _j.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, _j.loads(e.read())
    st, made = post({"action": "set", "minutes": 10, "label": "Чай"})
    assert st == 200
    st, out = post({"action": "extend", "label": "Чай", "target": made["target"], "minutes": 5})
    assert st == 200 and out["extended"] == 1
    import datetime as dt
    timers = _j.loads((state_dir / "state.json").read_text())["timers"]
    assert dt.datetime.fromisoformat(timers[0]["target"]) - dt.datetime.fromisoformat(made["target"]) == dt.timedelta(minutes=5)
    assert post({"action": "extend", "label": "Нет такого", "minutes": 5})[0] == 404


def test_hud_has_no_dead_elements():
    """Всё, что выглядит кликабельным на HUD, имеет обработчик (аудит 1.10.1): календарь, плитки знаний, батарея/модель, шапка, +5/+15."""
    html = (ROOT / "hud" / "static" / "index.html").read_text()
    for needle in ["b.querySelectorAll('.row.act')", "b.querySelectorAll('.stat.act')", "$('#battRow').onclick", "$('#modelRow').onclick",
                   "$('#modeChip').onclick", "$('#status').onclick", "$('#clock').onclick", "b.querySelectorAll('.ext')",
                   "action:'extend'", ".tag.act", "li.act[data-file]", ".row.act,.stat.act,.chip.act,.tag.act,#status,#clock.act{cursor:pointer}"]:
        assert needle in html, needle
