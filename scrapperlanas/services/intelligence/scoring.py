from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from .evidence import collect_evidence
from .next_best_action import recommend_next_best_action
from .risk import assess_risk
from .schemas import ScoreV2Result
from .utils import budget_amount, clean_text, combined_text, coerce_list, normalize_domain, parse_datetime, technical_matches
from ..quality import assess_opportunity_quality


BUYING_TERMS = ("budget", "paid", "bounty", "contract", "proposal", "quote", "procurement", "solicitation", "usd", "$")
URGENCY_TERMS = ("urgent", "asap", "deadline", "due", "immediate", "critical")
NEGATIVE_TERMS = ("volunteer", "unpaid", "intern", "student", "ios", "android", "wordpress")


def score_v2(opportunity: Mapping[str, Any], *, now: datetime | None = None) -> ScoreV2Result:
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)
    text = combined_text(opportunity)
    risk = assess_risk(opportunity, now=runtime_now)
    quality_payload = opportunity.get("quality") if isinstance(opportunity.get("quality"), dict) else None
    quality = assess_opportunity_quality(opportunity) if quality_payload is None else _quality_object(quality_payload)
    evidence = collect_evidence(opportunity)

    money_score, money_reasons = _money_score(opportunity, text)
    fit_score, fit_reasons = _fit_score(opportunity, text)
    urgency_score, urgency_reasons = _urgency_score(opportunity, text, runtime_now)
    contactability_score, contact_reasons = _contactability_score(opportunity)
    confidence_score, confidence_reasons = _confidence_score(opportunity, evidence)
    risk_penalty = min(45, round(risk.scam_risk * 0.45))

    target_match = int(opportunity.get("target_match") or 0)
    duplicate_confidence = float(opportunity.get("duplicate_confidence") or 0)
    if duplicate_confidence >= 0.85:
        risk_penalty = max(risk_penalty, 30)

    total = round(
        (0.30 * money_score)
        + (0.24 * fit_score)
        + (0.18 * urgency_score)
        + (0.15 * contactability_score)
        + (0.13 * confidence_score)
        + min(6, target_match // 15)
        - risk_penalty
    )
    total = max(0, min(100, total))
    total = min(total, quality.final_score_cap)
    grade = _grade(total, risk_penalty=risk_penalty, opportunity=opportunity, quality_grade=quality.grade)
    nba = recommend_next_best_action({**dict(opportunity), "score_v2": total, "grade": grade, "scam_risk": risk.scam_risk})
    reasons = [*money_reasons, *fit_reasons, *urgency_reasons, *contact_reasons, *confidence_reasons]
    reasons.extend([f"Calidad: {quality.quality_stage}.", f"Intencion: {quality.commercial_intent_label}."])

    return ScoreV2Result(
        grade=grade,
        numeric_score=total,
        components={
            "money_score": money_score,
            "fit_score": fit_score,
            "urgency_score": urgency_score,
            "contactability_score": contactability_score,
            "confidence_score": confidence_score,
            "risk_penalty": risk_penalty,
        },
        reasons=list(dict.fromkeys(reasons))[:6],
        warnings=risk.warnings[:6],
        suggested_next_action=nba.label,
    )


def _money_score(opportunity: Mapping[str, Any], text: str) -> tuple[int, list[str]]:
    amount = budget_amount(opportunity)
    source_type = clean_text(opportunity.get("source_type")).lower()
    reasons = []
    score = 18

    if amount >= 25_000:
        score = 100
    elif amount >= 10_000:
        score = 94
    elif amount >= 4_000:
        score = 84
    elif amount >= 1_000:
        score = 70
    elif amount > 0:
        score = 58

    if amount:
        reasons.append("Presupuesto visible o valor estimado.")
    elif source_type in {"procurement", "direct_rfp"}:
        score = 72
        reasons.append("Proceso formal de compra aunque el monto no sea claro.")
    elif source_type == "hiring_signal":
        score = 58
        reasons.append("Hiring signal tecnico: presupuesto probable, no confirmado.")
    elif any(term in text for term in BUYING_TERMS):
        score = 55
        reasons.append("Hay senales de compra sin monto visible.")
    else:
        reasons.append("No hay presupuesto visible.")

    return score, reasons


def _fit_score(opportunity: Mapping[str, Any], text: str) -> tuple[int, list[str]]:
    matches = technical_matches(opportunity)
    score = min(100, 28 + len(matches) * 10)
    for term in NEGATIVE_TERMS:
        if term in text:
            score -= 16
    pains = coerce_list(opportunity.get("pain_signals"))
    if pains:
        score += min(18, len(pains) * 6)
    reasons = [f"Fit tecnico: {', '.join(matches[:5])}."] if matches else ["Fit tecnico todavia debil."]
    return max(0, min(100, score)), reasons


def _urgency_score(opportunity: Mapping[str, Any], text: str, now: datetime) -> tuple[int, list[str]]:
    deadline = parse_datetime(opportunity.get("deadline_at") or opportunity.get("deadline"))
    posted = parse_datetime(opportunity.get("posted_at") or opportunity.get("created_at"))
    score = 40
    reasons = []
    if deadline:
        days = (deadline - now).days
        if days < 0:
            return 0, ["Deadline vencido."]
        if days <= 3:
            score = 72
            reasons.append("Deadline muy cercano.")
        elif days <= 14:
            score = 95
            reasons.append("Deadline cercano y accionable.")
        elif days <= 30:
            score = 82
            reasons.append("Deadline dentro de ventana comercial sana.")
        else:
            score = 58
            reasons.append("Hay deadline, pero no presiona todavia.")
    elif posted:
        age = (now - posted).days
        if age <= 3:
            score = 68
            reasons.append("Oportunidad reciente.")
        elif age <= 14:
            score = 54
            reasons.append("Oportunidad aun fresca.")
        else:
            score = 32
            reasons.append("Oportunidad vieja o sin deadline.")
    else:
        reasons.append("Sin fecha clara.")

    if any(term in text for term in URGENCY_TERMS):
        score = min(100, score + 10)
        reasons.append("El texto trae senales de urgencia.")
    return score, reasons


def _contactability_score(opportunity: Mapping[str, Any]) -> tuple[int, list[str]]:
    contacts = coerce_list(opportunity.get("contact_signals"))
    domain = normalize_domain(opportunity.get("buyer_domain"))
    has_url = bool(clean_text(opportunity.get("apply_url") or opportunity.get("url")))
    score = 15
    reasons = []
    if any("@" in signal or signal.lower().startswith("email:") for signal in contacts):
        score += 45
        reasons.append("Hay email o contacto directo.")
    if domain:
        score += 22
        reasons.append("Dominio del comprador disponible.")
    if has_url:
        score += 22
        reasons.append("Hay URL accionable.")
    if not reasons:
        reasons.append("No hay contacto claro todavia.")
    return min(100, score), reasons


def _confidence_score(opportunity: Mapping[str, Any], evidence: list[str]) -> tuple[int, list[str]]:
    score = 30 + min(45, len(evidence) * 8)
    if clean_text(opportunity.get("buyer_name") or opportunity.get("company")):
        score += 10
    if coerce_list(opportunity.get("pain_signals")):
        score += 10
    reasons = ["Hay evidencia trazable para explicar el score."] if evidence else ["Falta evidencia trazable."]
    return min(100, score), reasons


def _grade(total: int, *, risk_penalty: int, opportunity: Mapping[str, Any], quality_grade: str = "") -> str:
    if quality_grade == "REJECTED":
        return "D"
    if risk_penalty >= 35 or clean_text(opportunity.get("state")).upper() in {"SOSPECHOSO", "DESCARTADO"}:
        return "D"
    if total >= 90:
        return "A1"
    if total >= 80:
        return "A2"
    if total >= 65:
        return "B"
    if total >= 45:
        return "C"
    return "D"


def _quality_object(payload: Mapping[str, Any]):
    class _QualityProxy:
        def __init__(self, raw: Mapping[str, Any]):
            self.final_score_cap = int(raw.get("final_score_cap") or 100)
            self.grade = str(raw.get("grade") or "C")
            self.quality_stage = str(raw.get("quality_stage") or "REVIEW_REQUIRED")
            self.commercial_intent_label = str(raw.get("commercial_intent_label") or "UNKNOWN")

    return _QualityProxy(payload)
