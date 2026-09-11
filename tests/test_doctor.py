"""jarvis doctor: не падает без Hermes, JSON-контракт, автопочинка .env."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("jarvis_doctor", ROOT / "scripts" / "doctor.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jarvis_doctor"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_no_hermes_is_single_fail(monkeypatch, tmp_path, capsys):
    d = load(monkeypatch, tmp_path)
    monkeypatch.setattr(d.shutil, "which", lambda *_: None)
    monkeypatch.setattr(d, "BIN", tmp_path / "nobin")
    assert d.main(["--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["checks"][0]["status"] == "fail" and "install.sh" in out["checks"][0]["fix"]


def test_env_fix_writes_keys(monkeypatch, tmp_path):
    d = load(monkeypatch, tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=x\n")
    c = d.check_env_api(fix=False)
    assert c.status == "fail" and "API_SERVER_KEY" in c.note
    c = d.check_env_api(fix=True)
    assert c.fixed
    env = d.read_env()
    assert env["API_SERVER_ENABLED"] == "true" and len(env["API_SERVER_KEY"]) >= 32 and env["OPENROUTER_API_KEY"] == "x"
    assert d.check_env_api(fix=False).status == "ok"


def test_version_check_reads_update_json(monkeypatch, tmp_path):
    d = load(monkeypatch, tmp_path)
    (tmp_path / "jarvis").mkdir()
    (tmp_path / "jarvis" / "install.json").write_text(json.dumps({"version": "1.8.0", "channel": "stable"}))
    assert d.check_version(False).status == "ok"
    (tmp_path / "jarvis" / "update.json").write_text(json.dumps({"available": True, "latest": "1.9.0"}))
    c = d.check_version(False)
    assert c.status == "warn" and "1.9.0" in c.note and c.fix_hint == "jarvis update"


def test_memory_check_states(monkeypatch, tmp_path):
    d = load(monkeypatch, tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("memory:\n  enabled: true\n")
    c = d.check_memory(False)
    assert c.status == "warn" and "holographic" in c.fix_hint
    cfg.write_text("memory:\n  provider: holographic\n")
    assert d.check_memory(False).status == "ok"
    import sqlite3
    con = sqlite3.connect(tmp_path / "memory_store.db")
    con.execute("CREATE TABLE facts(fact_id INTEGER PRIMARY KEY, content TEXT)"); con.execute("INSERT INTO facts(content) VALUES ('x')"); con.commit(); con.close()
    c = d.check_memory(False)
    assert c.status == "ok" and "фактов: 1" in c.note
