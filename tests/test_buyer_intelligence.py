from __future__ import annotations

from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.buyer_intelligence import (
    assign_buyer_account,
    detect_possible_duplicates,
    list_duplicate_candidates,
    merge_buyer_accounts,
    normalize_buyer_name,
    normalize_domain,
    recalculate_buyer_account,
)
from scrapperlanas.services.pipeline import import_opportunities


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "buyer.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
        }
    )


def _insert_opportunity(
    db,
    *,
    title: str,
    buyer_name: str,
    buyer_domain: str = "",
    score_total: int = 70,
    score_tier: str = "A2",
    commercial_status: str = "new",
    estimated_value: int | None = None,
    proposal_value: int | None = None,
    won_value: int | None = None,
    ignored_reason: str = "",
    lost_reason: str = "",
    contact_signals: str = "[]",
    suffix: str = "",
) -> int:
    url_suffix = suffix or normalize_buyer_name(f"{buyer_name}-{title}") or title.replace(" ", "-").lower()
    cursor = db.execute(
        """
        INSERT INTO opportunities (
            source_key,
            source_label,
            source_type,
            title,
            company,
            buyer_name,
            buyer_domain,
            country,
            url,
            estimated_value,
            required_skills,
            pain_signals,
            contact_signals,
            evidence_snippets,
            score_total,
            score_tier,
            commercial_status,
            proposal_value,
            won_value,
            ignored_reason,
            lost_reason,
            raw_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "unit",
            "Unit",
            "direct_rfp",
            title,
            buyer_name,
            buyer_name,
            buyer_domain,
            "MX",
            f"https://example.com/{url_suffix}",
            estimated_value,
            '["Python", "dashboards"]',
            '["Necesita dashboards operativos"]',
            contact_signals,
            '["Public signal mentions dashboards."]',
            score_total,
            score_tier,
            commercial_status,
            proposal_value,
            won_value,
            ignored_reason,
            lost_reason,
            "Public signal mentions dashboards.",
        ),
    )
    return cursor.lastrowid


def test_normalize_domain_groups_variants():
    assert normalize_domain("https://www.Acme.com/path?q=1") == "acme.com"
    assert normalize_domain("buyer@Sub.Acme.com") == "sub.acme.com"
    assert normalize_domain("http://acme.com/") == "acme.com"


def test_normalize_buyer_name_removes_common_suffixes():
    assert normalize_buyer_name("Acme Technologies S.A. de C.V.") == "acme technologies"
    assert normalize_buyer_name("Acme Technologies LLC") == "acme technologies"


def test_domain_normalized_groups_opportunities(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(
            db,
            title="API dashboard",
            buyer_name="Acme Technologies",
            buyer_domain="https://www.acme.com/platform",
            suffix="domain-1",
        )
        second_id = _insert_opportunity(
            db,
            title="Automation support",
            buyer_name="Acme Tech",
            buyer_domain="acme.com",
            suffix="domain-2",
        )

        first_account = assign_buyer_account(db, first_id)
        second_account = assign_buyer_account(db, second_id)
        db.commit()

        assert first_account["id"] == second_account["id"]
        account = recalculate_buyer_account(db, first_account["id"])
        assert account["opportunity_count"] == 2


def test_similar_names_without_domain_do_not_auto_merge(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(db, title="API work", buyer_name="Acme Technologies", suffix="name-1")
        second_id = _insert_opportunity(db, title="API work 2", buyer_name="Acme Tech Labs", suffix="name-2")

        first_account = assign_buyer_account(db, first_id)
        second_account = assign_buyer_account(db, second_id)
        db.commit()

        assert first_account["id"] != second_account["id"]


def test_buyer_account_created_when_importing_opportunity(app):
    with app.app_context():
        db = get_db()
        stats = import_opportunities(
            db,
            source_key="email_alerts",
            source_label="Alertas por Correo",
            items=[
                {
                    "external_id": "buyer-import-1",
                    "title": "Python dashboard contract",
                    "company": "Imported Buyer",
                    "buyer_name": "Imported Buyer",
                    "buyer_domain": "imported.example",
                    "url": "https://imported.example/opportunities/python-dashboard",
                    "raw_text": "Need Python dashboards and API automation. Budget USD 12000.",
                    "estimated_value": 12000,
                    "required_skills": ["Python", "dashboards"],
                    "pain_signals": ["Necesita dashboard operativo"],
                }
            ],
        )

        row = db.execute(
            """
            SELECT buyer_account_id
            FROM opportunities
            WHERE url = ?
            """,
            ("https://imported.example/opportunities/python-dashboard",),
        ).fetchone()

        assert stats["created"] == 1
        assert row["buyer_account_id"] is not None


def test_recalculate_account_updates_counts_and_estimated_value(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(
            db,
            title="A1 dashboard",
            buyer_name="Metric Buyer",
            buyer_domain="metrics.example",
            score_total=82,
            score_tier="A1",
            estimated_value=10000,
            suffix="metric-1",
        )
        second_id = _insert_opportunity(
            db,
            title="A2 automation",
            buyer_name="Metric Buyer",
            buyer_domain="metrics.example",
            score_total=65,
            score_tier="A2",
            estimated_value=5000,
            suffix="metric-2",
        )

        account = assign_buyer_account(db, first_id)
        assign_buyer_account(db, second_id)
        account = recalculate_buyer_account(db, account["id"])

        assert account["opportunity_count"] == 2
        assert account["a1_count"] == 1
        assert account["a2_count"] == 1
        assert account["total_estimated_value"] == 15000


def test_multiple_active_a1_makes_account_hot(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(
            db,
            title="A1 one",
            buyer_name="Hot Buyer",
            buyer_domain="hot.example",
            score_total=82,
            score_tier="A1",
            suffix="hot-1",
        )
        second_id = _insert_opportunity(
            db,
            title="A1 two",
            buyer_name="Hot Buyer",
            buyer_domain="hot.example",
            score_total=80,
            score_tier="A1",
            suffix="hot-2",
        )

        account = assign_buyer_account(db, first_id)
        assign_buyer_account(db, second_id)
        account = recalculate_buyer_account(db, account["id"])

        assert account["account_tier"] == "hot"


def test_ignored_opportunities_do_not_remain_hot(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(
            db,
            title="Ignored one",
            buyer_name="Ignored Buyer",
            buyer_domain="ignored.example",
            score_total=90,
            score_tier="A1",
            commercial_status="ignored",
            ignored_reason="bad_fit",
            suffix="ignored-1",
        )
        second_id = _insert_opportunity(
            db,
            title="Ignored two",
            buyer_name="Ignored Buyer",
            buyer_domain="ignored.example",
            score_total=88,
            score_tier="A1",
            commercial_status="ignored",
            ignored_reason="bad_fit",
            suffix="ignored-2",
        )

        account = assign_buyer_account(db, first_id)
        assign_buyer_account(db, second_id)
        account = recalculate_buyer_account(db, account["id"])

        assert account["account_tier"] == "noisy"
        assert account["account_tier"] != "hot"


def test_manual_merge_reassigns_opportunities(app):
    with app.app_context():
        db = get_db()
        target_opportunity_id = _insert_opportunity(db, title="Target", buyer_name="Merge Buyer", suffix="merge-target")
        source_opportunity_id = _insert_opportunity(db, title="Source", buyer_name="Merge Buyers", suffix="merge-source")
        target = assign_buyer_account(db, target_opportunity_id)
        source = assign_buyer_account(db, source_opportunity_id)
        assert target["id"] != source["id"]

        merged = merge_buyer_accounts(
            db,
            target_account_id=target["id"],
            source_account_id=source["id"],
            actor_user_id=None,
        )
        source_row = db.execute("SELECT id FROM buyer_accounts WHERE id = ?", (source["id"],)).fetchone()
        reassigned = db.execute(
            "SELECT buyer_account_id FROM opportunities WHERE id = ?",
            (source_opportunity_id,),
        ).fetchone()

        assert merged["opportunity_count"] == 2
        assert source_row is None
        assert reassigned["buyer_account_id"] == target["id"]


def test_duplicate_detection_handles_missing_domains(app):
    with app.app_context():
        db = get_db()
        first_id = _insert_opportunity(db, title="Signals A", buyer_name="Northwind Analytics", suffix="dup-1")
        second_id = _insert_opportunity(db, title="Signals B", buyer_name="Northwind Analytic", suffix="dup-2")
        first = assign_buyer_account(db, first_id)
        second = assign_buyer_account(db, second_id)
        assert first["id"] != second["id"]

        candidates = detect_possible_duplicates(db)
        open_candidates = list_duplicate_candidates(db)

        assert candidates
        assert open_candidates
        assert open_candidates[0]["confidence"] >= 86
