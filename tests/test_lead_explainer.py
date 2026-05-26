from __future__ import annotations

from scrapperlanas.services.lead_explainer import explain_opportunity
from scrapperlanas.services.quality import assess_opportunity_quality


def _with_quality(data: dict) -> dict:
    return {**data, "quality": assess_opportunity_quality(data).to_dict()}


def test_lead_explainer_explains_rejected_noise():
    opportunity = _with_quality(
        {
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
    )

    explanation = explain_opportunity(opportunity)

    assert explanation.verdict == "REJECTED"
    assert any("Subreddit bloqueado" in reason for reason in explanation.negative_reasons)
    assert "No contactar" in explanation.next_best_action


def test_lead_explainer_explains_good_lead_without_inventing_claims():
    opportunity = _with_quality(
        {
            "source_key": "email_alerts",
            "source_label": "Direct",
            "source_type": "direct_rfp",
            "title": "Law firm needs document intake automation",
            "company": "Legal Rivera",
            "buyer_name": "Legal Rivera",
            "buyer_domain": "legalrivera.example",
            "url": "https://legalrivera.example/intake",
            "raw_text": "Law firm needs document intake automation. Budget USD 2500 project.",
            "risk_level": "low",
            "pain_signals": ["Document intake automation"],
            "evidence_snippets": ["Budget USD 2500 project"],
            "contact_signals": ["email:ops@legalrivera.example"],
        }
    )

    explanation = explain_opportunity(opportunity)
    joined = " ".join(explanation.positive_reasons + explanation.negative_reasons)

    assert explanation.verdict == "CONTACT_NOW"
    assert "Comprador identificable" in joined
    assert "certificado" not in joined.lower()
    assert "mundial" not in joined.lower()


def test_lead_explainer_shows_missing_evidence_for_ambiguous_lead():
    opportunity = _with_quality(
        {
            "source_key": "reddit",
            "source_label": "Reddit Public JSON",
            "source_type": "community",
            "title": "Founder asks how to automate reports",
            "company": "startups",
            "buyer_name": "startups",
            "buyer_domain": "reddit.com",
            "url": "https://reddit.com/r/startups/comments/reports",
            "raw_text": "Founder asks how to automate reports, no budget.",
            "risk_level": "low",
        }
    )

    explanation = explain_opportunity(opportunity)

    assert explanation.verdict in {"WATCH", "RESEARCH_FIRST"}
    assert explanation.missing_evidence
    assert "Validar" in explanation.next_best_action or "watchlist" in explanation.next_best_action.lower()
