from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scrapperlanas.services.intelligence import (
    compare_accounts,
    compose_outreach,
    copilot_mode,
    evaluate_opportunity,
    outreach_blockers,
    recommend_next_best_action,
    research_checklist,
    score_v2,
    transition_opportunity,
)
from scrapperlanas.services.intelligence.risk import assess_risk
from scrapperlanas.services.quality import assess_opportunity_quality


def _opportunity(**overrides):
    base = {
        "title": "[Hiring] API integration and operations dashboard",
        "buyer_name": "Acme Ops",
        "buyer_domain": "acme.example",
        "source_label": "Workana Public Projects",
        "source_key": "workana_projects",
        "source_type": "direct_rfp",
        "country": "US",
        "estimated_value": 151_839,
        "currency": "USD",
        "deadline_at": (datetime.now(UTC) + timedelta(days=15)).isoformat(),
        "required_skills": ["API", "Python", "Docker", "SQL", "Automation"],
        "pain_signals": [
            "Necesita integracion API",
            "Necesita automatizacion operativa",
            "Necesita pipeline ETL",
            "Necesita dashboard o visibilidad operativa",
        ],
        "contact_signals": ["email:ops@acme.example"],
        "evidence_snippets": ["Budget USD 151839 visible in description."],
        "commercial_status": "new",
        "risk_level": "low",
        "url": "https://acme.example/projects/api-dashboard",
    }
    base.update(overrides)
    return base


def test_score_v2_is_explainable_for_strong_opportunity():
    result = score_v2(_opportunity())

    assert result.grade in {"A1", "A2"}
    assert result.numeric_score >= 80
    assert result.components["money_score"] >= 90
    assert result.components["fit_score"] >= 80
    assert result.reasons
    assert result.suggested_next_action


def test_state_machine_allows_valid_transition():
    result = transition_opportunity("new", "interesting", _opportunity(), actor="1", reason="Buen fit")

    assert result.allowed is True
    assert result.guard_result == "ok"
    assert result.previous_state == "new"
    assert result.next_state == "interesting"


def test_state_machine_blocks_invalid_transition_without_guard():
    result = transition_opportunity(
        "proposal_sent",
        "won",
        _opportunity(evidence_snippets=[]),
        actor="1",
    )

    assert result.allowed is False
    assert result.guard_result == "won_requires_evidence_or_manual_confirmation"


def test_state_machine_requires_proposal_and_channel_before_proposal_sent():
    missing_proposal = transition_opportunity(
        "replied",
        "proposal_sent",
        _opportunity(proposal_value=None),
        actor="1",
    )
    missing_channel = transition_opportunity(
        "replied",
        "proposal_sent",
        _opportunity(proposal_value=2500, url="", action_url="", apply_url=""),
        actor="1",
    )
    ready = transition_opportunity(
        "replied",
        "proposal_sent",
        _opportunity(proposal_value=2500),
        actor="1",
    )

    assert missing_proposal.allowed is False
    assert missing_channel.allowed is False
    assert ready.allowed is True


def test_state_machine_manual_override_requires_reason():
    blocked = transition_opportunity("suspicious", "interesting", _opportunity(), override=True)
    allowed = transition_opportunity(
        "suspicious",
        "interesting",
        _opportunity(),
        override=True,
        reason="Revisado manualmente por operador.",
    )

    assert blocked.allowed is False
    assert allowed.allowed is True
    assert allowed.guard_result == "manual_override"


def test_next_best_action_prioritizes_outreach_for_a1_with_contact():
    result = recommend_next_best_action({**_opportunity(), "grade": "A1", "score_v2": 94})

    assert result.action_type == "REDACT_OUTREACH"
    assert "Redactar" in result.label
    assert result.required_fields == []


def test_dedupe_confidence_recommends_merge_for_same_domain_and_contact():
    left = _opportunity(contact_signals=["email:ops@acme.example"])
    right = _opportunity(
        buyer_name="Acme Ops",
        buyer_domain="https://www.acme.example/path",
        contact_signals=["email:ops@acme.example"],
    )

    result = compare_accounts(left, right)

    assert result.duplicate_confidence >= 0.85
    assert result.recommended_action == "merge"
    assert result.reasons


