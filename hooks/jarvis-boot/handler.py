"""
Gateway-хук JARVIS.

* gateway:startup → запускает одноразового агента с инструкциями из $HERMES_HOME/BOOT.md
                    (проверка cron, состояния системы, приветствие) — в фоне, не блокируя gateway.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger("hooks.jarvis-boot")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
BOOT_FILE = HERMES_HOME / "BOOT.md"
HUD_URL = os.environ.get("JARVIS_HUD_URL", "http://127.0.0.1:8765")


def _hud(event: str, data: dict) -> None:
    try:
        req = urllib.request.Request(
            HUD_URL + "/api/event",
            data=json.dumps({"event": event, "data": data}, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1).close()
    except Exception:  # HUD может быть выключен
        pass


_ERROR_RE = re.compile(r"^\s*(HTTP\s*\d{3}|Error code|error:|\[?ERROR\]?|Traceback|APIError|RateLimit)", re.I)


def _looks_like_error(text: str) -> bool:
    """Ответ агента — это текст ошибки, а не сообщение пользователю."""
    return not text or bool(_ERROR_RE.match(text)) or (len(text) < 80 and "error" in text.lower())


def _run_boot(content: str) -> None:
    try:
        from gateway.run import _resolve_gateway_model, _resolve_runtime_agent_kwargs  # type: ignore
        from run_agent import AIAgent  # type: ignore

        agent = AIAgent(
            model=_resolve_gateway_model(),
            **_resolve_runtime_agent_kwargs(),
            platform="gateway",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=15,
        )
        prompt = (
            "Ты JARVIS. Выполни стартовый чек-лист ниже. Если сообщать нечего — ответь ровно [SILENT].\n\n---\n"
            f"{content}\n---"
        )
        result = agent.run_conversation(prompt)
        text = (result.get("final_response") or "").strip()
        if _looks_like_error(text):
            # ошибка модели/сети — в лог, а не на экран (раньше HUD показывал «HTTP 405: Error code: 405»)
            logger.error("BOOT.md: агент вернул ошибку вместо ответа: %s", text[:300])
        elif text and text.upper().strip("[] ") not in {"SILENT", "NO_REPLY"}:
            logger.info("BOOT.md: %s", text[:300])
            # id «boot» → при перезапуске gateway панель заменяется, а не добавляется ещё одна
            _hud("panel.show", {"id": "boot", "kind": "markdown", "title": "При старте", "content": text, "position": "right", "ttl": 120})
        else:
            logger.info("BOOT.md: нечего сообщать")
    except Exception as e:
        logger.error("BOOT.md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    if event_type == "gateway:startup":
        _hud("session.start", {"session": "gateway", "model": "", "platform": ",".join(context.get("platforms", []))})
        if BOOT_FILE.exists() and BOOT_FILE.read_text(encoding="utf-8").strip():
            threading.Thread(target=_run_boot, args=(BOOT_FILE.read_text(encoding="utf-8"),), name="jarvis-boot", daemon=True).start()
    # agent:start / agent:end на HUD не зеркалим: то же самое уже делает плагин jarvis-core
    # через pre/post_llm_call — иначе каждый ход из Telegram/Discord появлялся дважды.
