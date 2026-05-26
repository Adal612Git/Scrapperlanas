from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scrapperlanas.services.intelligence import (
    compare_accounts,
    compose_outreach,
    recommend_next_best_action,
    score_v2,
    transition_opportunity,
)
from scrapperlanas.services.intelligence.risk import assess_risk


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
