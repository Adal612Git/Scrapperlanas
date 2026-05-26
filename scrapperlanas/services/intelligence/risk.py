from __future__ import annotations

from typing import Any, Mapping

from .schemas import RiskAssessment
from .utils import budget_amount, clean_text, combined_text, coerce_list, normalize_domain, parse_datetime


SCAM_TERMS = (
    "crypto wallet",
    "telegram only",
    "upfront fee",
    "gift card",
    "wire transfer",
    "no contract",
    "urgent payment",
)

GENERIC_TERMS = (
    "easy task",
    "simple job",
    "quick money",
    "work from home no experience",
)


def assess_risk(opportunity: Mapping[str, Any], *, now=None) -> RiskAssessment:
    text = combined_text(opportunity)
    warnings: list[str] = []
    reasons: list[str] = []
    score = 0

    if any(term in text for term in SCAM_TERMS):
        score += 45
        warnings.append("Posibles senales de scam en el texto.")
        reasons.append("El texto contiene patrones de fraude o pago inseguro.")

    if any(term in text for term in GENERIC_TERMS):
        score += 18
        warnings.append("Texto demasiado generico.")
        reasons.append("La descripcion suena generica y poco verificable.")

    if not clean_text(opportunity.get("buyer_name") or opportunity.get("company")):
        score += 14
        warnings.append("Comprador sin identidad clara.")

    if not normalize_domain(opportunity.get("buyer_domain")) and not coerce_list(opportunity.get("contact_signals")):
        score += 12
        warnings.append("Contactabilidad baja.")

    deadline = parse_datetime(opportunity.get("deadline_at") or opportunity.get("deadline"))
    if deadline and now and deadline < now:
        score += 25
        warnings.append("Deadline vencido.")
    elif deadline is None and budget_amount(opportunity) == 0:
        score += 8
        warnings.append("Falta deadline y presupuesto visible.")

    risk_level = clean_text(opportunity.get("risk_level")).lower()
    if risk_level == "high":
        score += 35
        warnings.append("Fuente marcada como alto riesgo.")
    elif risk_level == "medium":
        score += 12
        warnings.append("Fuente marcada como riesgo medio.")

    duplicate_confidence = float(opportunity.get("duplicate_confidence") or 0)
    if duplicate_confidence >= 0.85:
        score += 22
        warnings.append("Posible duplicado fuerte.")

    if len(text) < 90:
        score += 10
        warnings.append("Poca evidencia textual.")

    return RiskAssessment(
        scam_risk=max(0, min(100, score)),
        warnings=list(dict.fromkeys(warnings)),
        reasons=list(dict.fromkeys(reasons)),
    )
