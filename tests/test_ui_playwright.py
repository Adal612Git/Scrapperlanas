from __future__ import annotations

import socket
import threading
from contextlib import closing
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from scrapperlanas import create_app
from scrapperlanas.db import get_db


pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


class _ServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int):
        super().__init__(daemon=True)
        self.server = make_server(host, port, app)

    def run(self) -> None:
        self.server.serve_forever()

    def stop(self) -> None:
        self.server.shutdown()


@pytest.fixture()
def live_loto_app(tmp_path: Path):
    database_path = tmp_path / "playwright.sqlite3"
    port = _free_port()
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(database_path),
            "SECRET_KEY": "playwright-test-secret",
            "CRON_SECRET": "playwright-cron-secret",
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
            "AUTOMATION_MODE": "manual",
            "AUTOMATION_RUN_ON_STARTUP": False,
            "AI_PROVIDER": "heuristic",
            "AI_EMBED_PROVIDER": "none",
            "LOTO_SIGNAL_BASE_URL": f"http://127.0.0.1:{port}",
            "N8N_IMPORT_WEBHOOK_PATH": "loto-signal/import",
        }
    )
    with app.app_context():
        db = get_db()
        db.execute("UPDATE source_policies SET enabled = 0")
        db.execute("UPDATE source_policies SET enabled = 1 WHERE source_key = 'sample_feed'")
        db.commit()

    server = _ServerThread(app, "127.0.0.1", port)
    server.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


def test_loto_signal_playwright_smoke(live_loto_app: str, tmp_path: Path):
    screenshot_dir = Path(".tmp") / "playwright"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 980})
        page = context.new_page()

        page.goto(f"{live_loto_app}/auth/login", wait_until="networkidle")
        expect(page.get_by_role("heading", name="Buyer Intelligence Console")).to_be_visible()
        expect(page.locator("img").first).to_be_visible()

        page.goto(f"{live_loto_app}/auth/register", wait_until="networkidle")
        page.locator('input[name="email"]').fill("playwright@example.com")
        page.locator('input[name="password"]').fill("supersecret")
        page.locator('input[name="confirm_password"]').fill("supersecret")
        page.get_by_role("button", name="Crear cuenta").click()

        expect(page.locator("h1").filter(has_text="Inbox inteligente")).to_be_visible()
        expect(page.get_by_text("Copiloto comercial")).to_be_visible()
        page.screenshot(path=str(screenshot_dir / "loto-signal-dashboard-empty-desktop.png"), full_page=True)

        page.get_by_role("button", name="Ejecutar ingesta", exact=True).click()
        expect(page.get_by_text("Ingesta completada")).to_be_visible(timeout=15_000)
        expect(page.locator(".loto-row-title").filter(has_text="Python scraper para directorios B2B con export CSV")).to_be_visible()
        expect(page.get_by_text("Redactar mensaje").first).to_be_visible()
        page.screenshot(path=str(screenshot_dir / "loto-signal-dashboard-data-desktop.png"), full_page=True)

        mobile = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
        mobile_page = mobile.new_page()
        mobile_page.goto(f"{live_loto_app}/auth/login", wait_until="networkidle")
        expect(mobile_page.get_by_role("heading", name="Buyer Intelligence Console")).to_be_visible()
        mobile_page.screenshot(path=str(screenshot_dir / "loto-signal-login-mobile.png"), full_page=True)

        mobile.close()
        context.close()
        browser.close()


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
