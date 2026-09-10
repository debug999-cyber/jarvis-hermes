"""
Gateway-хук JARVIS.

* gateway:startup → запускает одноразового агента с инструкциями из ~/.hermes/BOOT.md
                    (проверка cron, состояния системы, приветствие) — в фоне, не блокируя gateway.
* agent:start/end → дублирует активность gateway-сессий (Telegram, Discord…) на HUD,
                    чтобы на экране было видно, что JARVIS работает, даже если команда пришла с телефона.
"""
from __future__ import annotations

import json
import logging
import os
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
    except Exception:  # noqa: BLE001 — HUD может быть выключен
        pass


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
        if text and text.upper() not in {"[SILENT]", "SILENT"}:
            logger.info("BOOT.md: %s", text[:300])
            _hud("panel.show", {"kind": "markdown", "title": "BOOT", "content": text, "position": "right", "ttl": 60})
        else:
            logger.info("BOOT.md: нечего сообщать")
    except Exception as e:  # noqa: BLE001
        logger.error("BOOT.md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    if event_type == "gateway:startup":
        _hud("session.start", {"session": "gateway", "model": "", "platform": ",".join(context.get("platforms", []))})
        if BOOT_FILE.exists() and BOOT_FILE.read_text(encoding="utf-8").strip():
            threading.Thread(target=_run_boot, args=(BOOT_FILE.read_text(encoding="utf-8"),), name="jarvis-boot", daemon=True).start()
    elif event_type == "agent:start":
        _hud("turn.start", {"text": context.get("message", ""), "source": context.get("platform", "gateway")})
    elif event_type == "agent:end":
        _hud("turn.end", {"text": context.get("response", ""), "source": context.get("platform", "gateway")})
