"""Проверки после аудита 1.7.0: пути от HERMES_HOME, защита HUD, guard'ы CLI, пересборка приложения."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── пути: нигде не зашит ~/.hermes ────────────────────────────────────────

def test_core_state_respects_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))
    monkeypatch.delenv("JARVIS_STATE_DIR", raising=False)
    state = _load(ROOT / "plugins" / "jarvis-core" / "state.py", "audit_state")
    assert str(state._path()).startswith(str(tmp_path / "custom" / "plugin-data" / "jarvis-core"))


def test_brain_db_default_respects_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "custom"))
    monkeypatch.delenv("JARVIS_BRAIN_DIR", raising=False)
    monkeypatch.delenv("JARVIS_STATE_DIR", raising=False)
    sys.path.insert(0, str(ROOT / "plugins" / "jarvis-brain"))
    try:
        db = _load(ROOT / "plugins" / "jarvis-brain" / "db.py", "audit_db")
    finally:
        sys.path.pop(0)
    assert db.Brain._default_path() == tmp_path / "custom" / "plugin-data" / "jarvis-brain" / "brain.db"


def test_macos_cache_dir_respects_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("JARVIS_CACHE_DIR", raising=False)
    mac = _load(ROOT / "plugins" / "jarvis-macos" / "mac.py", "audit_mac")
    assert mac.cache_dir("screenshots") == tmp_path / "h" / "cache" / "jarvis" / "screenshots"
    assert (tmp_path / "h" / "cache" / "jarvis" / "screenshots").is_dir()


def test_hud_server_paths_from_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    monkeypatch.delenv("JARVIS_BRAIN_DB", raising=False)
    (tmp_path / "hh").mkdir()
    (tmp_path / "hh" / ".env").write_text("API_SERVER_KEY=secret123 # comment\n")
    sys.path.insert(0, str(ROOT / "hud"))
    try:
        srv = _load(ROOT / "hud" / "server.py", "audit_hud_server")
    finally:
        sys.path.pop(0)
    assert srv.CONFIG["brain_db"] == str(tmp_path / "hh" / "plugin-data" / "jarvis-brain" / "brain.db")
    assert srv.CONFIG["allowed_file_roots"][0] == str(tmp_path / "hh" / "cache")
    assert srv._load_env_key() == "secret123"


def test_no_hardcoded_hermes_home_in_code():
    """В коде (не в документации) путь ~/.hermes допустим только как значение по умолчанию рядом с HERMES_HOME."""
    offenders = []
    for p in list(ROOT.glob("plugins/**/*.py")) + list(ROOT.glob("hud/*.py")) + list(ROOT.glob("scripts/*.py")) + list(ROOT.glob("hooks/**/*.py")):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if "~/.hermes" in line and "HERMES_HOME" not in line and not line.lstrip().startswith("#"):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


# ── HUD ──────────────────────────────────────────────────────────────────

def test_hud_rejects_huge_body(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    import threading
    sys.path.insert(0, str(ROOT / "hud"))
    try:
        srv_mod = _load(ROOT / "hud" / "server.py", "audit_hud_server2")
    finally:
        sys.path.pop(0)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), srv_mod.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/api/event"
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", "Content-Length": "5000000"}, method="POST")
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(req, timeout=3)
        assert ei.value.code == 400  # тело отброшено → «event required»
    finally:
        srv.shutdown()


# ── CLI bin/jarvis ────────────────────────────────────────────────────────

def _jarvis(*args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run(["bash", str(ROOT / "bin" / "jarvis"), *args], capture_output=True, text=True, env=e)


def test_cli_help_and_version_work_without_install(tmp_path):
    r = _jarvis("help", env={"HERMES_HOME": str(tmp_path / "missing")})
    assert r.returncode == 0 and "jarvis hud" in r.stdout
    r = _jarvis("version", env={"HERMES_HOME": str(tmp_path / "missing"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "JARVIS ?" in r.stdout


def test_cli_fails_clearly_when_hermes_home_missing(tmp_path):
    r = _jarvis("status", env={"HERMES_HOME": str(tmp_path / "missing")})
    assert r.returncode == 1 and "HERMES_HOME" in r.stderr


def test_cli_brain_without_db_is_friendly(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _jarvis("brain", "backup", env={"HERMES_HOME": str(home)})
    assert r.returncode == 1 and "базы ещё нет" in r.stderr
    r = _jarvis("brain", "restore", env={"HERMES_HOME": str(home)})
    assert r.returncode == 1 and "бэкапов нет" in r.stderr


def test_cli_ask_requires_question(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _jarvis("ask", env={"HERMES_HOME": str(home)})
    assert r.returncode == 1 and "использование" in r.stderr


# ── updater ──────────────────────────────────────────────────────────────

def test_rebuild_app_is_noop_without_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    u = _load(ROOT / "scripts" / "update.py", "audit_updater")
    monkeypatch.setattr(u.sys, "platform", "darwin")
    monkeypatch.setattr(u.Path, "home", classmethod(lambda cls: tmp_path))
    assert u.rebuild_app() == ""  # нет ~/Applications/JARVIS.app → ничего не делаем


def test_restart_services_survives_missing_binaries(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "/nonexistent")
    u = _load(ROOT / "scripts" / "update.py", "audit_updater2")
    monkeypatch.setattr(u.sys, "platform", "darwin")
    monkeypatch.setattr(u.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(u.Path, "home", classmethod(lambda cls: tmp_path))
    u.restart_services()  # не должно бросать, даже если launchctl/hermes отсутствуют


# ── конфиг/плагины ───────────────────────────────────────────────────────

def test_plugin_yaml_defaults_do_not_pin_hermes_home():
    import yaml
    for name in ("jarvis-core", "jarvis-macos", "jarvis-brain"):
        y = yaml.safe_load((ROOT / "plugins" / name / "plugin.yaml").read_text())
        for key, spec in (y.get("config_schema") or {}).items():
            assert "~/.hermes" not in str(spec.get("default", "")), f"{name}.{key} зашивает ~/.hermes"


def test_install_json_written_by_installer_snippet(tmp_path):
    """Фрагмент install.sh, пишущий install.json, сохраняет канал/режим и добавляет версию."""
    text = (ROOT / "install.sh").read_text()
    start = text.index("<<'PY'") + len("<<'PY'\n")
    snippet = text[start:text.index("\nPY\n", start)]
    p = tmp_path / "install.json"
    p.write_text(json.dumps({"channel": "main", "auto_update": "auto", "commit": "old"}))
    subprocess.run([sys.executable, "-", str(p), "1.7.0", "", "x/y", "", ""], input=snippet, text=True, check=True)
    d = json.loads(p.read_text())
    assert d["version"] == "1.7.0" and d["channel"] == "main" and d["auto_update"] == "auto" and d["commit"] == "old"
