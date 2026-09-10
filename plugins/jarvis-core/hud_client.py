"""
Клиент HUD: отправляет события на локальный HUD-сервер (hud/server.py).

Особенности:
  * неблокирующий — отправка в фоновом потоке через очередь, хук возвращается мгновенно;
  * «тихий» — если HUD не запущен, события отбрасываются без ошибок и без спама в логах;
  * без внешних зависимостей (urllib).
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class HudClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8765", max_queue: int = 500):
        self.base_url = base_url.rstrip("/")
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._down_until = 0.0
        self._worker = threading.Thread(target=self._loop, name="jarvis-hud-client", daemon=True)
        self._worker.start()

    # публичный API ---------------------------------------------------------

    def emit(self, event: str, data: dict | None = None) -> bool:
        """Поставить событие в очередь. Возвращает False, если HUD недоступен/очередь полна."""
        if time.time() < self._down_until:
            return False
        try:
            self._q.put_nowait({"event": event, "data": data or {}, "ts": time.time()})
            return True
        except queue.Full:
            return False

    # внутреннее ------------------------------------------------------------

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            try:
                self._post(item)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                # HUD не запущен — молчим 10 секунд, чтобы не долбить сокет
                self._down_until = time.time() + 10
                # выбросить накопившееся
                while not self._q.empty():
                    try:
                        self._q.get_nowait()
                    except queue.Empty:
                        break
            except Exception as e:  # noqa: BLE001
                logger.debug("HUD emit failed: %s", e)

    def _post(self, item: dict) -> None:
        body = json.dumps(item, ensure_ascii=False).encode()
        req = urllib.request.Request(
            self.base_url + "/api/event",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=1.5):
            pass
