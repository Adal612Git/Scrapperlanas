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


class DummyJsonResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self.payload


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
    assert rows["github_issues"]["enabled"] == 0
    assert rows["sam_gov"]["enabled"] == 0
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


def test_github_issues_connector_keeps_commercial_signals(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "github.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "GITHUB_API_URL": "https://api.github.test",
        }
    )

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == "https://api.github.test/search/issues"
        assert headers["Accept"] == "application/vnd.github+json"
        assert params["q"] == '"paid help" python is:issue is:open'
        return DummyJsonResponse(
            {
                "items": [
                    {
                        "id": 10,
                        "html_url": "https://github.com/acme/ops/issues/10",
                        "repository_url": "https://api.github.com/repos/acme/ops",
                        "title": "Paid help needed for Python automation",
                        "body": "We need a contractor for API integration. Budget USD 1500.",
                        "labels": [{"name": "help wanted"}],
                        "updated_at": "2026-05-15T10:00:00Z",
                    },
                    {
                        "id": 11,
                        "html_url": "https://github.com/acme/ops/pull/11",
                        "repository_url": "https://api.github.com/repos/acme/ops",
                        "title": "Pull request",
                        "pull_request": {"url": "https://api.github.com/pulls/11"},
                    },
                    {
                        "id": 12,
                        "html_url": "https://github.com/acme/ops/issues/12",
                        "repository_url": "https://api.github.com/repos/acme/ops",
                        "title": "Good first issue",
                        "body": "Volunteer cleanup.",
                        "labels": [{"name": "good first issue"}],
                        "updated_at": "2026-05-15T10:00:00Z",
                    },
                ]
            }
        )

    monkeypatch.setattr("scrapperlanas.services.ingestion.requests.get", fake_get)
    policy_row = {
        "source_key": "github_issues",
        "display_name": "GitHub Lead Signals",
        "risk_level": "medium",
        "config_json": json.dumps(
            {
                "queries": ['"paid help" python is:issue is:open'],
                "include_terms": ["python", "automation", "api", "paid", "contractor"],
                "exclude_terms": ["good first issue", "volunteer"],
                "commercial_signal_terms": ["paid", "budget", "contractor"],
                "require_commercial_signal": True,
                "per_page": 10,
                "max_items": 10,
            },
            ensure_ascii=False,
        ),
    }

    with app.app_context():
        opportunities = connector_registry()["github_issues"].fetch(policy_row)

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.title == "Paid help needed for Python automation"
    assert opportunity.company == "acme/ops"
    assert opportunity.budget_min == 1500
    assert opportunity.url == "https://github.com/acme/ops/issues/10"


def test_sam_gov_connector_fetches_contract_opportunities(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "sam.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "SAM_API_KEY": "sam-test-key",
            "SAM_OPPORTUNITIES_URL": "https://api.sam.test/opportunities/v2/search",
        }
    )

    def fake_get(url, headers=None, params=None, timeout=None):
        assert url == "https://api.sam.test/opportunities/v2/search"
        assert params["api_key"] == "sam-test-key"
        assert params["postedFrom"]
        assert params["postedTo"]
        return DummyJsonResponse(
            {
                "opportunitiesData": [
                    {
                        "noticeId": "notice-001",
                        "title": "Software development and API integration support",
                        "fullParentPathName": "GENERAL SERVICES ADMINISTRATION",
                        "type": "Combined Synopsis/Solicitation",
                        "baseType": "Solicitation",
                        "postedDate": "2026-05-15",
                        "naicsCode": "541511",
                        "classificationCode": "DA01",
                        "responseDeadLine": "2026-06-01",
                        "uiLink": "https://sam.gov/opp/notice-001/view",
                        "pointOfContact": [
                            {
                                "fullName": "Jane Buyer",
                                "email": "jane@example.gov",
                            }
                        ],
                    },
                    {
                        "noticeId": "notice-002",
                        "title": "Janitorial services",
                        "fullParentPathName": "OTHER AGENCY",
                        "type": "Solicitation",
                        "postedDate": "2026-05-15",
                        "uiLink": "https://sam.gov/opp/notice-002/view",
                    },
                ]
            }
        )

    monkeypatch.setattr("scrapperlanas.services.ingestion.requests.get", fake_get)
    policy_row = {
        "source_key": "sam_gov",
        "display_name": "SAM.gov Contract Opportunities",
        "risk_level": "low",
        "config_json": json.dumps(
            {
                "query_terms": ["software development"],
                "notice_types": ["k"],
                "posted_from_days": 14,
                "limit_per_query": 10,
                "max_items": 10,
                "include_terms": ["software", "api", "integration"],
                "exclude_terms": ["janitorial"],
            },
            ensure_ascii=False,
        ),
    }

    with app.app_context():
        opportunities = connector_registry()["sam_gov"].fetch(policy_row)

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.title == "Software development and API integration support"
    assert opportunity.company == "GENERAL SERVICES ADMINISTRATION"
    assert opportunity.url == "https://sam.gov/opp/notice-001/view"
    assert opportunity.posted_at.startswith("2026-05-15")
