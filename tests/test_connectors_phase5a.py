from __future__ import annotations

from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.connectors import base as connector_base
from scrapperlanas.services.connectors.ashby import AshbyConnector
from scrapperlanas.services.connectors.base import group_hiring_jobs_to_opportunity
from scrapperlanas.services.connectors.greenhouse import GreenhouseConnector
from scrapperlanas.services.connectors.hn_algolia import HNAlgoliaConnector
from scrapperlanas.services.connectors.lever import LeverConnector
from scrapperlanas.services.connectors.workable import WorkableConnector
from scrapperlanas.services.ingestion import NormalizedOpportunity
from scrapperlanas.services.pipeline import import_opportunities
from scrapperlanas.services.scoring import score_commercial_opportunity


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture()
def app(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": False,
            "AI_MAX_ENRICHMENTS_PER_RUN": 0,
        }
    )
    with app.app_context():
        db = get_db()
        db.execute("UPDATE source_policies SET enabled = 0")
        db.commit()
    return app


def _target(provider: str = "greenhouse") -> dict:
    return {
        "id": 7,
        "name": "Acme Data",
        "domain": "acme.example",
        "country": "US",
        "ats_provider": provider,
        "ats_slug": "acme",
        "careers_url": "https://acme.example/careers",
        "priority": "high",
    }


