"""
E2E: HUD в настоящем браузере (Playwright/Chromium) против hud/server.py --demo и поддельного Hermes API.

Проверяем контракт, который не ловят unit-тесты: страница грузится без ошибок JS, виджеты отрисованы,
чат через SSE-стрим доходит до #reply, события /api/event (panel.show, alert, mode.set) меняют DOM,
Esc останавливает речь, ошибка апстрима показывается человеку, а не глотается.

Запуск локально:  pip install playwright && python -m playwright install chromium && python -m pytest tests/e2e -q
Пропускается, если playwright не установлен (обычный `pytest tests` его не требует).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHermes(BaseHTTPRequestHandler):
    """OpenAI-совместимый /v1/chat/completions со стримом; режим задаётся классовым атрибутом."""
    mode = "ok"

    def log_message(self, *a):  # тишина
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"status":"ok"}'); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if FakeHermes.mode == "error":
            self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(b'{"error":{"message":"upstream exploded"}}'); return
        user = body["messages"][-1]["content"]
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for tok in (f"Вы сказали: {user}. ", "Слушаю, сэр."):
            chunk = {"choices": [{"delta": {"content": tok}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()); self.wfile.flush(); time.sleep(0.05)
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    home = tmp_path_factory.mktemp("hermes_home")
    api_port, hud_port = _free_port(), _free_port()
    (home / ".env").write_text("API_SERVER_KEY=test-key\n")
    api = ThreadingHTTPServer(("127.0.0.1", api_port), FakeHermes); api.daemon_threads = True
    threading.Thread(target=api.serve_forever, daemon=True).start()
    env = {**os.environ, "HERMES_HOME": str(home), "JARVIS_HERMES_URL": f"http://127.0.0.1:{api_port}", "JARVIS_STATE_DIR": str(home)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "hud" / "server.py"), "--port", str(hud_port), "--demo"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = f"http://127.0.0.1:{hud_port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=0.5); break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill(); pytest.fail("HUD не поднялся: " + (proc.stdout.read() if proc.stdout else ""))
    yield url
    proc.terminate(); api.shutdown()


@pytest.fixture(scope="module")
def page(stack):
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors  # type: ignore[attr-defined]
        pg.goto(stack); pg.wait_for_selector("#input")
        yield pg
        browser.close()


def _event(stack, event, data):
    req = urllib.request.Request(stack + "/api/event", data=json.dumps({"event": event, "data": data}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=3)


def test_page_loads_without_js_errors(page):
    assert page.errors == [], page.errors
    for sel in ("#input", "#reply", "#knowBody", "#sysBody", "#ttsBtn"):
        assert page.locator(sel).count() == 1, sel
    # demo-виджеты заполнены
    assert "Заметки" in page.inner_text("#knowBody") and "Файлы" in page.inner_text("#knowBody")


def test_chat_streams_reply(page):
    page.fill("#input", "который час")
    page.press("#input", "Enter")
    page.wait_for_function("document.querySelector('#reply').innerText.includes('Слушаю, сэр')", timeout=10000)
    assert "который час" in page.inner_text("#reply")
    assert page.errors == []


def test_upstream_error_is_shown(page):
    FakeHermes.mode = "error"
    try:
        page.fill("#input", "сломайся")
        page.press("#input", "Enter")
        page.wait_for_selector("#reply.err", timeout=10000)
        assert page.inner_text("#reply").strip()
    finally:
        FakeHermes.mode = "ok"


def test_events_drive_dom(page, stack):
    _event(stack, "panel.show", {"kind": "markdown", "title": "ТЕСТ ПАНЕЛИ", "content": "**жирный** текст", "position": "right"})
    page.wait_for_function("document.body.innerText.includes('ТЕСТ ПАНЕЛИ')", timeout=5000)
    _event(stack, "mode.set", {"mode": "focus", "source": "test"})
    page.wait_for_function("document.querySelector('#sysBody').innerText.includes('кратко')", timeout=5000)
    _event(stack, "alert", {"kind": "battery", "text": "Заряд 9%"})
    page.wait_for_function("document.body.innerText.includes('Заряд 9%')", timeout=5000)
    # лента активности (клавиша A) показывает событие хранилища
    page.locator("body").click(position={"x": 5, "y": 5}); page.keyboard.press("a")
    _event(stack, "vault.update", {"action": "scan", "indexed": 3})
    page.wait_for_function("document.body.innerText.includes('хранилище · проиндексировано 3')", timeout=5000)
    assert page.errors == []


def test_escape_blurs_input_then_clears_reply(page):
    page.fill("#input", "что-то")
    page.keyboard.press("Escape")                       # в поле ввода: Esc снимает фокус
    assert page.evaluate("document.activeElement.id") != "input"
    page.keyboard.press("Escape")                       # вне поля: Esc очищает ответ/останавливает речь
    assert page.inner_text("#reply").strip() == ""
    assert page.errors == []
