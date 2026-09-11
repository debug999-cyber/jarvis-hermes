"""
jarvis-core — ядро J.A.R.V.I.S. поверх Hermes Agent.

Что делает:
  * pre_llm_call   → подмешивает в каждый ход «ситуационный контекст»
                     (время, батарея, активное приложение, режим фокуса, таймеры);
  * pre/post_tool_call, on_stream_* → транслирует активность агента на HUD
                     (веб-интерфейс «арк-реактора») через HTTP-события;
  * инструменты    → jarvis_hud (панели на экране), jarvis_timer (таймеры/будильники),
                     jarvis_mode (режимы: focus / night / normal), jarvis_update;
  * slash-команды  → /brief (утренний брифинг), /focus, /timer.

Плагин не зависит от HUD: если сервер HUD не запущен, события просто отбрасываются.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import schemas, state
from .hud_client import HudClient
from .triggers import Triggers

logger = logging.getLogger(__name__)

TOOLSET = "jarvis_core"
_SKILLS_DIR = Path(__file__).parent / "skills"

_hud = HudClient()
_cfg = {"user_name": "сэр", "city": "Zürich", "inject_context": True,
        "watchdog": True, "battery_threshold": 20, "watch_calendar": False, "follow_focus": True,
        "triggers": True, "trigger_llm": True, "disk_min_gb": 20, "idle_return_min": 90, "screen_context": True}


# ══════════════════════════════ контекст хода ══════════════════════════════

def _battery_brief() -> str:
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
        for tok in out.replace(";", " ").split():
            if tok.endswith("%"):
                return tok + (" ⚡" if "charging" in out and "discharging" not in out else "")
    except Exception:
        pass
    return ""


def _frontmost() -> str:
    try:
        return subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        return ""


_UPDATE_MENTIONED: dict[str, bool] = {}


def build_context() -> str:
    """Короткий блок текста, который увидит модель перед сообщением пользователя."""
    now = dt.datetime.now()
    parts = [f"Сейчас {now.strftime('%A, %d %B %Y, %H:%M')}."]
    b = _battery_brief()
    if b:
        parts.append(f"Батарея: {b}.")
    fm = _frontmost()
    if fm:
        parts.append(f"Активное приложение: {fm}.")
    mode = state.get_mode()
    if mode != "normal":
        parts.append(f"Режим: {mode} ({state.MODE_HINTS.get(mode, '')}).")
    timers = state.active_timers()
    if timers:
        parts.append("Активные таймеры: " + "; ".join(f"«{t['label']}» через {t['remaining_h']}" for t in timers) + ".")
    upd = update_status()
    if upd["update_available"] and not _UPDATE_MENTIONED.get(upd["latest"]):
        # только один раз за процесс — иначе модель повторяла это в каждом ответе
        _UPDATE_MENTIONED[upd["latest"]] = True
        parts.append(f"Доступно обновление JARVIS {upd['latest']} (сейчас {upd['version']}) — можешь упомянуть одной фразой; ставить только по просьбе.")
    parts.append(f"Обращайся к пользователю: {_cfg['user_name']}.")
    return "[JARVIS context] " + " ".join(parts)


_SKILL_INJECT_RE = re.compile(r'^\s*\[IMPORTANT: The user has invoked the "([^"]+)" skill', re.I)


def _turn_source(session_id: str, user_message: str, kwargs: dict) -> tuple[str, str]:
    """Откуда пришёл ход: ('user', текст) для живого диалога, ('cron', подпись) для фоновых задач.

    Cron-сессии Hermes называются cron_<id>, а их «сообщение пользователя» — это служебный промпт
    с вставленным навыком. Раньше он попадал на HUD как «ВЫ: [IMPORTANT: The user has invoked…]».
    """
    text = user_message or ""
    platform = str(kwargs.get("platform") or "")
    m = _SKILL_INJECT_RE.match(text)
    if session_id.startswith("cron") or platform == "cron" or m:
        label = m.group(1).split("/")[-1] if m else (kwargs.get("job_name") or "задача")
        return "cron", label
    return "user", text[:300]


# ── контекст экрана: «что у меня на экране / посмотри сюда / что это за ошибка» → скриншот без лишнего вопроса ──
_SCREEN_RE = re.compile(
    r"(на\s+(моём\s+)?экране|на\s+мониторе|посмотри\s+(сюда|на\s+экран|что\s+тут|что\s+здесь)|глянь\s+(сюда|на\s+экран)|"
    r"что\s+(тут|здесь|это)\s+(написано|за\s+ошибка|за\s+окно|происходит|открыто)|видишь\s+(это|экран|окно)|"
    r"(прочитай|переведи|объясни|исправь)\s+(это|что\s+на\s+экране|текст\s+на\s+экране)|эт[ау]\s+ошибк[ау]\s+на\s+экране|"
    r"what'?s\s+on\s+(my\s+)?screen|look\s+at\s+(my\s+)?screen|see\s+this)", re.I)


def wants_screen(text: str) -> bool:
    return bool(text) and bool(_SCREEN_RE.search(text))


def capture_screen() -> str | None:
    """Скриншот активного дисплея в $HERMES_HOME/cache/jarvis/screenshots (тот же каталог, что у mac_screenshot)."""
    if sys.platform != "darwin":
        return None
    out_dir = Path(state.hermes_home()) / "cache" / "jarvis" / "screenshots"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"context-{dt.datetime.now():%Y%m%d-%H%M%S}.png"
        subprocess.run(["screencapture", "-x", str(path)], timeout=10, capture_output=True)
        return str(path) if path.exists() else None
    except (subprocess.SubprocessError, OSError):
        return None


def screen_ocr(path: str, limit: int = 60) -> list[str]:
    """Текст с экрана через peekaboo (openclaw/Peekaboo, Apple Vision), если он установлен. Иначе []."""
    bin_ = shutil.which("peekaboo") or next((p for p in ("/opt/homebrew/bin/peekaboo", "/usr/local/bin/peekaboo") if Path(p).exists()), None)
    if not bin_:
        return []
    try:
        proc = subprocess.run([bin_, "see", "--ocr", "--json", "--mode", "frontmost", "--path", path], timeout=45, capture_output=True, text=True)
        out = proc.stdout
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except (subprocess.SubprocessError, OSError, ValueError):
        return []
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    lines, seen = [], set()
    for el in payload.get("ui_elements") or []:
        text = " ".join(str(el.get(k)) for k in ("title", "label", "value") if isinstance(el, dict) and el.get(k)).strip()
        if text and text not in seen:
            seen.add(text)
            lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def screen_context(user_message: str) -> str:
    if not _cfg.get("screen_context", True) or not wants_screen(user_message):
        return ""
    path = capture_screen()
    if not path:
        return ("[JARVIS screen] Пользователь говорит о своём экране, но снять скриншот не удалось (нет права «Запись экрана»). "
                "Попроси выдать право: Системные настройки → Конфиденциальность → Запись экрана → терминал, или jarvis selftest --fix.")
    _hud.emit("panel.show", {"kind": "image", "title": "КОНТЕКСТ ЭКРАНА", "content": path, "position": "right", "ttl": 60})
    lines = screen_ocr(path)
    if lines:  # текст уже есть — vision-модель нужна только если важна сама картинка
        text = "\n".join(lines)[:3000]
        return (f"[JARVIS screen] Пользователь говорит о своём экране. Активное приложение: {_frontmost() or '—'}. "
                f"Текст с экрана (OCR):\n{text}\nЕсли по тексту всё ясно — отвечай сразу; если важна картинка/расположение — "
                f"vision_analyze({path}).")
    return (f"[JARVIS screen] Пользователь говорит о своём экране. Скриншот уже снят: {path} — сразу вызови vision_analyze "
            f"с этим путём (без вопроса «какой экран?») и отвечай по его содержимому. Активное приложение: {_frontmost() or '—'}.")


def hook_pre_llm_call(session_id: str = "", user_message: str = "", is_first_turn: bool = False, **kwargs):
    source, text = _turn_source(session_id, user_message, kwargs)
    _hud.emit("turn.start", {"session": session_id, "text": text, "source": source})
    if not _cfg.get("inject_context", True):
        return None
    ctx = build_context()
    if source == "user":
        sc = screen_context(user_message)
        if sc:
            ctx += "\n" + sc
    return {"context": ctx}


def hook_post_llm_call(session_id: str = "", assistant_response: str = "", **kwargs):
    source = "cron" if session_id.startswith("cron") or str(kwargs.get("platform") or "") == "cron" else "user"
    text = (assistant_response or "").strip()
    if source == "cron" and text.upper().strip("[] ") in {"NO_REPLY", "SILENT", ""}:
        text = ""  # heartbeat промолчал — на HUD показывать нечего
    _hud.emit("turn.end", {"session": session_id, "text": text[:2000], "source": source})


def hook_pre_tool_call(tool_name: str = "", args: dict | None = None, task_id: str = "", **kwargs):
    preview = json.dumps(args or {}, ensure_ascii=False)[:200]
    _hud.emit("tool.start", {"tool": tool_name, "args": preview, "task": task_id})
    return None  # ничего не блокируем — этим занимается approvals Hermes


def hook_post_tool_call(tool_name: str = "", args: dict | None = None, result=None, task_id: str = "", **kwargs):
    ok = True
    try:
        if isinstance(result, str) and result.lstrip().startswith("{"):
            ok = json.loads(result).get("success", True) is not False
    except Exception:
        pass
    _hud.emit("tool.end", {"tool": tool_name, "ok": ok, "task": task_id})


def hook_session_start(session_id: str = "", model: str = "", platform: str = "", **kwargs):
    _hud.emit("session.start", {"session": session_id, "model": model, "platform": platform})


def hook_stream_delta(delta: str = "", kind: str = "text", **kwargs):
    if kind == "text" and delta:
        _hud.emit("stream.delta", {"delta": delta})


def hook_stream_end(final_text: str = "", finished: bool = True, **kwargs):
    _hud.emit("stream.end", {"finished": finished})


# ══════════════════════════════ инструменты ════════════════════════════════

def tool_jarvis_hud(args: dict, **kwargs) -> str:
    """Показать панель на HUD: текст, изображение, видео (YouTube), веб-страницу, график."""
    action = args.get("action", "show")
    if action == "clear":
        _hud.emit("panel.clear", {})
        return json.dumps({"success": True, "message": "Экран очищен"})
    panel = {
        "kind": args.get("kind", "text"),
        "title": args.get("title", ""),
        "content": args.get("content", ""),
        "position": args.get("position", "center"),
        "ttl": int(args.get("ttl") or 0),
    }
    sent = _hud.emit("panel.show", panel)
    return json.dumps({"success": True, "delivered": sent, "panel": panel}, ensure_ascii=False)


def tool_jarvis_timer(args: dict, **kwargs) -> str:
    action = args.get("action")
    if action == "list":
        return json.dumps({"success": True, "timers": state.active_timers()}, ensure_ascii=False)
    if action == "cancel":
        n = state.cancel_timer(args.get("label", ""))
        return json.dumps({"success": True, "cancelled": n})
    if action == "set":
        minutes = float(args.get("minutes") or 0)
        at = args.get("at")
        label = args.get("label") or "таймер"
        if at:
            hh, mm = [int(x) for x in at.split(":")]
            target = dt.datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target <= dt.datetime.now():
                target += dt.timedelta(days=1)
        elif minutes > 0:
            target = dt.datetime.now() + dt.timedelta(minutes=minutes)
        else:
            return json.dumps({"success": False, "error": "Укажите minutes или at (HH:MM)"})
        state.add_timer(label, target)
        _schedule_fire(label, target)
        return json.dumps({"success": True, "label": label, "fires_at": target.strftime("%H:%M:%S")}, ensure_ascii=False)
    return json.dumps({"success": False, "error": f"Неизвестное действие: {action}"})


def _schedule_fire(label: str, target: dt.datetime) -> None:
    """Фоновый поток: по наступлении времени — уведомление + звук + событие HUD."""

    def _wait():
        delay = (target - dt.datetime.now()).total_seconds()
        if delay > 0:
            time.sleep(delay)
        if not state.timer_alive(label, target):
            return  # отменён
        state.cancel_timer(label)
        _hud.emit("timer.fire", {"label": label})
        try:
            subprocess.run(["osascript", "-e", f'display notification "{label}" with title "JARVIS ⏰" sound name "Glass"'], timeout=5)
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        except Exception:
            pass

    threading.Thread(target=_wait, name=f"jarvis-timer-{label}", daemon=True).start()


def tool_jarvis_mode(args: dict, **kwargs) -> str:
    mode = args.get("mode")
    if mode not in state.MODE_HINTS:
        return json.dumps({"success": False, "error": f"Доступные режимы: {', '.join(state.MODE_HINTS)}"})
    state.set_mode(mode)
    _hud.emit("mode.set", {"mode": mode})
    # Побочный эффект: Shortcut «JARVIS Mode <mode>» (если пользователь создал) — например, включает «Не беспокоить».
    # Для совместимости также пробуем «JARVIS Focus On/Off».
    ran = _run_shortcut(f"JARVIS Mode {mode}") or (
        _run_shortcut("JARVIS Focus On") if mode == "focus" else _run_shortcut("JARVIS Focus Off") if mode == "normal" else False
    )
    return json.dumps({"success": True, "mode": mode, "hint": state.MODE_HINTS[mode], "shortcut_ran": ran}, ensure_ascii=False)


def _run_shortcut(name: str) -> bool:
    """Запустить Быструю команду macOS, если она есть. Никогда не бросает."""
    try:
        proc = subprocess.run(["shortcuts", "run", name], capture_output=True, timeout=15)
        return proc.returncode == 0
    except Exception:  # нет shortcuts (Linux) или таймаут
        return False


def _updater_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return home / "jarvis" / "update.py"


def update_status() -> dict:
    """Краткий статус обновлений из файлов updater'а (без сети)."""
    home = _updater_path().parent
    try:
        inst = json.loads((home / "install.json").read_text())
    except (OSError, ValueError):
        inst = {}
    try:
        upd = json.loads((home / "update.json").read_text())
    except (OSError, ValueError):
        upd = {}
    return {"version": inst.get("version", "?"), "channel": inst.get("channel", "stable"),
            "auto_update": inst.get("auto_update", "check"), "update_available": bool(upd.get("available")),
            "latest": upd.get("latest"), "last_check": upd.get("checked_at"), "notes": (upd.get("notes") or "")[:400]}