def test_outreach_composer_uses_real_data_without_inventing_claims():
    draft = compose_outreach(_opportunity(), tone="consultivo")

    assert "Acme Ops" in draft.body
    assert "integracion API" in draft.body
    assert "Loto Signal" in draft.body
    assert "expertos mundiales" not in draft.body.lower()
    assert draft.personalization_bullets


def test_risk_engine_flags_scam_signals():
    risk = assess_risk(
        _opportunity(
            title="Easy task crypto wallet setup",
            raw_text="Send upfront fee by gift card and contact telegram only.",
            buyer_name="",
            buyer_domain="",
            contact_signals=[],
        )
    )

    assert risk.scam_risk >= 65
    assert risk.warnings


def test_copilot_evaluate_rejected_noise():
    opportunity = {
        "source_key": "reddit",
        "source_label": "Reddit Public JSON",
        "source_type": "community",
        "title": "The Warehouse I Work At Has a Basement That Doesn't Exist...",
        "company": "nosleep",
        "buyer_name": "nosleep",
        "buyer_domain": "reddit.com",
        "url": "https://reddit.com/r/nosleep/comments/1",
        "raw_text": "fiction story",
        "risk_level": "medium",
    }
    opportunity["quality"] = assess_opportunity_quality(opportunity).to_dict()

    result = evaluate_opportunity(opportunity)

    assert result.verdict == "REJECTED"
    assert result.blocking_conditions


def test_copilot_research_checklist_for_review_required():
    opportunity = _opportunity(
        quality={
            "quality_stage": "REVIEW_REQUIRED",
            "commercial_intent_label": "BUYER_PAIN",
            "risk_level": "LOW",
            "buyer_confidence": 45,
            "budget": {"is_valid_commercial_budget": False},
            "rejection_reasons": ["buyer_identity_weak"],
            "risk_reasons": [],
            "quality_score": 55,
            "budget_confidence": 0,
        }
    )

    result = research_checklist(opportunity)

    assert result.mode == "investigar"
    assert any("comprador" in item.lower() for item in result.checklist)


def test_copilot_redact_blocks_suspect():
    opportunity = _opportunity(
        state="SOSPECHOSO",
        quality={
            "quality_stage": "SUSPECT",
            "commercial_intent_label": "DIRECT_HIRING",
            "risk_level": "HIGH",
            "buyer_confidence": 80,
            "budget": {"is_valid_commercial_budget": True},
            "rejection_reasons": ["high_risk"],
            "risk_reasons": ["commission_only"],
            "quality_score": 44,
            "budget_confidence": 80,
        },
    )

    result = copilot_mode(opportunity, mode="redactar")
    draft = compose_outreach(opportunity)

    assert result.verdict == "BLOCKED"
    assert "No recomiendo contactar" in draft.body


def test_copilot_redact_blocks_low_buyer_confidence():
    opportunity = _opportunity(
        quality={
            "quality_stage": "READY_TO_CONTACT",
            "commercial_intent_label": "DIRECT_HIRING",
            "risk_level": "LOW",
            "buyer_confidence": 35,
            "budget": {"is_valid_commercial_budget": True},
            "rejection_reasons": [],
            "risk_reasons": [],
            "quality_score": 78,
            "budget_confidence": 80,
        }
    )

    assert any("Comprador" in blocker for blocker in outreach_blockers(opportunity))


def test_copilot_redact_allows_ready_to_contact():
    opportunity = _opportunity()
    opportunity["quality"] = {
        "quality_stage": "READY_TO_CONTACT",
        "commercial_intent_label": "DIRECT_HIRING",
        "risk_level": "LOW",
        "buyer_confidence": 85,
        "budget": {"is_valid_commercial_budget": True},
        "rejection_reasons": [],
        "risk_reasons": [],
        "quality_score": 88,
        "budget_confidence": 80,
    }
    result = copilot_mode(opportunity, mode="redactar")

    assert result.verdict == "READY"
    assert result.draft
    assert "Acme Ops" in result.draft["body"]
