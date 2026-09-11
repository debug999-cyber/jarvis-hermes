"""
Событийная проактивность: JARVIS реагирует на события, а не «просыпается по будильнику».

Раньше: cron-heartbeat каждые 45 минут → вызов модели, чаще всего впустую (NO_REPLY).
Теперь: локальные детекторы (без LLM, дёшево, каждые 60 с) замечают ИЗМЕНЕНИЯ и только тогда:
  * либо сразу уведомляют (диск, питание) — вообще без модели;
  * либо один раз зовут модель с конкретным поводом (`hermes chat -s jarvis/heartbeat -q "Событие: …"`) —
    новый файл в хранилище, возвращение к Mac утром, встреча через 10 минут.

Детекторы (каждый возвращает список событий {kind, text, llm: bool, urgent: bool}):
  disk.low          свободно < disk_min_gb (по умолчанию 20) — уведомление, раз в 6 ч
  power.unplugged   отключили питание при заряде ≤ 30 % — уведомление
  vault.inbox       появились новые файлы в ~/JARVIS/inbox → модель: прочитать и предложить, что сделать
  user.returned     Mac был без ввода ≥ idle_min минут (по умолчанию 90) и пользователь вернулся;
                    если это первый раз за день после 06:00 → утренний брифинг; иначе — короткое «что изменилось»
  calendar.soon     приходит из Watchdog (событие через lead_min) — модель готовит справку

Правила тишины: LLM-триггеры не чаще одного раза в cooldown_min (15) минут; в режимах focus/night
срабатывают только urgent. Все состояния — в памяти процесса + небольшой файл triggers.json
(чтобы после рестарта не сработал «вернулся» и «новый файл» на всё подряд).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_SKILL = "jarvis/heartbeat"


class Triggers:
    def __init__(self, state_file: Path, vault_root: Path | None = None, disk_min_gb: int = 20, idle_min: int = 90,
                 cooldown_min: int = 15, llm: bool = True, notify=None, emit=None, get_mode=None, runner=None):
        self.state_file = state_file
        self.vault_root = vault_root or Path(os.environ.get("JARVIS_VAULT_DIR") or "~/JARVIS").expanduser()
        self.disk_min_gb = disk_min_gb
        self.idle_min = idle_min
        self.cooldown = cooldown_min * 60
        self.llm_enabled = llm
        self.notify = notify or (lambda title, text: None)
        self.emit = emit or (lambda event, data: None)
        self.get_mode = get_mode or (lambda: "normal")
        self.runner = runner or self._run_hermes  # callable(prompt) -> str | None; подменяется в тестах
        self.st = self._load()
        self._was_idle = False
        self._last_power: bool | None = None
        self._lock = threading.Lock()

    # ── состояние ──
    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {"last_llm": 0, "last_disk_alert": 0, "brief_day": ""}  # seen_inbox появится после первого прохода

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self.st, ensure_ascii=False))
        except OSError:
            pass

    # ── детекторы ──
    def detect_disk(self, now: float) -> list[dict]:
        try:
            free_gb = shutil.disk_usage(str(Path.home())).free / 1e9
        except OSError:
            return []
        if free_gb < self.disk_min_gb and now - self.st.get("last_disk_alert", 0) > 6 * 3600:
            self.st["last_disk_alert"] = now
            return [{"kind": "disk.low", "text": f"На диске осталось {free_gb:.0f} ГБ. Рекомендую освободить место, сэр.",
                     "llm": False, "urgent": free_gb < 5}]
        return []

    def detect_power(self, pct: int | None, charging: bool) -> list[dict]:
        prev, self._last_power = self._last_power, charging
        if prev is True and charging is False and pct is not None and pct <= 30:
            return [{"kind": "power.unplugged", "text": f"Питание отключено, заряд {pct}%.", "llm": False, "urgent": False}]
        return []

    def detect_inbox(self) -> list[dict]:
        inbox = self.vault_root / "inbox"
        if not inbox.is_dir():
            return []
        seen = set(self.st.get("seen_inbox") or [])
        current = sorted(p.name for p in inbox.iterdir() if not p.name.startswith(".") and p.is_file())
        first_run = "seen_inbox" not in self.st
        new = [n for n in current if n not in seen]
        self.st["seen_inbox"] = current[-500:]
        if first_run or not new:
            return []
        names = ", ".join(new[:5]) + (f" и ещё {len(new) - 5}" if len(new) > 5 else "")
        return [{"kind": "vault.inbox", "text": f"Новые файлы в хранилище: {names}", "llm": True, "urgent": False,
                 "prompt": (f"Событие: пользователь положил в ~/JARVIS/inbox новые файлы: {names}. "
                            "1) vault_manage reindex, затем vault_manage pending. 2) Для каждого нового файла: vault_read (первые ~80 строк), "
                            "brain_remember(kind='document', content='<имя файла>: <суть в 1–2 предложениях, ключевые даты/суммы/имена>', "
                            "tags='vault,inbox', importance=2), затем vault_manage summarized(file_id, note_id). "
                            "3) Ответь пользователю одним-двумя предложениями: что это и что с этим можно сделать "
                            "(например: «это договор с Acme до 2026 — записать срок в память? разложить в inbox/договоры?»). "
                            "Ничего не перемещай и не удаляй без просьбы. Если файлы служебные/пустые — NO_REPLY.")}]

    def detect_return(self, idle_sec: float | None, now: float) -> list[dict]:
        if idle_sec is None:
            return []
        idle_now = idle_sec >= self.idle_min * 60
        returned = self._was_idle and not idle_now
        self._was_idle = idle_now
        if not returned:
            return []
        today = dt.date.fromtimestamp(now).isoformat()
        hour = dt.datetime.fromtimestamp(now).hour
        if self.st.get("brief_day") != today and 6 <= hour < 12:
            self.st["brief_day"] = today
            return [{"kind": "user.returned", "text": "С возвращением, сэр.", "llm": True, "urgent": False,
                     "prompt": ("Событие: пользователь вернулся к Mac, это его первое появление сегодня. Сделай короткий утренний "
                                "брифинг (3–5 предложений, без markdown): погода (навык weather / wttr.in), события календаря сегодня "
                                "(mac_calendar today), просроченные напоминания, батарея. Заверши одной фразой: что важнее всего сегодня.")}]
        return [{"kind": "user.returned", "text": "", "llm": True, "urgent": False,
                 "prompt": ("Событие: пользователь вернулся к Mac после перерыва. Проверь HEARTBEAT.md по навыку heartbeat: "
                            "если за время отсутствия ничего не требует внимания — ответь ровно NO_REPLY; иначе одно-два предложения.")}]

    # ── исполнение ──
    def handle(self, events: list[dict], now: float) -> list[str]:
        """Уведомить / позвать модель; возвращает список kind сработавших событий (для тестов)."""
        fired = []
        mode = self.get_mode()
        quiet = mode in ("focus", "night", "presentation")
        for ev in events:
            if quiet and not ev.get("urgent"):
                logger.debug("trigger %s подавлен режимом %s", ev["kind"], mode)
                continue
            fired.append(ev["kind"])
            if ev.get("text"):
                self.emit("alert", {"kind": ev["kind"], "text": ev["text"]})
            if ev.get("llm") and self.llm_enabled and ev.get("prompt"):
                if now - self.st.get("last_llm", 0) < self.cooldown:
                    logger.debug("trigger %s: cooldown", ev["kind"])
                    continue
                self.st["last_llm"] = now
                threading.Thread(target=self._llm, args=(ev,), name=f"jarvis-trigger-{ev['kind']}", daemon=True).start()
            elif ev.get("text"):
                self.notify("JARVIS", ev["text"])
        self._save()
        return fired

    def _llm(self, ev: dict) -> None:
        try:
            text = (self.runner(ev["prompt"]) or "").strip()
        except Exception as e:
            logger.debug("trigger llm %s: %s", ev["kind"], e)
            return
        if not text or text.upper().strip("[] .") in {"NO_REPLY", "SILENT"}:
            return
        self.notify("JARVIS", text[:230])
        self.emit("turn.end", {"text": text, "source": "cron", "trigger": ev["kind"]})

    @staticmethod
    def _run_hermes(prompt: str) -> str | None:
        hermes = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
        try:
            p = subprocess.run([hermes, "chat", "-s", HEARTBEAT_SKILL, "-q", prompt], capture_output=True, text=True, timeout=240,
                               env={**os.environ, "JARVIS_TRIGGER": "1"})
            return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else None
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("hermes chat: %s", e)
            return None

    # ── системные датчики ──
    @staticmethod
    def idle_seconds() -> float | None:
        """Секунды с последнего ввода (macOS: ioreg HIDIdleTime, наносекунды). None — недоступно."""
        if sys.platform != "darwin":
            return None
        try:
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True, text=True, timeout=3).stdout
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    return int(line.rsplit("=", 1)[1].strip()) / 1e9
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        return None

    def tick(self, battery: tuple[int | None, bool] | None = None, now: float | None = None, idle: float | None = None) -> list[str]:
        now = now or time.time()
        with self._lock:
            events: list[dict] = []
            events += self.detect_disk(now)
            if battery:
                events += self.detect_power(*battery)
            events += self.detect_inbox()
            events += self.detect_return(idle if idle is not None else self.idle_seconds(), now)
            return self.handle(events, now)