def tool_jarvis_update(args: dict, **kwargs) -> str:
    action = args.get("action") or "status"
    script = _updater_path()
    if action == "status":
        return json.dumps({"success": True, **update_status()}, ensure_ascii=False)
    if not script.exists():
        return json.dumps({"success": False, "error": f"updater не установлен (нет {script}) — переустановите через install.sh"}, ensure_ascii=False)
    if action in ("apply", "rollback") and not args.get("confirmed"):
        return json.dumps({"success": False, "needs_confirmation": True,
                           "error": f"{action} требует явного подтверждения пользователя (confirmed=true)"}, ensure_ascii=False)
    cmd = {"check": ["check", "--json"], "apply": ["apply"], "rollback": ["rollback"],
           "set_auto": ["set", "auto", str(args.get("value") or "")],
           "set_channel": ["set", "channel", str(args.get("value") or "")]}.get(action)
    if not cmd:
        return json.dumps({"success": False, "error": f"неизвестное действие {action}"}, ensure_ascii=False)
    try:
        proc = subprocess.run([sys.executable, str(script), *cmd], capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return json.dumps({"success": False, "error": "updater не ответил за 15 минут"}, ensure_ascii=False)
    out = (proc.stdout or "").strip()
    if action == "check" and out.startswith("{"):
        data = json.loads(out)
        return json.dumps({"success": not data.get("error"), **data}, ensure_ascii=False)
    if proc.returncode != 0:
        return json.dumps({"success": False, "error": (proc.stderr or out)[-800:]}, ensure_ascii=False)
    if action == "apply":
        _hud.emit("alert", {"kind": "update", "text": "JARVIS обновлён — сервисы перезапускаются"})
    return json.dumps({"success": True, "output": out[-1200:], **update_status()}, ensure_ascii=False)


# ══════════════════════════════ watchdog (без LLM) ═════════════════════════

class Watchdog:
    """Фоновый поток: дешёвые локальные проверки без вызова модели.

    * батарея < порога и не на зарядке → уведомление macOS (не чаще раза в час);
    * (опционально) событие календаря через N минут → уведомление (нужен доступ к Calendar).
    * Focus macOS («Не беспокоить», «Работа», «Сон») → режим JARVIS (focus/night/normal) — без слов;
    * к событию календаря — короткая справка из базы знаний (участники, проект, обещания) на HUD.
    Раньше это делал cron Hermes каждые 30 минут — 48 вызовов LLM в сутки впустую.
    """

    def __init__(self, battery_threshold: int = 20, interval: int = 300, watch_calendar: bool = False, lead_min: int = 10,
                 follow_focus: bool = True, meeting_prep=None, triggers: Triggers | None = None):
        self.battery_threshold = battery_threshold
        self.interval = interval
        self.triggers = triggers  # событийная проактивность (triggers.py); тикает в том же цикле
        self.watch_calendar = watch_calendar
        self.lead_min = lead_min
        self.follow_focus = follow_focus
        self.meeting_prep = meeting_prep  # callable(event) -> str | None: краткая справка из базы знаний
        self._last_battery_alert = -1e12  # «никогда»
        self._alerted_events: set[str] = set()
        self._last_focus: str | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._loop, name="jarvis-watchdog", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # триггеры — каждые 60 с (дёшево, без LLM), остальное — раз в interval
        step = min(60, self.interval)
        elapsed = self.interval  # первый полный tick сразу
        while not self._stop.wait(step):
            elapsed += step
            try:
                if self.triggers:
                    self.triggers.tick(battery=self.battery_state())
                if elapsed >= self.interval:
                    elapsed = 0
                    self.tick()
            except Exception as e:
                logger.debug("watchdog: %s", e)

    def tick(self, now: float | None = None) -> list[str]:
        """Одна итерация; возвращает список отправленных уведомлений (для тестов)."""
        now = now or time.time()
        sent: list[str] = []
        pct, charging = self.battery_state()
        if pct is not None and pct <= self.battery_threshold and not charging and now - self._last_battery_alert > 3600:
            msg = f"Заряд {pct}%. Рекомендую подключить питание, сэр."
            self.notify("JARVIS 🔋", msg)
            _hud.emit("alert", {"kind": "battery", "text": msg})
            self._last_battery_alert = now
            sent.append(msg)
        if self.watch_calendar:
            for ev in self.upcoming_events():
                key = f"{ev['title']}@{ev['start']}"
                if key not in self._alerted_events:
                    self._alerted_events.add(key)
                    msg = f"Через {self.lead_min} минут: {ev['title']}"
                    prep = None
                    if self.meeting_prep:
                        try:
                            prep = self.meeting_prep(ev)
                        except Exception as e:
                            logger.debug("meeting_prep: %s", e)
                    self.notify("JARVIS 📅", msg + (f" — {prep[:120]}" if prep else ""))
                    _hud.emit("alert", {"kind": "calendar", "text": msg})
                    if prep:
                        _hud.emit("panel.show", {"kind": "markdown", "title": f"К ВСТРЕЧЕ · {ev['title']}", "content": prep,
                                                 "position": "right", "ttl": 600})
                    sent.append(msg)
        if self.follow_focus:
            focus = self.macos_focus()
            if focus is not None and focus != self._last_focus:
                if self._last_focus is not None or focus:  # первый тик с «нет фокуса» — не событие
                    mapped = self.map_focus(focus)
                    if mapped and mapped != state.get_mode():
                        state.set_mode(mapped)
                        _hud.emit("mode.set", {"mode": mapped, "source": "macos-focus", "focus": focus})
                        sent.append(f"focus:{focus}->{mapped}")
                self._last_focus = focus
        return sent

    # ── Focus macOS ──
    FOCUS_MAP = {"do not disturb": "focus", "не беспокоить": "focus", "work": "focus", "работа": "focus",
                 "sleep": "night", "сон": "night", "personal": "normal", "личное": "normal", "": "normal"}

    @classmethod
    def map_focus(cls, focus_name: str) -> str | None:
        return cls.FOCUS_MAP.get((focus_name or "").strip().lower())

    @staticmethod
    def macos_focus() -> str | None:
        """Имя активного режима Focus macOS ("" — нет) из ~/Library/DoNotDisturb/DB; None — недоступно."""
        base = Path.home() / "Library" / "DoNotDisturb" / "DB"
        try:
            assertions = json.loads((base / "Assertions.json").read_text())
            records = (assertions.get("data") or [{}])[0].get("storeAssertionRecords") or []
            if not records:
                return ""
            rec = max(records, key=lambda r: r.get("assertionStartDateTimestamp", 0))
            mode_id = rec.get("assertionDetails", {}).get("assertionDetailsModeIdentifier", "")
            configs = json.loads((base / "ModeConfigurations.json").read_text())
            modes = (configs.get("data") or [{}])[0].get("modeConfigurations") or {}
            mode = modes.get(mode_id, {}).get("mode", {})
            return mode.get("name") or mode_id.rsplit(".", 1)[-1] or ""
        except (OSError, ValueError, KeyError, IndexError):
            return None

    @staticmethod
    def battery_state() -> tuple[int | None, bool]:
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
        except Exception:
            return None, False
        pct = None
        for tok in out.replace(";", " ").split():
            if tok.endswith("%") and tok[:-1].isdigit():
                pct = int(tok[:-1])
        charging = ("charging" in out and "discharging" not in out) or "AC Power" in out
        return pct, charging

    def upcoming_events(self) -> list[dict]:
        """События, начинающиеся в ближайшие lead_min минут (Calendar.app через AppleScript)."""
        script = f'''
        set nowD to (current date)
        set endD to nowD + ({self.lead_min} * minutes)
        set output to ""
        tell application "Calendar"
            repeat with cal in calendars
                repeat with ev in (every event of cal whose start date ≥ nowD and start date ≤ endD)
                    set output to output & (summary of ev) & "|" & (time string of (start date of ev)) & linefeed
                end repeat
            end repeat
        end tell
        return output'''
        try:
            out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return []
        evs = []
        for line in out.splitlines():
            if "|" in line:
                t, st = line.split("|", 1)
                evs.append({"title": t.strip(), "start": st.strip()})
        return evs

    @staticmethod
    def notify(title: str, text: str) -> None:
        try:
            subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}" sound name "Glass"'], timeout=5)
        except Exception:
            pass


