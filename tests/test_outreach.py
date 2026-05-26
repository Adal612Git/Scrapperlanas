from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.outreach import (
    generate_outreach_drafts,
    persist_outreach_drafts,
    validate_outreach_draft,
)


def _base_opportunity(**overrides):
    data = {
        "id": 1,
        "source_type": "direct_rfp",
        "title": "Python dashboard and API automation",
        "buyer_name": "Acme Ops",
        "company": "Acme Ops",
        "required_skills": ["Python", "API integrations", "dashboards"],
        "pain_signals": ["Necesita visibilidad operativa y automatizacion de reportes"],
        "evidence_snippets": ["The team needs API automation and operational dashboards."],
        "contact_signals": [],
        "deadline_at": None,
        "estimated_value": None,
        "currency": "USD",
        "url": "https://example.com/opportunity",
    }
    data.update(overrides)
    return data


def _draft_by_type(drafts, draft_type: str):
    return next(draft for draft in drafts if draft.draft_type == draft_type)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w@.-]+\b", text or ""))


def test_procurement_with_deadline_generates_formal_message():
    drafts = generate_outreach_drafts(
        _base_opportunity(
            source_type="procurement",
            title="Software development services for reporting platform",
            deadline_at="2026-06-01T12:00:00+00:00",
        )
    )

    initial = _draft_by_type(drafts, "initial")

    assert initial.channel == "portal"
    assert initial.body.startswith("Buen dia,")
    assert "oportunidad publicada" in initial.body
    assert "fecha limite" in initial.body
    assert _draft_by_type(drafts, "procurement_response")


def test_hiring_signal_does_not_use_invasive_language():
    drafts = generate_outreach_drafts(
        _base_opportunity(
            source_type="hiring_signal",
            title="Backend Automation Engineer",
            pain_signals=["Fortalecen capacidades de automatizacion backend"],
        )
    )
    initial = _draft_by_type(drafts, "initial")
    text = initial.body.lower()

    assert "fortaleciendo capacidades" in text
    assert "desesper" not in text
    assert "atorad" not in text
    assert "te scrape" not in text


def test_github_issue_generates_technical_respectful_reply():
    drafts = generate_outreach_drafts(
        _base_opportunity(
            source_type="github_issue",
            title="Fix flaky API integration tests",
            pain_signals=["Issue con pruebas inestables en integracion API"],
        )
    )
    github_reply = _draft_by_type(drafts, "github_reply")

    assert github_reply.channel == "github_comment"
    assert "issue" in github_reply.body.lower()
    assert "solucion" in github_reply.body.lower()
    assert "abrir PR" in github_reply.body


def test_opportunity_without_buyer_name_uses_neutral_greeting():
    drafts = generate_outreach_drafts(_base_opportunity(buyer_name="", company=""))
    initial = _draft_by_type(drafts, "initial")

    assert initial.body.startswith("Hola,\n")


def test_evidence_snippets_are_carried_into_evidence_used():
    drafts = generate_outreach_drafts(
        _base_opportunity(evidence_snippets=["Needs API automation before quarterly reporting."])
    )
    initial = _draft_by_type(drafts, "initial")

    assert "Needs API automation before quarterly reporting." in initial.evidence_used


def test_initial_message_stays_under_120_words():
    drafts = generate_outreach_drafts(_base_opportunity())
    initial = _draft_by_type(drafts, "initial")

    assert _word_count(initial.body) <= 120


@pytest.mark.parametrize("draft_type", ["followup_1", "followup_2", "polite_close"])
def test_followups_stay_under_90_words(draft_type):
    drafts = generate_outreach_drafts(_base_opportunity())
    draft = _draft_by_type(drafts, draft_type)

    assert _word_count(draft.body) <= 90


def test_drafts_do_not_invent_budget_when_estimated_value_is_null():
    drafts = generate_outreach_drafts(_base_opportunity(estimated_value=None))
    joined = "\n".join(draft.body.lower() for draft in drafts)

    assert "valor estimado" not in joined
    assert "budget" not in joined
    assert "$" not in joined
    assert all("mentions_budget_without_source_value" not in (draft.quality_warnings or []) for draft in drafts)


def test_generation_handles_empty_signal_lists():
    drafts = generate_outreach_drafts(
        _base_opportunity(required_skills=[], pain_signals=[], evidence_snippets=[])
    )

    assert _draft_by_type(drafts, "initial").body
    assert _draft_by_type(drafts, "mini_proposal").body


def test_validate_outreach_draft_flags_missing_evidence_and_aggressive_tone():
    opportunity = _base_opportunity()
    draft = {
        "draft_type": "initial",
        "body": "Hola, garantizo resultados. Deben contratar ya.",
        "evidence_used": [],
    }

    warnings = validate_outreach_draft(draft, opportunity)

    assert "no_traceable_evidence" in warnings
    assert "aggressive_or_overclaiming_tone" in warnings
    assert "missing_clear_cta" in warnings


def test_regenerating_drafts_deactivates_previous_rows(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "outreach.sqlite3"),
            "SECRET_KEY": "test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
        }
    )

    with app.app_context():
        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO opportunities (
                source_key,
                source_label,
                source_type,
                title,
                buyer_name,
                url,
                required_skills,
                pain_signals,
                evidence_snippets,
                raw_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "unit",
                "Unit",
                "procurement",
                "Software automation procurement",
                "City Office",
                "https://example.com/outreach-regenerate",
                '["Python", "dashboards"]',
                '["Necesita dashboards operativos"]',
                '["Public notice mentions dashboards."]',
                "Public notice mentions dashboards.",
            ),
        )
        opportunity_id = cursor.lastrowid
        first = persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=False)
        second = persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=True)

        active_total = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND is_active = 1",
            (opportunity_id,),
        ).fetchone()["total"]
        inactive_total = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND is_active = 0",
            (opportunity_id,),
        ).fetchone()["total"]
        regenerated_total = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND regenerated_at IS NOT NULL",
            (opportunity_id,),
        ).fetchone()["total"]

        assert len(second) == len(first)
        assert active_total == len(second)
        assert inactive_total == len(first)
        assert regenerated_total == len(second)


def test_persist_outreach_blocks_rejected_noise(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "outreach-block.sqlite3"),
            "SECRET_KEY": "test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
        }
    )

    with app.app_context():
        db = get_db()
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
                url,
                raw_text,
                risk_level
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "reddit",
                "Reddit Public JSON",
                "community",
                "The Warehouse I Work At Has a Basement That Doesn't Exist...",
                "nosleep",
                "nosleep",
                "reddit.com",
                "https://reddit.com/r/nosleep/comments/outreach-block",
                "fiction story",
                "medium",
            ),
        )
        opportunity_id = cursor.lastrowid
        drafts = persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=False)

    assert len(drafts) == 1
    assert drafts[0]["draft_type"] == "discovery_questions"
    assert "No recomiendo redactar" in drafts[0]["body"]
    assert "falta validacion" in drafts[0]["subject"].lower()
