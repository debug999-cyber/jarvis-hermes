"""Тесты updater'а: сравнение версий, check по фиктивному API, бэкап/откат, apply с проваленным install.sh → откат."""
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest


def load(tmp_home: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_home))
    spec = importlib.util.spec_from_file_location("jarvis_updater", "scripts/update.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "notify", lambda *a, **k: None)
    monkeypatch.setattr(mod, "restart_services", lambda: None)
    return mod


def fake_install(home: Path, version="1.3.1"):
    (home / "jarvis").mkdir(parents=True, exist_ok=True)
    (home / "jarvis" / "install.json").write_text(json.dumps({"version": version, "commit": "abc", "repo": "x/y", "channel": "stable", "auto_update": "check"}))
    (home / "jarvis" / "VERSION").write_text(version)
    for plug in ("jarvis-core", "jarvis-macos", "jarvis-brain"):
        (home / "plugins" / plug).mkdir(parents=True, exist_ok=True)
        (home / "plugins" / plug / "__init__.py").write_text(f"# {plug} {version}\n")
    (home / "SOUL.md").write_text(f"soul {version}")
    (home / "config.yaml").write_text("model: x\n")


def test_version_compare(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    assert u.parse_version("v1.10.0") > u.parse_version("1.9.9")
    assert u.is_newer("1.4.0", "1.3.1", "stable")
    assert not u.is_newer("1.3.1", "1.3.1", "stable")
    assert u.is_newer("", "1.3.1", "main", latest_commit="def", current_commit="abc")
    assert not u.is_newer("", "1.3.1", "main", latest_commit="abc", current_commit="abc")


def test_check_uses_release_then_falls_back_to_main(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    fake_install(tmp_path)
    calls = []

    def fake_http(url, timeout=15):
        calls.append(url)
        if "releases/latest" in url:
            return None  # релизов ещё нет
        if "/commits/main" in url:
            return {"sha": "deadbeef" * 5, "commit": {"message": "feat: x"}}
        return None
    monkeypatch.setattr(u, "http_json", fake_http)

    monkeypatch.setattr(u, "fetch_bytes", lambda url, timeout=10: b"1.4.0\n")  # raw VERSION
    r = u.check()
    assert r["available"] and r["latest"] == "1.4.0" and r["channel"] == "main" and not r["error"]
    assert json.loads((tmp_path / "jarvis" / "update.json").read_text())["available"]
    # нет сети → error, но файл не ломается и available не выдумывается
    monkeypatch.setattr(u, "http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    r2 = u.check()
    assert r2["error"] and "available" in r2


def test_backup_and_rollback(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    fake_install(tmp_path, "1.3.1")
    b = u.make_backup("1.3.1")
    assert (b / "plugins" / "jarvis-core" / "__init__.py").exists() and (b / "SOUL.md").exists()
    # «обновление» испортило файлы
    (tmp_path / "plugins" / "jarvis-core" / "__init__.py").write_text("broken")
    (tmp_path / "SOUL.md").write_text("broken")
    (tmp_path / "jarvis" / "install.json").write_text(json.dumps({"version": "9.9.9"}))
    r = u.rollback()
    assert r["rolled_back"] and r["to"] == "1.3.1"
    assert "1.3.1" in (tmp_path / "plugins" / "jarvis-core" / "__init__.py").read_text()
    assert (tmp_path / "SOUL.md").read_text() == "soul 1.3.1"
    assert u.installed()["version"] == "1.3.1"
    # хранится не больше 3 бэкапов
    for i in range(5):
        u.make_backup(f"t{i}")
    assert len([p for p in (tmp_path / "jarvis" / "backups").iterdir() if p.is_dir()]) == 3


def make_tarball(dst: Path, version: str, install_body: str) -> Path:
    src = dst / "src" / f"repo-{version}"
    (src / "plugins").mkdir(parents=True)
    (src / "VERSION").write_text(version)
    (src / "plugins" / "ok.py").write_text("x = 1\n")
    (src / "install.sh").write_text("#!/usr/bin/env bash\n" + install_body)
    tar = dst / "src.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(src, arcname=src.name)
    return tar


def test_apply_success_and_failed_install_rolls_back(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    fake_install(tmp_path, "1.3.1")
    # подменяем скачивание: читаем локальный tar.gz
    def fake_download(tarball, workdir):
        with tarfile.open(tarball) as tf:
            tf.extractall(workdir, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(workdir)
        return next(p for p in workdir.iterdir() if p.is_dir())
    monkeypatch.setattr(u, "download_and_extract", fake_download)
    good = make_tarball(tmp_path / "good", "1.4.0",
                        'echo "# jarvis-core 1.4.0" > "$HERMES_HOME/plugins/jarvis-core/__init__.py"\n'
                        'python3 -c "import json,sys;p=\'$HERMES_HOME/jarvis/install.json\';d=json.load(open(p));d[\'version\']=\'1.4.0\';json.dump(d,open(p,\'w\'))"\n')
    r = u.apply(tarball=str(good), version="1.4.0")
    assert r["updated"] and r["to"] == "1.4.0" and u.installed()["version"] == "1.4.0"
    assert u.installed()["previous_version"] == "1.3.1"
    assert "1.4.0" in (tmp_path / "plugins" / "jarvis-core" / "__init__.py").read_text()

    bad = make_tarball(tmp_path / "bad", "1.5.0", 'echo "# jarvis-core BROKEN" > "$HERMES_HOME/plugins/jarvis-core/__init__.py"\nexit 3\n')
    with pytest.raises(RuntimeError, match="откат"):
        u.apply(tarball=str(bad), version="1.5.0")
    assert u.installed()["version"] == "1.4.0"
    assert "1.4.0" in (tmp_path / "plugins" / "jarvis-core" / "__init__.py").read_text()


def test_smoke_test_rejects_broken_python(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    src = tmp_path / "s"; src.mkdir()
    (src / "VERSION").write_text("1.0.0"); (src / "install.sh").write_text("true\n")
    (src / "bad.py").write_text("def (:\n")
    with pytest.raises(Exception):
        u.smoke_test(src)


def test_set_options_and_status(tmp_path, monkeypatch):
    u = load(tmp_path, monkeypatch)
    fake_install(tmp_path)
    u.set_option("auto", "auto"); u.set_option("channel", "main")
    s = u.status()
    assert s["auto_update"] == "auto" and s["channel"] == "main" and s["version"] == "1.3.1"
    with pytest.raises(AssertionError):
        u.set_option("auto", "sometimes")
    # auto: off → пропуск без сети; check при недоступной сети → error, без исключений
    u.set_option("auto", "off")
    assert u.auto() == {"skipped": "auto_update=off"}
    u.set_option("auto", "check")
    monkeypatch.setattr(u, "http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert u.auto()["error"]


def test_jarvis_update_tool(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, "tests")
    from conftest import load_plugin
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    core = load_plugin("jarvis-core")
    st = json.loads(core.tool_jarvis_update({"action": "status"}))
    assert st["success"] and st["version"] == "?"          # ничего не установлено — не падаем
    r = json.loads(core.tool_jarvis_update({"action": "apply"}))
    assert r["success"] is False and "updater не установлен" in r["error"]
    fake_install(tmp_path, "1.3.1")
    (tmp_path / "jarvis" / "update.py").write_text(Path("scripts/update.py").read_text())
    (tmp_path / "jarvis" / "update.json").write_text(json.dumps({"available": True, "latest": "1.4.0", "checked_at": "2026-09-10T10:00:00"}))
    r = json.loads(core.tool_jarvis_update({"action": "apply"}))
    assert r["needs_confirmation"] and r["success"] is False   # без confirmed не ставим
    assert "Доступно обновление JARVIS 1.4.0" in core.build_context()
    r = json.loads(core.tool_jarvis_update({"action": "set_auto", "value": "auto"}))
    assert r["success"] and r["auto_update"] == "auto"


def test_fetch_bytes_falls_back_to_curl_when_urllib_cannot_resolve(tmp_path, monkeypatch):
    """Кейс из жизни: urllib на macOS берёт прокси из системы и падает с [Errno 8] nodename nor servname — curl работает."""
    import socket
    import urllib.error
    u = load(tmp_path, monkeypatch)

    class BadOpener:
        def open(self, req, timeout=0):
            raise urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))
    monkeypatch.setattr(u.urllib.request, "build_opener", lambda *h: BadOpener())
    calls = []

    class P:
        returncode, stdout, stderr = 0, b'{"tag_name": "v9.9.9"}', b""
    monkeypatch.setattr(u.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or P())
    assert u.http_json("https://api.github.com/repos/x/y/releases/latest") == {"tag_name": "v9.9.9"}
    assert calls and calls[0][0].endswith("curl") and "-fsSL" in calls[0]
    # curl тоже не смог → понятная подсказка про прокси/VPN и --from
    monkeypatch.setattr(u.shutil, "which", lambda n: None)
    with pytest.raises(RuntimeError, match="Прокси|прокси") as ei:
        u.fetch_bytes("https://api.github.com/x")
    assert "--from" in str(ei.value)


def test_apply_from_local_folder_and_zip(tmp_path, monkeypatch):
    """jarvis update --from <папка|zip>: обновление без GitHub API (нет сети / прокси мешает)."""
    import zipfile
    u = load(tmp_path, monkeypatch)
    fake_install(tmp_path, "1.9.0")
    src = tmp_path / "proj"; src.mkdir()
    (src / "VERSION").write_text("1.10.1\n")
    (src / "install.sh").write_text('#!/bin/bash\npython3 -c "import json;p=\'$HERMES_HOME/jarvis/install.json\';d=json.load(open(p));d[\'version\']=\'1.10.1\';json.dump(d,open(p,\'w\'))"\n')
    r = u.apply(tarball=str(src))
    assert r["updated"] and r["to"] == "1.10.1" and "1.10.1" in r["log"][0]
    # zip (как «Download ZIP» на GitHub — с корневой папкой)
    fake_install(tmp_path, "1.9.0")
    z = tmp_path / "jarvis-hermes-main.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in ("VERSION", "install.sh"):
            zf.write(src / f, f"jarvis-hermes-main/{f}")
    assert u.apply(tarball=str(z))["to"] == "1.10.1"
    with pytest.raises(RuntimeError, match="подозрительный"):
        zz = tmp_path / "evil.zip"
        with zipfile.ZipFile(zz, "w") as zf:
            zf.writestr("../evil.sh", "x")
        u.download_and_extract(str(zz), tmp_path / "w")
