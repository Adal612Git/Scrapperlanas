from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

from .quality import assess_opportunity_quality


LeadVerdict = Literal["CONTACT_NOW", "RESEARCH_FIRST", "WATCH", "DO_NOT_CONTACT", "REJECTED"]


@dataclass(frozen=True)
class LeadExplanation:
    headline: str
    verdict: LeadVerdict
    positive_reasons: list[str] = field(default_factory=list)
    negative_reasons: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    next_best_action: str = ""
    confidence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def explain_opportunity(opportunity: Mapping[str, Any]) -> LeadExplanation:
    quality = _quality_payload(opportunity)
    stage = str(quality.get("quality_stage") or "REVIEW_REQUIRED").upper()
    intent = str(quality.get("commercial_intent_label") or "UNKNOWN").upper()
    risk_level = str(quality.get("risk_level") or "MEDIUM").upper()
    buyer_confidence = _int(quality.get("buyer_confidence"))
    budget = quality.get("budget") if isinstance(quality.get("budget"), dict) else {}
    budget_valid = bool(budget.get("is_valid_commercial_budget"))
    evidence = _coerce_list(opportunity.get("evidence_snippets") or opportunity.get("evidence"))
    pains = _coerce_list(opportunity.get("pain_signals"))
    contacts = _coerce_list(opportunity.get("contact_signals"))

    positive: list[str] = []
    negative: list[str] = []
    missing: list[str] = []

    if buyer_confidence >= 70:
        positive.append("Comprador identificable.")
    elif buyer_confidence >= 50:
        positive.append("Comprador parcialmente verificable.")
    else:
        negative.append("No hay comprador verificable.")
        missing.append("comprador, empresa o dominio oficial")

    if intent in {"DIRECT_HIRING", "PROCUREMENT", "RFP", "SERVICE_REQUEST"}:
        positive.append("Intencion comercial clara.")
    elif intent == "BUYER_PAIN":
        positive.append("Hay dolor operativo posible.")
        missing.append("confirmacion de compra o canal oficial")
    elif intent in {"SEO_CONTENT", "DISCUSSION", "FICTION", "CONSPIRACY", "NEWS", "SELF_PROMO", "JOB_AGGREGATOR"}:
        negative.append(f"Contenido clasificado como {intent}.")
    else:
        negative.append("No hay intencion comercial clara.")

    if budget_valid:
        positive.append("Presupuesto valido o contexto de pago verificable.")
    elif budget.get("raw_evidence"):
        negative.append("El presupuesto detectado parece dudoso.")
        missing.append("presupuesto comercial valido")
    else:
        missing.append("presupuesto o rango de pago")

    if risk_level == "LOW":
        positive.append("Riesgo bajo.")
    elif risk_level in {"HIGH", "CRITICAL"}:
        negative.append("Riesgo alto antes de contactar.")
    elif risk_level == "MEDIUM":
        negative.append("Riesgo medio: conviene validar primero.")

    if pains:
        positive.append(f"Dolor detectado: {pains[0]}.")
    else:
        missing.append("dolor concreto")

    if contacts or opportunity.get("apply_url") or opportunity.get("url"):
        positive.append("Existe un canal o URL accionable.")
    else:
        missing.append("canal de contacto")

    for reason in quality.get("rejection_reasons") or []:
        negative.append(_reason_label(str(reason)))
    for reason in quality.get("risk_reasons") or []:
        if reason not in {"no_buyer_identity"}:
            negative.append(_reason_label(str(reason)))

    verdict = _verdict_for_stage(stage)
    headline = _headline(verdict, stage, intent)
    next_action = _next_action(verdict, stage, missing)
    confidence = _confidence(quality, evidence=evidence)

    return LeadExplanation(
        headline=headline,
        verdict=verdict,
        positive_reasons=_unique(positive)[:6],
        negative_reasons=_unique(negative)[:6],
        missing_evidence=_unique(missing)[:5],
        next_best_action=next_action,
        confidence=confidence,
    )


def _quality_payload(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    raw_quality = opportunity.get("quality")
    if isinstance(raw_quality, dict):
        return raw_quality
    analysis = opportunity.get("analysis")
    if isinstance(analysis, dict) and isinstance(analysis.get("quality"), dict):
        return analysis["quality"]
    return assess_opportunity_quality(opportunity).to_dict()


def _verdict_for_stage(stage: str) -> LeadVerdict:
    if stage == "READY_TO_CONTACT":
        return "CONTACT_NOW"
    if stage == "REVIEW_REQUIRED":
        return "RESEARCH_FIRST"
    if stage == "WATCHLIST":
        return "WATCH"
    if stage in {"REJECTED_NOISE", "DUPLICATE"}:
        return "REJECTED"
    return "DO_NOT_CONTACT"


def _headline(verdict: LeadVerdict, stage: str, intent: str) -> str:
    if verdict == "CONTACT_NOW":
        return "Contactar ahora: hay evidencia comercial suficiente."
    if verdict == "RESEARCH_FIRST":
        return "Investigar primero: el lead puede servir, pero falta validacion."
    if verdict == "WATCH":
        return "Vigilar: hay senales, todavia no es accionable."
    if verdict == "REJECTED":
        return f"Rechazado: {intent.lower()} fuera del pipeline comercial."
    return f"No contactar todavia: calidad {stage.lower()}."


def _next_action(verdict: LeadVerdict, stage: str, missing: list[str]) -> str:
    if verdict == "CONTACT_NOW":
        return "Redactar outreach consultivo con evidencia y CTA de 15-20 minutos."
    if verdict == "RESEARCH_FIRST":
        missing_text = ", ".join(missing[:3]) if missing else "evidencia comercial clave"
        return f"Validar {missing_text} antes de redactar."
    if verdict == "WATCH":
        return "Mantener en watchlist y esperar comprador, presupuesto o CTA mas claro."
    if verdict == "REJECTED":
        return "No contactar. Mantener fuera de oportunidades activas."
    return "No contactar. Revisar riesgo, comprador y fuente antes de avanzar."


def _confidence(quality: dict[str, Any], *, evidence: list[str]) -> int:
    base = _int(quality.get("quality_score"))
    buyer = _int(quality.get("buyer_confidence"))
    budget = _int(quality.get("budget_confidence"))
    risk = _int(quality.get("risk_score"))
    confidence = round((base * 0.48) + (buyer * 0.22) + (budget * 0.15) + (min(100, len(evidence) * 20) * 0.15) - (risk * 0.25))
    return max(0, min(100, confidence))


def _reason_label(reason: str) -> str:
    labels = {
        "blocked_subreddit": "Subreddit bloqueado.",
        "buyer_identity_weak": "Identidad de comprador debil.",
        "budget_no_currency_budget_signal": "No se detecto presupuesto comercial.",
        "budget_numeric_context_not_budget": "Numero detectado no parece presupuesto.",
        "budget_percentage_or_commission": "Porcentaje/comision no es presupuesto valido.",
        "seo_content": "Contenido SEO, no solicitud comercial.",
        "high_risk": "Riesgo alto.",
        "critical_risk": "Riesgo critico.",
        "duplicate_exact": "Duplicado exacto.",
    }
    return labels.get(reason, reason.replace("_", " ").strip().capitalize() + ".")


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
