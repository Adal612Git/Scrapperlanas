from __future__ import annotations

from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.quality import assess_opportunity_quality
from scrapperlanas.services.quality_memory import (
    apply_feedback_overrides,
    get_feedback_for_opportunity,
    get_feedback_stats,
    record_feedback,
)


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "quality-memory.sqlite3"),
            "SECRET_KEY": "quality-memory-secret",
            "CRON_SECRET": "quality-memory-cron",
            "CSRF_ENABLED": False,
            "AUTOMATION_MODE": "manual",
            "AI_PROVIDER": "heuristic",
            "AI_EMBED_PROVIDER": "none",
        }
    )


def _insert_opportunity(db, *, url: str = "https://example.com/memory", source_key: str = "reddit", buyer: str = "forhire") -> int:
    cursor = db.execute(
        """
        INSERT INTO opportunities (
            source_key, source_label, source_type, title, company, buyer_name,
            buyer_domain, url, raw_text, risk_level, analysis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_key,
            "Reddit Public JSON" if source_key == "reddit" else "Direct",
            "community" if source_key == "reddit" else "direct_rfp",
            "[Hiring] automation specialist $50/hour",
            buyer,
            buyer,
            "reddit.com" if source_key == "reddit" else "buyer.example",
            url,
            "Need a developer for automation. $50/hour.",
            "low",
            "{}",
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def test_quality_memory_records_and_reads_feedback(app):
    with app.app_context():
        db = get_db()
        opportunity_id = _insert_opportunity(db)

        saved = record_feedback(
            db,
            opportunity_id=opportunity_id,
            decision="BAD_LEAD",
            actor_user_id=None,
            reason="No buyer identity.",
            corrected_stage="LOW_VALUE",
        )
        feedback = get_feedback_for_opportunity(db, opportunity_id)

    assert saved.decision == "BAD_LEAD"
    assert feedback[0].decision == "BAD_LEAD"
    assert feedback[0].reason == "No buyer identity."
    assert feedback[0].corrected_stage == "LOW_VALUE"
    assert feedback[0].created_at


def test_good_lead_feedback_can_override_watchlist():
    opportunity = {
        "source_key": "reddit",
        "source_label": "Reddit Public JSON",
        "source_type": "community",
        "title": "[Hiring] automation specialist $50/hour",
        "company": "forhire",
        "buyer_name": "forhire",
        "buyer_domain": "reddit.com",
        "url": "https://reddit.com/r/forhire/comments/override",
        "raw_text": "Need a developer for n8n automation. $50/hour.",
        "risk_level": "low",
    }
    quality = assess_opportunity_quality(opportunity)

    overridden = apply_feedback_overrides(
        opportunity,
        quality,
        {
            "decision": "GOOD_LEAD",
            "reason": "Ricardo validated buyer by DM.",
            "corrected_stage": "READY_TO_CONTACT",
            "corrected_grade": "B",
        },
    )

    assert quality.is_contactable is False
    assert overridden.quality_stage == "READY_TO_CONTACT"
    assert overridden.is_contactable is True
    assert "human_feedback_good_lead" in overridden.rejection_reasons


def test_negative_feedback_reduces_source_trust(app):
    with app.app_context():
        db = get_db()
        first = _insert_opportunity(db, url="https://reddit.com/r/forhire/comments/scam1")
        second = _insert_opportunity(db, url="https://reddit.com/r/forhire/comments/scam2")
        record_feedback(db, opportunity_id=first, decision="SCAM", reason="Telegram only.")
        record_feedback(db, opportunity_id=second, decision="SCAM", reason="Upfront fee.")
        stats = get_feedback_stats(db)

    assert stats["decision_counts"]["SCAM"] == 2
    assert stats["source_trust_penalties"]["reddit"] >= 70