def test_hn_algolia_connector_normalizes_hit_with_evidence(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "hits": [
                    {
                        "objectID": "123",
                        "title": "Need help with internal dashboard automation",
                        "url": "",
                        "author": "founder42",
                        "story_text": "Looking for a consultant to help with a data pipeline.",
                        "created_at": "2026-05-18T10:00:00Z",
                    }
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = HNAlgoliaConnector().fetch({"queries": ["need help with"], "hits_per_page": 5})

    assert result.status == "ok"
    assert result.normalized_count == 1
    opportunity = result.opportunities[0]
    assert opportunity["source_type"] == "community_signal"
    assert opportunity["buyer_name"] == "founder42"
    assert opportunity["url"] == "https://news.ycombinator.com/item?id=123"
    assert opportunity["evidence_snippets"]
    assert "HN query match" in opportunity["pain_signals"][0]


def test_hn_paid_contractor_urgent_signal_scores_above_noise(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "hits": [
                    {
                        "objectID": "456",
                        "title": "Paid contractor needed urgently for automation dashboard",
                        "url": "https://news.ycombinator.com/item?id=456",
                        "author": "opsfounder",
                        "comment_text": "Budget available. Need Python data pipeline help asap.",
                        "created_at": "2026-05-18T10:00:00Z",
                    }
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = HNAlgoliaConnector().fetch({"queries": ["contractor"], "hits_per_page": 5})
    raw = result.opportunities[0]
    opportunity = NormalizedOpportunity(
        external_id=raw["external_id"],
        source_key="hn_algolia",
        source_label="Hacker News Algolia Signals",
        source_type=raw["source_type"],
        title=raw["title"],
        company=raw["company"],
        buyer_name=raw["buyer_name"],
        buyer_domain=raw["buyer_domain"],
        url=raw["url"],
        apply_url=raw["apply_url"],
        raw_text=raw["raw_text"],
        pain_signals=raw["pain_signals"],
        evidence_snippets=raw["evidence_snippets"],
        posted_at=raw["posted_at"],
    )

    score = score_commercial_opportunity(opportunity, base_score=45)

    assert score["score_total"] >= 45
    assert score["score_tier"] in {"A1", "A2", "B"}
    assert any("compra" in reason.lower() or "senales" in reason.lower() for reason in score["score_reasons"])


def test_greenhouse_fixture_groups_multiple_jobs_into_one_batch(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "jobs": [
                    {
                        "id": 1,
                        "title": "Senior Python Backend Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "updated_at": "2026-05-18T10:00:00Z",
                        "location": {"name": "Remote"},
                        "departments": [{"name": "Engineering"}],
                        "content": "<p>Build APIs and automation.</p>",
                    },
                    {
                        "id": 2,
                        "title": "Data Engineer",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                        "updated_at": "2026-05-18T10:00:00Z",
                        "location": {"name": "Remote"},
                        "departments": [{"name": "Data"}],
                        "content": "<p>Own ETL pipelines and analytics.</p>",
                    },
                    {
                        "id": 3,
                        "title": "Account Executive",
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
                        "updated_at": "2026-05-18T10:00:00Z",
                        "location": {"name": "Remote"},
                        "departments": [{"name": "Sales"}],
                        "content": "<p>Sell software.</p>",
                    },
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = GreenhouseConnector().fetch({"target_company": _target("greenhouse")})

    assert result.status == "ok"
    assert result.normalized_count == 1
    opportunity = result.opportunities[0]
    assert opportunity["source_type"] == "hiring_signal"
    assert len(opportunity["jobs_snapshot"]) == 2
    assert "Python" in opportunity["required_skills"]
    assert opportunity["url"].endswith("/greenhouse/2026-05-19") or "/greenhouse/" in opportunity["url"]


def test_lever_fixture_normalizes_buyer_domain_from_target_company(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            [
                {
                    "id": "abc",
                    "text": "Backend API Engineer",
                    "hostedUrl": "https://jobs.lever.co/acme/abc",
                    "applyUrl": "https://jobs.lever.co/acme/abc/apply",
                    "categories": {"team": "Engineering", "location": "Remote"},
                    "descriptionPlain": "Python APIs and integration automation.",
                }
            ]
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = LeverConnector().fetch({"target_company": _target("lever")})

    assert result.status == "ok"
    assert result.opportunities[0]["buyer_domain"] == "acme.example"
    assert result.opportunities[0]["buyer_name"] == "Acme Data"


def test_ashby_compensation_is_evidence_not_estimated_budget(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "jobs": [
                    {
                        "title": "AI Platform Engineer",
                        "jobUrl": "https://jobs.ashbyhq.com/acme/ai",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/ai/apply",
                        "department": "Engineering",
                        "location": "Remote",
                        "descriptionPlain": "Build AI automation and data pipelines.",
                        "isListed": True,
                        "compensation": {"scrapeableCompensationSalarySummary": "$120K - $150K"},
                    }
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = AshbyConnector().fetch({"target_company": _target("ashby")})
    opportunity = result.opportunities[0]

    assert opportunity["estimated_value"] is None
    assert any("Compensacion visible" in snippet for snippet in opportunity["evidence_snippets"])


def test_workable_without_token_respects_disabled_and_enabled_modes():
    connector = WorkableConnector()

    disabled = connector.fetch({"enabled": False, "target_company": _target("workable")})
    enabled = connector.fetch({"enabled": True, "target_company": _target("workable")})

    assert disabled.status == "no_matches"
    assert enabled.status == "auth_error"
    assert "WORKABLE_API_TOKEN" in enabled.error_message


def test_ats_batch_import_does_not_duplicate_same_target_day(app):
    opportunity = group_hiring_jobs_to_opportunity(
        source_name="greenhouse_jobs",
        provider="greenhouse",
        target_company=_target("greenhouse"),
        jobs=[
            {
                "title": "Python Data Engineer",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "department": "Data",
                "location": "Remote",
                "description": "Python ETL and dashboard automation.",
            }
        ],
    )
    assert opportunity is not None

    with app.app_context():
        db = get_db()
        first = import_opportunities(db, source_key="greenhouse_jobs", items=[opportunity], trigger="test")
        second = import_opportunities(db, source_key="greenhouse_jobs", items=[opportunity], trigger="test")
        total = db.execute("SELECT COUNT(*) AS total FROM opportunities WHERE source_key = 'greenhouse_jobs'").fetchone()["total"]

    assert first["created"] == 1
    assert second["updated"] == 1
    assert total == 1


def test_new_ats_opportunity_gets_buyer_account(app):
    opportunity = group_hiring_jobs_to_opportunity(
        source_name="lever_postings",
        provider="lever",
        target_company=_target("lever"),
        jobs=[
            {
                "title": "Automation Backend Engineer",
                "url": "https://jobs.lever.co/acme/abc",
                "department": "Engineering",
                "location": "Remote",
                "description": "APIs, Python, dashboards and workflow automation.",
            }
        ],
    )
    assert opportunity is not None

    with app.app_context():
        db = get_db()
        import_opportunities(db, source_key="lever_postings", items=[opportunity], trigger="test")
        row = db.execute(
            """
            SELECT o.buyer_account_id, b.name, b.normalized_domain
            FROM opportunities o
            JOIN buyer_accounts b ON b.id = o.buyer_account_id
            WHERE o.source_key = 'lever_postings'
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["buyer_account_id"]
    assert row["name"] == "Acme Data"
    assert row["normalized_domain"] == "acme.example"