_watchdog: Watchdog | None = None


def meeting_prep_from_brain(event: dict) -> str | None:
    """Справка к встрече из базы знаний (если плагин jarvis-brain загружен): участники, проект, обещания."""
    try:
        import sys
        mod = next((m for n, m in sys.modules.items() if n.endswith("jarvis_brain") and hasattr(m, "brain")), None)
        if mod is None:
            return None
        b = mod.brain()
    except Exception:
        return None
    title = event.get("title", "")
    mat = b.reflect_material(title, limit=6)
    lines = []
    for e in mat["entities"][:3]:
        head = f"**{e['name']}**" + (f" — {e['summary']}" if e.get("summary") else "")
        lines.append(head)
        lines += [f"- {n}" for n in e["notes"][:3]]
    for n in mat["notes"][:4]:
        if not any(n["content"] in line for line in lines):
            lines.append(f"- {n['content']}")
    for ep in mat["episodes"][:1]:
        lines.append(f"_{ep['day']}: {ep['summary']}_")
    return "\n".join(lines) if lines else None


# ══════════════════════════════ slash-команды ══════════════════════════════

BRIEF_PROMPT = (
    "Сделай утренний брифинг в стиле JARVIS, коротко и по делу: "
    "1) поздоровайся по времени суток; 2) погода (навык weather: wttr.in через web_extract/terminal, город из контекста); 3) события календаря на сегодня (mac_calendar today); "
    "4) активные напоминания (mac_reminders list); 5) батарея и состояние системы (mac_battery); "
    "6) если есть непрочитанное в памяти/задачах — напомни. Заверши одной фразой-рекомендацией."
)


