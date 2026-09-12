#!/usr/bin/env python3
"""
jarvis doctor — самодиагностика JARVIS одной командой, с автопочинкой.

    jarvis doctor            проверить всё и показать, что делать
    jarvis doctor --fix      то же + починить, что можно автоматически (ключ HUD, launchd, плагины, индекс хранилища…)
    jarvis doctor --json     машинно-читаемый отчёт (использует JARVIS.app)

Проверки (каждая — независима, падение одной не мешает другим):
  1. hermes установлен и отвечает                    6. HUD отвечает на :8765 и знает ключ API
  2. модель настроена и РЕАЛЬНО отвечает (ping)     7. launchd-агенты загружены (если ставили)
  3. плагины JARVIS включены и импортируются        8. права macOS (краткая выжимка selftest)
  4. API-сервер Hermes (.env) включён, ключ есть     9. хранилище ~/JARVIS и индекс
  5. gateway/API :8642 живой                        10. версия JARVIS и доступные обновления

Идея: новичок при любой проблеме запускает `jarvis doctor --fix` и получает либо «всё зелёное», либо конкретный
следующий шаг человеческим языком. Без LLM (кроме одного короткого ping модели, который можно отключить --no-model).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
JARVIS_HOME = HERMES_HOME / "jarvis"
HUD_PORT = int(os.environ.get("JARVIS_HUD_PORT", "8765"))
API_PORT = 8642
PLUGINS = ("jarvis-core", "jarvis-macos", "jarvis-brain")
IS_MAC = sys.platform == "darwin"
BIN = Path.home() / ".local" / "bin"

C = {"ok": "\033[1;32m✔\033[0m", "warn": "\033[1;33m⚠\033[0m", "fail": "\033[1;31m✖\033[0m", "fixed": "\033[1;36m⟳\033[0m", "skip": "\033[2m·\033[0m"}


class Check:
    def __init__(self, name: str):
        self.name, self.status, self.note, self.fix_hint, self.fixed = name, "ok", "", "", False

    def ok(self, note: str = ""):
        self.status, self.note = "ok", note
        return self

    def warn(self, note: str, fix: str = ""):
        self.status, self.note, self.fix_hint = "warn", note, fix
        return self

    def fail(self, note: str, fix: str = ""):
        self.status, self.note, self.fix_hint = "fail", note, fix
        return self

    def skip(self, note: str = ""):
        self.status, self.note = "skip", note
        return self

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "note": self.note, "fix": self.fix_hint, "fixed": self.fixed}


def sh(cmd: list[str], timeout: int = 20, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PATH": f"{BIN}:/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""), **(env or {})})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def http(url: str, timeout: float = 2.0, headers: dict | None = None) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2000).decode(errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # любая сетевая ошибка = «не отвечает»
        return 0, str(e)[:100]


def read_env() -> dict:
    out = {}
    try:
        for line in (HERMES_HOME / ".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def write_env(updates: dict) -> None:
    p = HERMES_HOME / ".env"
    lines = p.read_text().splitlines() if p.exists() else []
    for k, v in updates.items():
        lines = [ln for ln in lines if not ln.startswith(f"{k}=")]
        lines.append(f"{k}={v}")
    p.write_text("\n".join(lines) + "\n")
    os.chmod(p, 0o600)


# ─────────────────────────────── проверки ───────────────────────────────

def check_hermes(fix: bool) -> Check:
    c = Check("Hermes Agent")
    hermes = shutil.which("hermes") or (str(BIN / "hermes") if (BIN / "hermes").exists() else None)
    if not hermes:
        return c.fail("команда hermes не найдена", "bash install.sh  (установит Hermes официальным скриптом)")
    code, out = sh([hermes, "--version"], timeout=30)
    if code != 0:
        return c.fail(f"hermes не запускается: {out.strip()[:120]}", "hermes doctor")
    return c.ok(out.strip().splitlines()[0][:60] if out.strip() else hermes)


def check_model(fix: bool, do_ping: bool) -> Check:
    c = Check("Модель LLM")
    code, out = sh(["hermes", "config", "get", "model"], timeout=30)
    model = out.strip().splitlines()[-1].strip() if code == 0 and out.strip() else ""
    if not model or model in ("None", "null", ""):
        return c.fail("модель не настроена", "hermes model   (выберите OpenRouter/Anthropic/OpenAI/Ollama)")
    if not do_ping:
        return c.ok(f"{model} (ping пропущен)")
    t0 = time.time()
    code, out = sh(["hermes", "chat", "-q", "Ответь одним словом: ok"], timeout=90)
    dt = time.time() - t0
    text = out.strip()
    low = text.lower()
    if code != 0 or not text or any(k in low for k in ("error code", "http 4", "http 5", "traceback", "401", "403", "405", "429")):
        return c.fail(f"{model} не отвечает: {text[-140:] or 'пустой ответ'}",
                      "hermes model → выберите рабочую модель; проверьте ключ провайдера и баланс")
    return c.ok(f"{model} отвечает ({dt:.1f} с)")


def check_plugins(fix: bool) -> Check:
    c = Check("Плагины JARVIS")
    missing_dirs = [p for p in PLUGINS if not (HERMES_HOME / "plugins" / p / "plugin.yaml").exists()]
    if missing_dirs:
        return c.fail(f"не установлены: {', '.join(missing_dirs)}", "bash install.sh --yes --no-launchd --no-brew-tools")
    code, out = sh(["hermes", "plugins", "list"], timeout=30)
    listed = [p for p in PLUGINS if p in out]
    if len(listed) < len(PLUGINS):
        if fix:
            sh(["hermes", "plugins", "enable", *PLUGINS], timeout=30)
            code, out = sh(["hermes", "plugins", "list"], timeout=30)
            if all(p in out for p in PLUGINS):
                c.fixed = True
                return c.ok("включены (исправлено)")
        return c.warn(f"не включены: {', '.join(p for p in PLUGINS if p not in listed)}",
                      "hermes plugins enable jarvis-core jarvis-macos jarvis-brain")
    # импортируемость: синтаксис всех .py
    import py_compile
    bad = []
    for p in PLUGINS:
        for py in (HERMES_HOME / "plugins" / p).rglob("*.py"):
            try:
                py_compile.compile(str(py), doraise=True)
            except py_compile.PyCompileError as e:
                bad.append(f"{py.name}: {str(e)[:60]}")
    if bad:
        return c.fail("ошибки в коде плагинов: " + "; ".join(bad[:2]), "jarvis update --rollback  или  bash install.sh")
    return c.ok(f"{len(PLUGINS)}/3 включены, код компилируется")


def check_env_api(fix: bool) -> Check:
    c = Check("API-сервер Hermes (.env)")
    env = read_env()
    problems = {}
    if env.get("API_SERVER_ENABLED", "").lower() != "true":
        problems["API_SERVER_ENABLED"] = "true"
    if not env.get("API_SERVER_KEY"):
        import secrets
        problems["API_SERVER_KEY"] = secrets.token_hex(24)
    if not env.get("API_SERVER_HOST"):
        problems["API_SERVER_HOST"] = "127.0.0.1"
    if problems:
        if fix:
            write_env(problems)
            c.fixed = True
            return c.warn(f"добавлено в .env: {', '.join(problems)} — перезапустите: jarvis gateway restart && jarvis hud restart")
        return c.fail(f"в {HERMES_HOME}/.env нет: {', '.join(problems)}", "jarvis doctor --fix")
    return c.ok("API_SERVER_ENABLED=true, ключ задан")


def check_gateway(fix: bool) -> Check:
    c = Check(f"Gateway / API :{API_PORT}")
    st, _ = http(f"http://127.0.0.1:{API_PORT}/health")
    if st == 200:
        return c.ok("отвечает")
    if fix:
        sh(["jarvis", "gateway", "start"], timeout=30)
        for _ in range(10):
            time.sleep(1)
            if http(f"http://127.0.0.1:{API_PORT}/health")[0] == 200:
                c.fixed = True
                return c.ok("запущен (исправлено)")
    return c.fail("не отвечает — HUD-чат и мессенджеры не работают", "jarvis gateway start   (лог: hermes logs)")


def check_hud(fix: bool) -> Check:
    c = Check(f"HUD :{HUD_PORT}")
    st, body = http(f"http://127.0.0.1:{HUD_PORT}/api/status")
    if st != 200:
        if fix:
            sh(["jarvis", "hud", "start"], timeout=30)
            time.sleep(1.5)
            st, body = http(f"http://127.0.0.1:{HUD_PORT}/api/status")
            if st == 200:
                c.fixed = True
        if st != 200:
            return c.fail("не запущен", "jarvis hud   (лог: jarvis hud log)")
    try:
        j = json.loads(body)
    except ValueError:
        j = {}
    up = (j.get("hermes") or {}).get("up")
    # ключ HUD должен совпадать с .env — проверяем реальным запросом к API с ключом из .env
    key = read_env().get("API_SERVER_KEY", "")
    if key and up:
        st2, _ = http(f"http://127.0.0.1:{API_PORT}/v1/models", headers={"Authorization": f"Bearer {key}"})
        if st2 == 401:
            return c.fail("ключ в .env не совпадает с тем, что использует gateway", "jarvis gateway restart && jarvis hud restart")
    return c.ok("отвечает" + (", видит Hermes API" if up else "; Hermes API недоступен — см. пункт Gateway") + (" (исправлено)" if c.fixed else ""))


def check_launchd(fix: bool) -> Check:
    c = Check("Автозапуск (launchd)")
    if not IS_MAC:
        return c.skip("не macOS")
    la = Path.home() / "Library" / "LaunchAgents"
    plists = [p for p in ("ai.jarvis.hud", "ai.jarvis.gateway", "ai.jarvis.updater", "ai.jarvis.app") if (la / f"{p}.plist").exists()]
    if not plists:
        return c.warn("агенты не установлены — JARVIS не поднимется сам после перезагрузки", "bash install.sh --yes  (шаг launchd)  или запускайте jarvis up вручную")
    code, out = sh(["launchctl", "list"], timeout=10)
    loaded = [p for p in plists if p in out]
    missing = [p for p in plists if p not in loaded]
    if missing and fix:
        for p in missing:
            sh(["launchctl", "load", "-w", str(la / f"{p}.plist")], timeout=10)
        code, out = sh(["launchctl", "list"], timeout=10)
        missing = [p for p in plists if p not in out]
        c.fixed = not missing
    if missing:
        return c.warn(f"не загружены: {', '.join(missing)}", "launchctl load -w ~/Library/LaunchAgents/<имя>.plist")
    return c.ok(f"{len(loaded)} агент(ов) загружено" + (" (исправлено)" if c.fixed else ""))


def check_permissions(fix: bool) -> Check:
    c = Check("Права macOS")
    if not IS_MAC:
        return c.skip("не macOS")
    selftest = JARVIS_HOME / "selftest.py"
    if not selftest.exists():
        return c.warn("selftest.py не установлен", "bash install.sh")
    code, out = sh([sys.executable, str(selftest), "--json"], timeout=180)
    try:
        rows = json.loads(out[out.index("["):]) if "[" in out else []
    except ValueError:
        return c.warn("selftest не вернул отчёт", "jarvis selftest")
    if not rows:
        return c.warn("selftest не вернул отчёт", "jarvis selftest")
    if isinstance(rows, dict):
        rows = rows.get("rows") or rows.get("results") or []
    perm = [r for r in rows if r.get("status") == "perm"]
    fail = [r for r in rows if r.get("status") == "fail"]
    if perm:
        note = "нет прав: " + ", ".join(sorted({r["note"].replace("нет прав: ", "") for r in perm}))
        if fix:
            sh([sys.executable, str(selftest), "--fix"], timeout=60)
            note += " — открыл нужные панели настроек"
        return c.warn(note, "jarvis selftest --fix  → выдайте права терминалу и python")
    if fail:
        return c.warn(f"{len(fail)} инструмент(ов) с ошибкой: " + ", ".join(r["tool"] for r in fail[:4]), "jarvis selftest")
    return c.ok(f"все {len(rows)} интеграции работают")


NATIVE_CLI = {  # инструмент → (что даёт, как поставить)
    "ical": ("календарь через EventKit", "brew install BRO3886/tap/ical"),
    "remindctl": ("напоминания через EventKit", "brew install steipete/tap/remindctl"),
    "peekaboo": ("окна и скриншоты через Accessibility", "brew install steipete/tap/peekaboo  (macOS 15+)"),
}


def check_native_cli(fix: bool) -> Check:
    """Готовые CLI с GitHub, которыми jarvis-macos заменяет AppleScript. Без них всё работает, но медленнее."""
    c = Check("Нативные CLI")
    if not IS_MAC:
        return c.skip("не macOS")
    path = f"{BIN}:/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
    missing = [n for n in NATIVE_CLI if not shutil.which(n, path=path)]
    if missing and fix and shutil.which("brew", path=path):
        for n in list(missing):
            code, _ = sh(["brew", "install", NATIVE_CLI[n][1].split()[2]], timeout=600)
            if code == 0:
                missing.remove(n)
        c.fixed = bool(set(NATIVE_CLI) - set(missing))
    if missing:
        hint = "; ".join(NATIVE_CLI[n][1] for n in missing)
        return c.warn("нет: " + ", ".join(missing) + " — AppleScript-запасной путь", hint)
    return c.ok("ical, remindctl, peekaboo установлены")


def check_memory(fix: bool) -> Check:
    """memory.provider: holographic в config.yaml + memory_store.db с фактами (см. docs/BRAIN.md)."""
    c = Check("Память Hermes (holographic)")
    cfg_path = HERMES_HOME / "config.yaml"
    provider = ""
    try:
        for line in cfg_path.read_text().splitlines():
            if line.strip().startswith("provider:") and "holographic" in line:
                provider = "holographic"
    except OSError:
        return c.warn("config.yaml не найден", "bash install.sh")
    if provider != "holographic":
        if fix:
            code, _ = sh(["hermes", "config", "set", "memory.provider", "holographic"], timeout=30)
            if code == 0:
                c.fixed = True
                return c.ok("memory.provider=holographic (исправлено) — перезапустите: jarvis gateway restart")
        return c.warn("memory.provider не holographic — факты не зеркалятся в память Hermes", "hermes config set memory.provider holographic")
    db = HERMES_HOME / "memory_store.db"
    brain_db = HERMES_HOME / "plugin-data" / "jarvis-brain" / "brain.db"
    if not db.exists():
        if brain_db.exists() and fix:
            sh([sys.executable, str(HERMES_HOME / "jarvis" / "migrate_brain.py")], timeout=120)
            c.fixed = True
        return c.ok("включён; memory_store.db появится после первого факта" + (" (перенос выполнен)" if c.fixed else "")) if not brain_db.exists() or c.fixed \
            else c.warn("включён, но старые заметки ещё не перенесены", "jarvis brain migrate")
    try:
        import sqlite3
        n = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    except Exception:
        n = "?"
    return c.ok(f"включён, фактов: {n}")


def check_vault(fix: bool) -> Check:
    c = Check("Хранилище ~/JARVIS")
    vault_py = HERMES_HOME / "plugins" / "jarvis-brain" / "vault.py"
    if not vault_py.exists():
        return c.warn("плагин jarvis-brain без vault (старая версия)", "jarvis update")
    code, out = sh([sys.executable, str(vault_py), "status", "--json"], timeout=60)
    try:
        st = json.loads(out)
    except ValueError:
        return c.warn(f"vault status не отвечает: {out.strip()[:80]}", "jarvis vault status")
    if not st.get("exists"):
        if fix:
            sh([sys.executable, str(vault_py), "init"], timeout=30)
            c.fixed = True
            return c.ok(f"создано {st['root']} (исправлено) — кладите туда файлы")
        return c.warn(f"папка {st['root']} ещё не создана", "jarvis vault open")
    extras = []
    if not st.get("pdf"):
        extras.append("PDF не индексируются: brew install poppler")
    note = f"{st['files']} файлов, {len(st.get('sources', []))} проект(ов), скан {str(st.get('last_scan') or '—')[:16]}"
    if st["files"] == 0 and fix:
        sh([sys.executable, str(vault_py), "reindex"], timeout=300)
    return c.ok(note + (" · " + "; ".join(extras) if extras else ""))


def check_network(fix: bool) -> Check:
    """Доступен ли GitHub из Python (urllib с системным прокси) и из curl. Расхождение = прокси/PAC от VPN."""
    import urllib.error
    import urllib.request
    c = Check("Сеть → GitHub")
    url = "https://api.github.com/"
    py_err = ""
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "jarvis-doctor"}), timeout=8).read(1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        py_err = str(getattr(e, "reason", e))[:80]
    code, _ = sh(["curl", "-fsS", "-o", "/dev/null", "--max-time", "8", url], timeout=15)
    if not py_err:
        return c.ok("api.github.com доступен")
    proxies = urllib.request.getproxies()
    if code == 0:
        return c.warn(f"Python не достучался до GitHub ({py_err}), а curl — да" + (f"; прокси из системы: {proxies}" if proxies else ""),
                      "Системные настройки → Сеть → ваше подключение → Подробнее → Прокси: снимите галочки (часто остаётся от VPN). "
                      "Обновление всё равно работает: обновлялка сама переключится на curl (jarvis update), или jarvis update --from ~/Downloads/jarvis-hermes")
    return c.fail(f"GitHub недоступен ни из Python, ни из curl ({py_err})", "проверьте интернет/VPN; без сети — jarvis update --from <папка проекта>")


def check_version(fix: bool) -> Check:
    c = Check("Версия JARVIS")
    inst, upd = {}, {}
    try:
        inst = json.loads((JARVIS_HOME / "install.json").read_text())
    except (OSError, ValueError):
        return c.warn("install.json не найден — установка неполная", "bash install.sh")
    try:
        upd = json.loads((JARVIS_HOME / "update.json").read_text())
    except (OSError, ValueError):
        pass
    v = inst.get("version", "?")
    if upd.get("available"):
        return c.warn(f"{v} → доступно {upd.get('latest')}", "jarvis update")
    if upd.get("error"):
        return c.ok(f"{v} (последняя проверка обновлений не удалась: {upd['error'][:60]})")
    return c.ok(f"{v} · канал {inst.get('channel', 'stable')} · автообновление {inst.get('auto_update', 'check')}")


# ─────────────────────────────── отчёт ───────────────────────────────

def run(fix: bool, ping_model: bool, quick: bool) -> list[Check]:
    checks = [check_hermes(fix)]
    if checks[0].status == "fail":
        return checks
    checks.append(check_model(fix, do_ping=ping_model and not quick))
    checks.append(check_plugins(fix))
    checks.append(check_env_api(fix))
    checks.append(check_gateway(fix))
    checks.append(check_hud(fix))
    checks.append(check_launchd(fix))
    if not quick:
        checks.append(check_permissions(fix))
    checks.append(check_native_cli(fix))
    checks.append(check_memory(fix))
    checks.append(check_vault(fix))
    if not quick:
        checks.append(check_network(fix))
    checks.append(check_version(fix))
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JARVIS doctor")
    ap.add_argument("--fix", action="store_true", help="чинить, что можно автоматически")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-model", action="store_true", help="не пинговать модель (быстрее, без расхода токенов)")
    ap.add_argument("--quick", action="store_true", help="без ping модели и selftest прав")
    args = ap.parse_args(argv)
    checks = run(args.fix, ping_model=not args.no_model, quick=args.quick)
    if args.json:
        print(json.dumps({"ok": all(c.status in ("ok", "skip") for c in checks), "checks": [c.as_dict() for c in checks]}, ensure_ascii=False, indent=1))
        return 0 if all(c.status != "fail" for c in checks) else 1
    print("\033[1;36mJ.A.R.V.I.S. doctor\033[0m" + ("  (режим --fix)" if args.fix else ""))
    width = max(len(c.name) for c in checks) + 2
    for c in checks:
        mark = C["fixed"] if c.fixed else C[c.status]
        print(f" {mark} {c.name:<{width}} {c.note}")
        if c.status in ("warn", "fail") and c.fix_hint:
            print(f"   {'':<{width}} → {c.fix_hint}")
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    if not fails and not warns:
        print("\n Всё в порядке, сэр.")
    elif fails:
        print(f"\n {len(fails)} проблем(ы). Начните с первой красной строки" + ("" if args.fix else " или запустите: jarvis doctor --fix"))
    else:
        print(f"\n Работает, но есть {len(warns)} замечание(я)." + ("" if args.fix else " jarvis doctor --fix попробует исправить."))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
