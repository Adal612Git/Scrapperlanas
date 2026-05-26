from __future__ import annotations

from datetime import UTC, datetime

from scrapperlanas.services.ingestion import NormalizedOpportunity
from scrapperlanas.services.scoring import score_commercial_opportunity


NOW = datetime(2026, 5, 19, tzinfo=UTC)


def test_procurement_with_budget_and_workable_deadline_is_a1():
    opportunity = NormalizedOpportunity(
        external_id="sam-001",
        source_key="sam_gov",
        source_label="SAM.gov Contract Opportunities",
        source_type="procurement",
        title="Software development and API integration support",
        company="GENERAL SERVICES ADMINISTRATION",
        buyer_name="GENERAL SERVICES ADMINISTRATION",
        buyer_domain="sam.gov",
        country="US",
        url="https://sam.gov/opp/sam-001/view",
        apply_url="https://sam.gov/opp/sam-001/view",
        raw_text=(
            "Solicitation for software development, API integration and dashboard support. "
            "Response deadline: 2026-05-28. Award amount USD 12000. Contact jane@example.gov."
        ),
        estimated_value=12000,
        required_skills=["software", "api", "dashboard"],
        pain_signals=["Necesita integracion API", "Necesita dashboard o visibilidad operativa"],
        contact_signals=["email:jane@example.gov"],
        evidence_snippets=["Award amount USD 12000.", "Response deadline: 2026-05-28."],
        deadline_at="2026-05-28T00:00:00+00:00",
        posted_at="2026-05-15T00:00:00+00:00",
    )

    score = score_commercial_opportunity(opportunity, base_score=70, now=NOW)

    assert score["score_tier"] == "A1"
    assert score["score_money"] >= 75
    assert score["score_urgency"] >= 75
    assert score["score_contactability"] >= 75


def test_old_github_issue_without_contact_is_low_priority():
    opportunity = NormalizedOpportunity(
        external_id="gh-001",
        source_key="github_issues",
        source_label="GitHub Lead Signals",
        source_type="github_issue",
        title="Help wanted cleanup",
        company="hobby/project",
        buyer_name="hobby/project",
        buyer_domain="github.com",
        url="https://github.com/hobby/project/issues/1",
        raw_text="Old help wanted cleanup issue. No budget, no paid signal, no contact.",
        posted_at="2025-01-01T00:00:00+00:00",
        required_skills=["help wanted"],
    )

    score = score_commercial_opportunity(opportunity, base_score=10, now=NOW)

    assert score["score_tier"] in {"C", "D"}
    assert score["score_total"] < 45


def test_recent_technical_job_posting_becomes_a2_signal():
    opportunity = NormalizedOpportunity(
        external_id="ats-001",
        source_key="greenhouse",
        source_label="Greenhouse Public Boards",
        source_type="hiring_signal",
        title="Backend Automation Engineer",
        company="Acme Logistics",
        buyer_name="Acme Logistics",
        buyer_domain="acme.example",
        url="https://boards.greenhouse.io/acme/jobs/1",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        raw_text=(
            "Remote Backend Automation Engineer role. Python, APIs, data pipelines, dashboards, "
            "internal tools and workflow automation."
        ),
        posted_at="2026-05-17T00:00:00+00:00",
        required_skills=["Python", "API", "Automation"],
        pain_signals=["Necesita automatizacion operativa", "Necesita pipeline de datos"],
        evidence_snippets=["Python, APIs, data pipelines, dashboards."],
    )

    score = score_commercial_opportunity(opportunity, base_score=62, now=NOW)

    assert score["score_tier"] in {"A2", "A1"}
    assert score["score_fit"] >= 70


def test_expired_opportunity_is_discarded_even_with_good_fit():
    opportunity = NormalizedOpportunity(
        external_id="sam-old",
        source_key="sam_gov",
        source_label="SAM.gov Contract Opportunities",
        source_type="procurement",
        title="Python dashboard development",
        company="Agency",
        buyer_name="Agency",
        buyer_domain="sam.gov",
        url="https://sam.gov/opp/sam-old/view",
        raw_text="Budget USD 8000. Python dashboard development. Response deadline: 2026-05-01.",
        estimated_value=8000,
        required_skills=["Python", "Dashboard"],
        contact_signals=["email:buyer@example.gov"],
        evidence_snippets=["Budget USD 8000.", "Response deadline: 2026-05-01."],
        deadline_at="2026-05-01T00:00:00+00:00",
        posted_at="2026-04-20T00:00:00+00:00",
    )

    score = score_commercial_opportunity(opportunity, base_score=80, now=NOW)

    assert score["score_tier"] == "D"
    assert score["score_total"] <= 20
