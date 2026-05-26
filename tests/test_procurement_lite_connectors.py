from __future__ import annotations

from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.connectors import base as connector_base
from scrapperlanas.services.connectors.ted_eu import TEDEUConnector
from scrapperlanas.services.connectors.uk_contracts_finder import UKContractsFinderConnector
from scrapperlanas.services.connectors.uk_find_tender import UKFindTenderConnector
from scrapperlanas.services.connectors.worldbank import WorldBankProcurementConnector
from scrapperlanas.services.pipeline import import_opportunities


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


def _ocds_release(*, title: str = "Data platform and dashboard services", cpv: str = "72200000") -> dict:
    return {
        "ocid": "ocds-test-001",
        "id": "NOTICE-001",
        "date": "2026-05-18T10:00:00Z",
        "tag": ["tender"],
        "buyer": {"name": "Digital Services Agency"},
        "parties": [
            {
                "id": "GB-TEST",
                "name": "Digital Services Agency",
                "roles": ["buyer"],
                "address": {"countryName": "United Kingdom"},
                "contactPoint": {"email": "procurement@example.gov.uk"},
            }
        ],
        "tender": {
            "id": "DSA-2026-01",
            "title": title,
            "description": "Build a reporting dashboard, APIs and cloud data integration platform.",
            "classification": {"scheme": "CPV", "id": cpv, "description": "Software programming and consultancy services"},
            "value": {"amount": 125000, "currency": "GBP"},
            "tenderPeriod": {"endDate": "2026-06-15T12:00:00Z"},
            "status": "active",
            "items": [
                {
                    "id": "1",
                    "additionalClassifications": [
                        {"scheme": "CPV", "id": cpv, "description": "Software programming and consultancy services"}
                    ],
                    "deliveryAddresses": [{"countryName": "United Kingdom"}],
                }
            ],
        },
    }


def test_ted_eu_connector_normalizes_structured_notice(monkeypatch):
    def fake_post(*_args, **_kwargs):
        return FakeResponse(
            {
                "results": [
                    {
                        "publication-number": "123456-2026",
                        "notice-title": "Cloud data platform and dashboard implementation",
                        "buyer-name": "European Digital Office",
                        "buyer-country": "BE",
                        "publication-date": "2026-05-18",
                        "deadline-receipt-tender": "2026-06-20T12:00:00Z",
                        "estimated-value": 250000,
                        "estimated-value-cur": "EUR",
                        "classification-cpv": ["72200000"],
                        "description": "Implementation of cloud APIs, dashboards and reporting automation.",
                    }
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "post", fake_post)
    result = TEDEUConnector().fetch({"query_terms": ["software"], "limit": 5})

    assert result.status == "ok"
    opportunity = result.opportunities[0]
    assert opportunity["source_type"] == "procurement"
    assert opportunity["buyer_name"] == "European Digital Office"
    assert opportunity["estimated_value"] == 250000
    assert opportunity["currency"] == "EUR"
    assert opportunity["deadline_at"] == "2026-06-20T12:00:00Z"
    assert "pdf" not in opportunity["url"].lower()


def test_uk_find_tender_normalizes_ocds_release(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse({"releases": [_ocds_release()]})

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = UKFindTenderConnector().fetch({"limit": 5})

    assert result.status == "ok"
    opportunity = result.opportunities[0]
    assert opportunity["source_type"] == "procurement"
    assert opportunity["buyer_name"] == "Digital Services Agency"
    assert opportunity["estimated_value"] == 125000
    assert "email:procurement@example.gov.uk" in opportunity["contact_signals"]
    assert "Dashboard" in opportunity["required_skills"]


def test_uk_contracts_finder_skips_award_only_by_default(monkeypatch):
    award = _ocds_release()
    award["tag"] = ["award"]
    award["tender"]["status"] = "complete"

    def fake_get(*_args, **_kwargs):
        return FakeResponse({"releases": [award]})

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = UKContractsFinderConnector().fetch({"limit": 5})

    assert result.status == "no_matches"
    assert result.raw_count == 1


def test_procurement_fit_filter_excludes_non_technical_ocds(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "releases": [
                    _ocds_release(title="Janitorial cleaning services for offices", cpv="90910000")
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = UKFindTenderConnector().fetch({"limit": 5})

    assert result.status == "no_matches"


def test_worldbank_connector_normalizes_notice_without_pdf_source(monkeypatch):
    def fake_get(*_args, **_kwargs):
        return FakeResponse(
            {
                "procnotices": [
                    {
                        "id": "OP123",
                        "notice_type": "Request for Expression of Interest",
                        "noticedate": "18-May-2026",
                        "project_ctry_name": "Mexico",
                        "project_id": "P123",
                        "project_name": "Digital Government Modernization",
                        "bid_description": "Software platform for reporting dashboards and API integration",
                        "procurement_method_name": "Quality and Cost-Based Selection",
                        "submission_date": "2026-06-10T00:00:00Z",
                        "notice_text": "<p>Need cloud dashboard, automation and data integration. See https://example.org/file.pdf</p>",
                    }
                ]
            }
        )

    monkeypatch.setattr(connector_base.requests, "get", fake_get)
    result = WorldBankProcurementConnector().fetch({"query_terms": ["software"], "rows": 5})

    assert result.status == "ok"
    opportunity = result.opportunities[0]
    assert opportunity["source_type"] == "procurement"
    assert opportunity["buyer_name"] == "Digital Government Modernization"
    assert opportunity["buyer_domain"] == "worldbank.org"
    assert "pdf" not in opportunity["raw_text"].lower()
    assert opportunity["deadline_at"] == "2026-06-10T00:00:00Z"


def test_procurement_lite_policies_are_disabled_by_default(app):
    with app.app_context():
        db = get_db()
        rows = db.execute(
            """
            SELECT source_key, enabled
            FROM source_policies
            WHERE source_key IN ('ted_eu', 'uk_find_tender', 'uk_contracts_finder', 'worldbank_procurement')
            ORDER BY source_key
            """
        ).fetchall()

    assert len(rows) == 4
    assert all(row["enabled"] == 0 for row in rows)


def test_procurement_opportunity_enters_pipeline_and_buyer_account(app):
    opportunity = UKFindTenderConnector().normalize(
        {
            **_ocds_release(),
            "_source_label": "UK Find a Tender Lite",
        }
    )
    assert opportunity

    with app.app_context():
        db = get_db()
        stats = import_opportunities(db, source_key="uk_find_tender", items=[opportunity], trigger="test")
        row = db.execute(
            """
            SELECT o.source_type, o.score_tier, o.buyer_account_id, b.name
            FROM opportunities o
            JOIN buyer_accounts b ON b.id = o.buyer_account_id
            WHERE o.source_key = 'uk_find_tender'
            LIMIT 1
            """
        ).fetchone()

    assert stats["created"] == 1
    assert row["source_type"] == "procurement"
    assert row["score_tier"] in {"A1", "A2", "B", "C", "D"}
    assert row["buyer_account_id"]
    assert row["name"] == "Digital Services Agency"