def register(ctx) -> None:
    # настройки
    for key in ("hud_url", "user_name", "city", "inject_context", "watchdog", "battery_threshold", "watch_calendar", "follow_focus",
                "triggers", "trigger_llm", "disk_min_gb", "idle_return_min", "screen_context"):
        try:
            val = ctx.get_config(key, default=None)
        except Exception:
            val = None
        if val is not None:
            if key == "hud_url":
                _hud.base_url = str(val)
            else:
                _cfg[key] = val

    # хуки
    ctx.register_hook("pre_llm_call", hook_pre_llm_call)
    ctx.register_hook("post_llm_call", hook_post_llm_call)
    ctx.register_hook("pre_tool_call", hook_pre_tool_call)
    ctx.register_hook("post_tool_call", hook_post_tool_call)
    ctx.register_hook("on_session_start", hook_session_start)
    for name, fn in (("on_stream_delta", hook_stream_delta), ("on_stream_end", hook_stream_end)):
        try:
            ctx.register_hook(name, fn)
        except Exception as e:  # стриминговые хуки есть не во всех версиях
            logger.debug("hook %s недоступен: %s", name, e)

    # инструменты
    ctx.register_tool(name="jarvis_hud", toolset=TOOLSET, schema=schemas.JARVIS_HUD, handler=tool_jarvis_hud)
    ctx.register_tool(name="jarvis_timer", toolset=TOOLSET, schema=schemas.JARVIS_TIMER, handler=tool_jarvis_timer)
    ctx.register_tool(name="jarvis_mode", toolset=TOOLSET, schema=schemas.JARVIS_MODE, handler=tool_jarvis_mode)
    ctx.register_tool(name="jarvis_update", toolset=TOOLSET, schema=schemas.JARVIS_UPDATE, handler=tool_jarvis_update)

    # бандл-скиллы плагина (jarvis-core:morning-briefing и т.д.)
    if _SKILLS_DIR.exists():
        for child in sorted(_SKILLS_DIR.iterdir()):
            md = child / "SKILL.md"
            if child.is_dir() and md.exists():
                try:
                    ctx.register_skill(child.name, md)
                except Exception as e:
                    logger.debug("register_skill(%s): %s", child.name, e)

    # slash-команды
    def cmd_brief(raw: str) -> str:
        try:
            ctx.inject_message(BRIEF_PROMPT, role="user")
            return ""  # ответ придёт как обычный ход агента
        except Exception:
            return BRIEF_PROMPT  # старые версии: просто вернуть текст подсказки

    def cmd_focus(raw: str) -> str:
        mode = (raw.strip() or "focus").lower()
        return tool_jarvis_mode({"mode": "normal" if mode in ("off", "выкл") else mode})

    def cmd_timer(raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return tool_jarvis_timer({"action": "list"})
        # /timer 10 чай  |  /timer 07:30 подъём
        head, _, label = raw.partition(" ")
        if ":" in head:
            return tool_jarvis_timer({"action": "set", "at": head, "label": label or "будильник"})
        try:
            return tool_jarvis_timer({"action": "set", "minutes": float(head), "label": label or "таймер"})
        except ValueError:
            return "Использование: /timer <минуты|HH:MM> [название]"

    for name, fn, desc in (
        ("brief", cmd_brief, "Утренний брифинг JARVIS"),
        ("focus", cmd_focus, "Режим фокуса: /focus | /focus off | /focus night"),
        ("timer", cmd_timer, "Таймер: /timer 10 чай | /timer 07:30 подъём"),
    ):
        try:
            ctx.register_command(name, fn, description=desc)
        except Exception as e:
            logger.debug("register_command(%s): %s", name, e)

    # восстановить таймеры после рестарта
    for t in state.active_timers(raw=True):
        _schedule_fire(t["label"], dt.datetime.fromisoformat(t["target"]))

    # локальный watchdog (батарея, календарь) — без вызовов LLM
    global _watchdog
    if _cfg.get("watchdog", True) and _watchdog is None:
        trig = None
        if _cfg.get("triggers", True):
            trig = Triggers(state_file=state._path().parent / "triggers.json",
                            disk_min_gb=int(_cfg.get("disk_min_gb") or 20), idle_min=int(_cfg.get("idle_return_min") or 90),
                            llm=bool(_cfg.get("trigger_llm", True)), notify=Watchdog.notify, emit=_hud.emit, get_mode=state.get_mode)
        _watchdog = Watchdog(battery_threshold=int(_cfg.get("battery_threshold") or 20),
                             watch_calendar=bool(_cfg.get("watch_calendar")),
                             follow_focus=bool(_cfg.get("follow_focus", True)),
                             meeting_prep=meeting_prep_from_brain, triggers=trig)
        _watchdog.start()

    _hud.emit("plugin.ready", {"name": "jarvis-core"})
    logger.info("jarvis-core загружен (HUD: %s)", _hud.base_url)
