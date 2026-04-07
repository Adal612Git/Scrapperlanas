from __future__ import annotations

import json
from pathlib import Path

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.ingestion import connector_registry


class DummyHtmlResponse:
    def __init__(self, *, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_default_source_mix_prioritizes_freelance_channels(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "defaults.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
        }
    )

    with app.app_context():
        db = get_db()
        rows = {
            row["source_key"]: row
            for row in db.execute(
                "SELECT source_key, enabled, notes FROM source_policies"
            ).fetchall()
        }

    assert rows["reddit"]["enabled"] == 1
    assert rows["workana_projects"]["enabled"] == 1
    assert rows["greenhouse"]["enabled"] == 0
    assert rows["lever"]["enabled"] == 0
    assert rows["weworkremotely"]["enabled"] == 0
    assert rows["hackernews_jobs"]["enabled"] == 0
    assert "Upwork" in rows["email_alerts"]["notes"]


def test_workana_connector_fetches_public_projects(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "workana.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
        }
    )

    listing_html = """
    <html>
      <body>
        <a href="/job/python-scraper-directorios-b2b">Python scraper para directorios B2B</a>
        <a href="/job/virtual-assistant-sales-ops">Virtual assistant para ventas</a>
      </body>
    </html>
    """
    scraper_job_html = """
    <html>
      <head>
        <title>Python scraper para directorios B2B - Se Busca Freelancer - Workana</title>
        <meta name="description" content="Proyecto remoto para scraper en Python con automatizacion, APIs y presupuesto USD 2400." />
      </head>
      <body>
        <h1>Python scraper para directorios B2B</h1>
        <div>Publicado el 08 Julio, 2025</div>
        <section>Necesitamos un scraper en Python para directorios publicos, con API y automatizacion.</section>
      </body>
    </html>
    """
    assistant_job_html = """
    <html>
      <head><title>Virtual assistant para ventas - Workana</title></head>
      <body>
        <h1>Virtual assistant para ventas</h1>
        <div>Publicado el 03 Julio, 2025</div>
      </body>
    </html>
    """

    def fake_get(url, headers=None, timeout=None):
        if "jobs?category=it-programming&skills=python" in url:
            return DummyHtmlResponse(text=listing_html)
        if url.endswith("/job/python-scraper-directorios-b2b"):
            return DummyHtmlResponse(text=scraper_job_html)
        if url.endswith("/job/virtual-assistant-sales-ops"):
            return DummyHtmlResponse(text=assistant_job_html)
        return DummyHtmlResponse(text="<html></html>", status_code=404)

    monkeypatch.setattr("scrapperlanas.services.ingestion.requests.get", fake_get)

    policy_row = {
        "source_key": "workana_projects",
        "display_name": "Workana Public Projects",
        "risk_level": "low",
        "config_json": json.dumps(
            {
                "search_urls": ["https://www.workana.com/es/jobs?category=it-programming&skills=python"],
                "include_terms": ["python", "scraping", "automation", "api"],
                "exclude_terms": ["virtual assistant", "ventas"],
                "candidate_limit": 10,
                "max_items": 10,
            },
            ensure_ascii=False,
        ),
    }

    with app.app_context():
        opportunities = connector_registry()["workana_projects"].fetch(policy_row)

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.title == "Python scraper para directorios B2B"
    assert opportunity.url == "https://www.workana.com/job/python-scraper-directorios-b2b"
    assert opportunity.budget_min == 2400
    assert opportunity.posted_at == "2025-07-08T00:00:00+00:00"
