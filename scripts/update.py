#!/usr/bin/env python3
"""
JARVIS updater — проверка и установка обновлений с GitHub без сторонних зависимостей.

    update.py check [--notify] [--json]   узнать, есть ли новая версия (пишет update.json)
    update.py apply [--force]             скачать → бэкап → install.sh → перезапуск сервисов
    update.py auto                        то, что делает ежедневный демон: check, затем apply, если режим auto
    update.py rollback                    откатиться на предыдущую установку
    update.py status [--json]             текущая версия, канал, режим, последняя проверка
    update.py set channel stable|main     откуда брать обновления: релизы GitHub или ветка main
    update.py set auto off|check|auto     режим автообновления

Файлы (в $HERMES_HOME/jarvis):
    install.json   что установлено: version, commit, repo, channel, auto_update, installed_at
    update.json    результат последней проверки: available, latest, notes, url
    backups/       копии предыдущих установок (хранится 3)

Принципы: никогда не ломать работающую установку — сначала полный бэкап, установка во временную папку,
проверка синтаксиса всех плагинов, и только потом замена; при любой ошибке — автоматический откат.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
JARVIS_HOME = HERMES_HOME / "jarvis"
INSTALL_JSON = JARVIS_HOME / "install.json"
UPDATE_JSON = JARVIS_HOME / "update.json"
BACKUPS = JARVIS_HOME / "backups"
DEFAULT_REPO = "debug999-cyber/jarvis-hermes"
KEEP_BACKUPS = 3
UA = {"User-Agent": "jarvis-updater", "Accept": "application/vnd.github+json"}
PLUGINS = ("jarvis-core", "jarvis-macos", "jarvis-brain")


# ───────────────────────── утилиты ─────────────────────────
def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def read_json(p: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return dict(default or {})


def write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(p)


def parse_version(v: str) -> tuple:
    """'v1.4.0' → (1, 4, 0); нечисловые хвосты игнорируются, чтобы '1.4.0-beta' < '1.4.0' не ломало сравнение."""
    nums = re.findall(r"\d+", v or "0")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str, channel: str, latest_commit: str = "", current_commit: str = "") -> bool:
    if channel == "main":
        return bool(latest_commit) and latest_commit != current_commit
    return parse_version(latest) > parse_version(current)


def http_json(url: str, timeout: int = 15) -> dict | None:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def notify(title: str, text: str) -> None:
    """Уведомление macOS + событие на HUD (оба — best effort)."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}"'],
                       capture_output=True, timeout=5)
    try:
        payload = json.dumps({"event": "alert", "data": {"kind": "update", "text": f"{title}: {text}"}}).encode()
        req = urllib.request.Request(os.environ.get("JARVIS_HUD_URL", "http://127.0.0.1:8765") + "/api/event",
                                     data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:  # HUD может быть выключен
        pass


def installed() -> dict:
    d = read_json(INSTALL_JSON)
    d.setdefault("version", "0.0.0")
    d.setdefault("commit", "")
    d.setdefault("repo", DEFAULT_REPO)
    d.setdefault("channel", "stable")
    d.setdefault("auto_update", "check")
    return d


# ───────────────────────── проверка ─────────────────────────
def fetch_latest(repo: str, channel: str) -> dict:
    """Что лежит на GitHub: {'version','commit','notes','tarball','url','channel'}. Канал stable без релизов → main."""
    if channel == "stable":
        rel = http_json(f"https://api.github.com/repos/{repo}/releases/latest")
        if rel:
            return {"version": rel["tag_name"].lstrip("v"), "commit": rel.get("target_commitish", ""),
                    "notes": (rel.get("body") or "")[:1500], "tarball": rel["tarball_url"],
                    "url": rel["html_url"], "channel": "stable"}
        channel = "main"  # релизов ещё нет — берём ветку
    commit = http_json(f"https://api.github.com/repos/{repo}/commits/main")
    if not commit:
        raise RuntimeError(f"репозиторий {repo} недоступен или пуст")
    sha = commit["sha"]
    # VERSION из ветки, чтобы показать человеку номер, а не хеш
    version = ""
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://raw.githubusercontent.com/{repo}/{sha}/VERSION", headers=UA), timeout=10) as r:
            version = r.read().decode().strip()
    except Exception:
        version = sha[:7]
    return {"version": version, "commit": sha, "notes": commit["commit"]["message"][:1500],
            "tarball": f"https://github.com/{repo}/archive/{sha}.tar.gz",
            "url": f"https://github.com/{repo}/commit/{sha}", "channel": "main"}


def check(do_notify: bool = False) -> dict:
    cur = installed()
    try:
        latest = fetch_latest(cur["repo"], cur["channel"])
        available = is_newer(latest["version"], cur["version"], latest["channel"], latest["commit"], cur["commit"])
        result = {"checked_at": now_iso(), "current": cur["version"], "current_commit": cur["commit"],
                  "latest": latest["version"], "latest_commit": latest["commit"], "available": available,
                  "notes": latest["notes"], "url": latest["url"], "tarball": latest["tarball"],
                  "channel": latest["channel"], "error": ""}
    except Exception as e:  # нет сети и т. п.
        result = {**read_json(UPDATE_JSON), "checked_at": now_iso(), "current": cur["version"], "error": str(e)[:200]}
        result.setdefault("available", False)
    write_json(UPDATE_JSON, result)
    if do_notify and result.get("available") and not result.get("error"):
        notify("JARVIS", f"Доступно обновление {result['latest']}. Скажите «обнови себя» или jarvis update")
    return result


# ───────────────────────── бэкап / откат ─────────────────────────
def _copytree(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)


def make_backup(tag: str) -> Path:
    """Снимок всего, что меняет install.sh: плагины, навыки, хук, HUD и служебные файлы, SOUL.md, config.yaml."""
    dest = BACKUPS / f"{tag}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    dest.mkdir(parents=True, exist_ok=True)
    for plug in PLUGINS:
        _copytree(HERMES_HOME / "plugins" / plug, dest / "plugins" / plug)
    _copytree(HERMES_HOME / "skills" / "jarvis", dest / "skills" / "jarvis")
    _copytree(HERMES_HOME / "hooks" / "jarvis-boot", dest / "hooks" / "jarvis-boot")
    for item in JARVIS_HOME.iterdir():
        if item.name in ("backups", "hud.pid") or item.suffix in (".log", ".pid"):
            continue
        if item.is_dir():
            _copytree(item, dest / "jarvis" / item.name)
        else:
            (dest / "jarvis").mkdir(exist_ok=True)
            shutil.copy2(item, dest / "jarvis" / item.name)
    for f in ("SOUL.md", "config.yaml", "BOOT.md"):
        if (HERMES_HOME / f).exists():
            shutil.copy2(HERMES_HOME / f, dest / f)
    if (HERMES_HOME / "skill-bundles" / "jarvis.yaml").exists():
        (dest / "skill-bundles").mkdir(exist_ok=True)
        shutil.copy2(HERMES_HOME / "skill-bundles" / "jarvis.yaml", dest / "skill-bundles" / "jarvis.yaml")
    # чистим старые
    olds = sorted(p for p in BACKUPS.iterdir() if p.is_dir())
    for old in olds[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return dest


def restore_backup(src: Path) -> None:
    for plug in PLUGINS:
        if (src / "plugins" / plug).exists():
            shutil.rmtree(HERMES_HOME / "plugins" / plug, ignore_errors=True)
            _copytree(src / "plugins" / plug, HERMES_HOME / "plugins" / plug)
    if (src / "skills" / "jarvis").exists():
        shutil.rmtree(HERMES_HOME / "skills" / "jarvis", ignore_errors=True)
        _copytree(src / "skills" / "jarvis", HERMES_HOME / "skills" / "jarvis")
    if (src / "hooks" / "jarvis-boot").exists():
        shutil.rmtree(HERMES_HOME / "hooks" / "jarvis-boot", ignore_errors=True)
        _copytree(src / "hooks" / "jarvis-boot", HERMES_HOME / "hooks" / "jarvis-boot")
    if (src / "jarvis").exists():
        for item in (src / "jarvis").iterdir():
            target = JARVIS_HOME / item.name
            if item.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                _copytree(item, target)
            else:
                shutil.copy2(item, target)
    for f in ("SOUL.md", "config.yaml", "BOOT.md"):
        if (src / f).exists():
            shutil.copy2(src / f, HERMES_HOME / f)
    if (src / "skill-bundles" / "jarvis.yaml").exists():
        shutil.copy2(src / "skill-bundles" / "jarvis.yaml", HERMES_HOME / "skill-bundles" / "jarvis.yaml")


def latest_backup() -> Path | None:
    if not BACKUPS.exists():
        return None
    dirs = sorted(p for p in BACKUPS.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


# ───────────────────────── установка ─────────────────────────
def download_and_extract(tarball: str, workdir: Path) -> Path:
    """Скачать tar.gz и вернуть папку с install.sh внутри."""
    archive = workdir / "src.tar.gz"
    req = urllib.request.Request(tarball, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r, open(archive, "wb") as f:
        shutil.copyfileobj(r, f)
    with tarfile.open(archive) as tf:
        # защита от path traversal в архиве
        for m in tf.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts:
                raise RuntimeError(f"подозрительный путь в архиве: {m.name}")
        tf.extractall(workdir, filter="data") if hasattr(tarfile, "data_filter") else tf.extractall(workdir)
    for cand in workdir.iterdir():
        if cand.is_dir() and (cand / "install.sh").exists():
            return cand
    raise RuntimeError("в архиве нет install.sh")


def smoke_test(src: Path) -> None:
    """Все .py компилируются, VERSION есть, install.sh синтаксически корректен — до того, как трогать установку."""
    for py in src.rglob("*.py"):
        if "tests" in py.parts:
            continue
        py_compile.compile(str(py), doraise=True)
    if not (src / "VERSION").exists():
        raise RuntimeError("в новой версии нет файла VERSION")
    subprocess.run(["bash", "-n", str(src / "install.sh")], check=True)


def restart_services() -> None:
    """launchd-агенты перезапускаем kickstart'ом; если сервисами управляет JARVIS.app — он сам заметит новый install.json."""
    if sys.platform != "darwin":
        return
    uid = os.getuid()
    env = {**os.environ, "HERMES_HOME": str(HERMES_HOME)}

    def loaded(label: str) -> bool:
        try:
            return subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"], capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def run(cmd: list[str], timeout: int) -> None:
        try:
            subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except (OSError, subprocess.SubprocessError):
            pass  # перезапуск — best effort; сама установка уже завершена

    jarvis = Path.home() / ".local" / "bin" / "jarvis"
    if loaded("ai.jarvis.hud"):
        run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.jarvis.hud"], 15)
    elif jarvis.exists():
        run([str(jarvis), "hud", "restart"], 20)
    if loaded("ai.jarvis.gateway"):
        run(["launchctl", "kickstart", "-k", f"gui/{uid}/ai.jarvis.gateway"], 15)
    else:
        hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
        run([hermes, "gateway", "restart"], 60)


def rebuild_app() -> str:
    """Пересобрать JARVIS.app из свежего app.src, если приложение установлено и есть swiftc.

    install.sh при обновлении запускается с --no-app (чтобы не открывать окна из демона), поэтому
    приложение строки меню пересобираем здесь; если оно запущено — перезапускаем отдельным процессом,
    чтобы обновление, начатое из самого приложения, не убило само себя.
    """
    if sys.platform != "darwin":
        return ""
    app = Path.home() / "Applications" / "JARVIS.app"
    build = JARVIS_HOME / "app.src" / "build.sh"
    if not app.exists() or not build.exists() or not shutil.which("swiftc"):
        return ""
    proc = subprocess.run(["bash", str(build), str(app)], capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        return f"JARVIS.app не пересобрано: {(proc.stderr or proc.stdout)[-300:]}"
    running = subprocess.run(["pgrep", "-x", "JARVIS"], capture_output=True).returncode == 0
    if running:
        subprocess.Popen(["bash", "-c", "sleep 2; osascript -e 'tell application \"JARVIS\" to quit' >/dev/null 2>&1; "
                          "sleep 1; pkill -x JARVIS >/dev/null 2>&1; open -a \"$0\"", str(app)],
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "JARVIS.app пересобрано" + (" и перезапускается" if running else "")


def apply(force: bool = False, tarball: str | None = None, version: str | None = None) -> dict:
    cur = installed()
    info = read_json(UPDATE_JSON)
    if not tarball:
        if not info or info.get("checked_at", "")[:10] != now_iso()[:10]:
            info = check()
        if info.get("error"):
            raise RuntimeError(f"проверка не удалась: {info['error']}")
        if not info.get("available") and not force:
            return {"updated": False, "reason": "уже последняя версия", "version": cur["version"]}
        tarball, version = info["tarball"], info["latest"]
    log = [f"→ обновление {cur['version']} → {version}"]
    with tempfile.TemporaryDirectory(prefix="jarvis-update-") as tmp:
        src = download_and_extract(tarball, Path(tmp))
        log.append(f"✔ скачано: {src.name}")
        smoke_test(src)
        log.append("✔ проверка синтаксиса пройдена")
        backup = make_backup(cur["version"])
        log.append(f"✔ бэкап: {backup}")
        env = {**os.environ, "HERMES_HOME": str(HERMES_HOME), "JARVIS_QUIET": "1",
               "JARVIS_COMMIT": info.get("latest_commit", "") if info else "",
               "JARVIS_CHANNEL": cur["channel"], "JARVIS_AUTO_UPDATE": cur["auto_update"]}
        proc = subprocess.run(["bash", str(src / "install.sh"), "--yes", "--no-launchd", "--no-brew-tools",
                               "--no-voice", "--no-cron", "--no-app"], env=env, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0:
            restore_backup(backup)
            write_json(INSTALL_JSON, cur)
            raise RuntimeError("install.sh завершился с ошибкой — выполнен откат.\n" + (proc.stderr or proc.stdout)[-1500:])
        log.append("✔ install.sh выполнен")
    new = installed()
    if parse_version(new["version"]) < parse_version(cur["version"]) and not force:
        # install.sh почему-то записал более старую версию — не верим, откатываемся
        restore_backup(backup)
        write_json(INSTALL_JSON, cur)
        raise RuntimeError(f"после установки версия {new['version']} старше текущей — откат")
    new["previous_version"] = cur["version"]
    new["updated_at"] = now_iso()
    write_json(INSTALL_JSON, new)
    write_json(UPDATE_JSON, {**info, "available": False, "current": new["version"], "applied_at": now_iso()})
    restart_services()
    log.append("✔ сервисы перезапущены")
    try:
        msg = rebuild_app()
    except (OSError, subprocess.SubprocessError) as e:  # приложение — не критично для работы JARVIS
        msg = f"JARVIS.app не пересобрано: {e}"
    if msg:
        log.append(("✔ " if msg.startswith("JARVIS.app пересобрано") else "⚠ ") + msg)
    notify("JARVIS обновлён", f"Версия {new['version']}. Откат: jarvis update --rollback")
    return {"updated": True, "from": cur["version"], "to": new["version"], "backup": str(backup), "log": log}


def rollback() -> dict:
    b = latest_backup()
    if not b:
        raise RuntimeError("бэкапов нет")
    cur = installed()
    restore_backup(b)
    prev = read_json(b / "jarvis" / "install.json", {"version": b.name.split("-")[0]})
    prev["rolled_back_from"] = cur["version"]
    prev["rolled_back_at"] = now_iso()
    write_json(INSTALL_JSON, prev)
    restart_services()
    notify("JARVIS", f"Откат на версию {prev.get('version')} выполнен")
    return {"rolled_back": True, "to": prev.get("version"), "from": cur["version"], "backup": str(b)}


def auto() -> dict:
    """Ежедневный запуск из launchd: проверить; в режиме auto — установить."""
    cur = installed()
    if cur["auto_update"] == "off":
        return {"skipped": "auto_update=off"}
    res = check(do_notify=(cur["auto_update"] == "check"))
    if cur["auto_update"] == "auto" and res.get("available") and not res.get("error"):
        try:
            return apply()
        except Exception as e:
            notify("JARVIS: обновление не удалось", str(e)[:120])
            return {"updated": False, "error": str(e)}
    return res


def set_option(key: str, value: str) -> dict:
    cur = installed()
    if key == "channel":
        assert value in ("stable", "main"), "channel: stable | main"
        cur["channel"] = value
    elif key == "auto":
        assert value in ("off", "check", "auto"), "auto: off | check | auto"
        cur["auto_update"] = value
    else:
        raise SystemExit("set channel … | set auto …")
    write_json(INSTALL_JSON, cur)
    return cur


def status() -> dict:
    cur = installed()
    upd = read_json(UPDATE_JSON)
    b = latest_backup()
    return {"version": cur["version"], "commit": cur["commit"][:7], "repo": cur["repo"], "channel": cur["channel"],
            "auto_update": cur["auto_update"], "installed_at": cur.get("installed_at"),
            "update_available": upd.get("available", False), "latest": upd.get("latest"),
            "last_check": upd.get("checked_at"), "last_error": upd.get("error", ""),
            "rollback_available": str(b) if b else None}


# ───────────────────────── CLI ─────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS updater")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check"); c.add_argument("--notify", action="store_true"); c.add_argument("--json", action="store_true")
    a = sub.add_parser("apply"); a.add_argument("--force", action="store_true")
    sub.add_parser("auto"); sub.add_parser("rollback")
    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    st = sub.add_parser("set"); st.add_argument("key"); st.add_argument("value")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            r = check(args.notify)
            if args.json:
                print(json.dumps(r, ensure_ascii=False))
            elif r.get("error"):
                print(f"⚠ не удалось проверить: {r['error']}")
            elif r["available"]:
                print(f"⬆ Доступно обновление: {r['current']} → {r['latest']} ({r['channel']})\n{r['url']}\n\n{r['notes'][:600]}")
            else:
                print(f"✔ У вас последняя версия {r['current']} ({r['channel']})")
            return 0
        if args.cmd == "apply":
            r = apply(force=args.force)
            print("\n".join(r.get("log", [])) or r.get("reason", ""))
            if r.get("updated"):
                print(f"\n✔ JARVIS обновлён до {r['to']}. Откат: jarvis update --rollback")
            return 0
        if args.cmd == "auto":
            print(json.dumps(auto(), ensure_ascii=False)); return 0
        if args.cmd == "rollback":
            r = rollback(); print(f"✔ Откат на {r['to']} (из {r['backup']})"); return 0
        if args.cmd == "set":
            r = set_option(args.key, args.value); print(f"✔ channel={r['channel']} auto_update={r['auto_update']}"); return 0
        r = status()
        if getattr(args, "json", False):
            print(json.dumps(r, ensure_ascii=False))
        else:
            print(f"JARVIS {r['version']} ({r['commit'] or '—'}) · {r['repo']} · канал {r['channel']} · автообновление {r['auto_update']}")
            print(f"последняя проверка: {r['last_check'] or 'ещё не было'}"
                  + (f" · доступно {r['latest']}" if r['update_available'] else "")
                  + (f" · ошибка: {r['last_error']}" if r['last_error'] else ""))
            print(f"откат возможен: {'да → ' + r['rollback_available'] if r['rollback_available'] else 'нет'}")
        return 0
    except Exception as e:
        print(f"✖ {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
