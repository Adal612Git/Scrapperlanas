from __future__ import annotations

from datetime import UTC, datetime

from .ingestion import NormalizedOpportunity
from .quality import QualityAssessment, apply_quality_to_score, assess_opportunity_quality


TARGET_TERMS = (
    "python",
    "scraping",
    "scraper",
    "automation",
    "n8n",
    "api",
    "integration",
    "integracion",
    "dashboard",
    "data",
    "etl",
    "pipeline",
    "backend",
    "flask",
    "postgresql",
    "ai",
    "llm",
    "cloud",
)

NEGATIVE_FIT_TERMS = (
    "frontend",
    "front-end",
    "designer",
    "ios",
    "android",
    "wordpress",
    "shopware",
    "firmware",
    "intern",
    "student",
    "volunteer",
    "unpaid",
)

BUYING_SIGNAL_TERMS = (
    "budget",
    "paid",
    "bounty",
    "contract",
    "freelance",
    "consultant",
    "quote",
    "proposal",
    "solicitation",
    "procurement",
    "set-aside",
    "award",
    "invoice",
    "$",
    "usd",
)

URGENCY_TERMS = (
    "urgent",
    "asap",
    "immediate",
    "critical",
    "deadline",
    "response deadline",
    "due",
)

SOURCE_TYPE_BONUS = {
    "procurement": 28,
    "grant": 18,
    "direct_rfp": 18,
    "github_issue": 8,
    "hiring_signal": 24,
    "community": 8,
    "community_signal": 8,
}

TIER_LABELS = {
    "A1": "contactar hoy",
    "A2": "revisar esta semana",
    "B": "nutrir",
    "C": "ruido",
    "D": "descartar",
}


def score_commercial_opportunity(
    opportunity: NormalizedOpportunity,
    *,
    base_score: int = 0,
    enrichment: dict | None = None,
    preferred_keywords: tuple[str, ...] = (),
    min_budget: int = 0,
    now: datetime | None = None,
    quality: QualityAssessment | None = None,
) -> dict:
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)
    enrichment = enrichment or {}
    text = _combined_text(opportunity, enrichment)
    deadline = _parse_datetime(opportunity.deadline_at)
    published_at = _parse_datetime(opportunity.posted_at)
    budget_anchor = opportunity.estimated_value or opportunity.budget_max or opportunity.budget_min

    money, money_reasons = _money_score(opportunity, text, budget_anchor, min_budget)
    fit, fit_reasons = _fit_score(opportunity, text, preferred_keywords, base_score)
    urgency, urgency_reasons, expired = _urgency_score(text, deadline, published_at, runtime_now)
    contactability, contact_reasons = _contactability_score(opportunity)
    confidence, confidence_reasons = _confidence_score(opportunity, enrichment, text)

    total = round(
        (0.30 * money)
        + (0.25 * fit)
        + (0.20 * urgency)
        + (0.15 * contactability)
        + (0.10 * confidence)
    )

    if expired:
        total = min(total, 20)
    if opportunity.is_suspicious or opportunity.risk_level == "high":
        total = min(total, 35)

    tier = _tier_for_score(total, expired=expired, suspicious=opportunity.is_suspicious)
    reasons = [*money_reasons, *fit_reasons, *urgency_reasons, *contact_reasons, *confidence_reasons]
    reasons = _unique(reasons)[:6]
    next_best_action = _next_best_action(tier, opportunity)

    result = {
        "score_total": max(0, min(100, total)),
        "score_money": money,
        "score_fit": fit,
        "score_urgency": urgency,
        "score_contactability": contactability,
        "score_confidence": confidence,
        "score_tier": tier,
        "score_label": TIER_LABELS[tier],
        "score_reasons": reasons,
        "next_best_action": next_best_action,
        "deadline_at": opportunity.deadline_at,
        "estimated_value": budget_anchor,
    }
    return apply_quality_to_score(result, quality or assess_opportunity_quality(opportunity))


def _money_score(
    opportunity: NormalizedOpportunity,
    text: str,
    budget_anchor: int | None,
    min_budget: int,
) -> tuple[int, list[str]]:
    score = 10
    reasons = []
    source_type = (opportunity.source_type or "").strip().lower()
    score += SOURCE_TYPE_BONUS.get(source_type, 8)

    if source_type == "procurement":
        reasons.append("Es procurement: hay proceso formal de compra.")
    elif source_type == "direct_rfp":
        reasons.append("Parece solicitud directa de trabajo.")
    elif source_type == "hiring_signal":
        reasons.append("La empresa muestra dolor operativo contratando talento tecnico.")

    if budget_anchor:
        if budget_anchor >= 10_000:
            score += 42
        elif budget_anchor >= 4_000:
            score += 36
        elif budget_anchor >= 2_000:
            score += 28
        elif budget_anchor >= 1_000:
            score += 18
        else:
            score += 10
        reasons.append("Tiene valor o presupuesto visible.")
        if min_budget and budget_anchor < min_budget:
            score -= 18
            reasons.append("El valor visible queda por debajo del minimo deseado.")
    elif source_type == "hiring_signal":
        score += 16
        reasons.append("No hay presupuesto visible, pero contratar perfiles tecnicos caros implica presupuesto probable.")
    elif any(term in text for term in BUYING_SIGNAL_TERMS):
        score += 18
        reasons.append("Tiene senales de compra aunque no monto claro.")
    else:
        score -= 12
        reasons.append("No hay presupuesto visible.")

    if not (opportunity.buyer_name or opportunity.company):
        score -= 18
        reasons.append("No hay comprador claro.")
    else:
        score += 8
        reasons.append("Tiene comprador identificado.")

    return _clamp(score), reasons


def _fit_score(
    opportunity: NormalizedOpportunity,
    text: str,
    preferred_keywords: tuple[str, ...],
    base_score: int,
) -> tuple[int, list[str]]:
    score = min(35, max(0, base_score // 2))
    reasons = []
    matched = []
    for term in TARGET_TERMS:
        if term in text:
            score += 8
            matched.append(term)
    for keyword in preferred_keywords:
        normalized = keyword.strip().lower()
        if normalized and normalized in text and normalized not in matched:
            score += 7
            matched.append(normalized)
    for term in NEGATIVE_FIT_TERMS:
        if term in text:
            score -= 12

    if matched:
        reasons.append(f"Encaja con {', '.join(matched[:4])}.")
    else:
        reasons.append("No muestra match tecnico fuerte.")

    if opportunity.source_type == "procurement" and any(term in text for term in ("software", "system", "data")):
        score += 10
    return _clamp(score), reasons


def _urgency_score(
    text: str,
    deadline: datetime | None,
    published_at: datetime | None,
    now: datetime,
) -> tuple[int, list[str], bool]:
    score = 35
    reasons = []
    expired = False

    if deadline is not None:
        days_until = (deadline.astimezone(UTC) - now).days
        if days_until < 0:
            expired = True
            return 0, ["La fecha limite ya vencio."], True
        if 3 <= days_until <= 14:
            score = 92
            reasons.append("Deadline cercano y trabajable.")
        elif 15 <= days_until <= 30:
            score = 76
            reasons.append("Deadline dentro de una ventana comercial sana.")
        elif 0 <= days_until <= 2:
            score = 62
            reasons.append("Deadline muy cercano; requiere decidir rapido.")
        else:
            score = 52
            reasons.append("Tiene deadline, pero no es urgente.")
    elif published_at is not None:
        age_days = (now - published_at.astimezone(UTC)).days
        if age_days <= 3:
            score = 70
            reasons.append("Publicacion reciente.")
        elif age_days <= 14:
            score = 56
            reasons.append("Publicacion aun razonablemente fresca.")
        elif age_days >= 60:
            score = 18
            reasons.append("Publicacion vieja.")

    if any(term in text for term in URGENCY_TERMS):
        score += 10
        reasons.append("El texto contiene senales de urgencia.")

    return _clamp(score), reasons, expired


def _contactability_score(opportunity: NormalizedOpportunity) -> tuple[int, list[str]]:
    score = 20
    reasons = []
    contacts = opportunity.contact_signals or []
    if any(signal.startswith("email:") for signal in contacts):
        score += 46
        reasons.append("Hay email de contacto.")
    if any(signal.startswith("phone:") for signal in contacts):
        score += 14
        reasons.append("Hay telefono de contacto.")
    if opportunity.buyer_domain:
        score += 16
        reasons.append("Hay dominio o canal del comprador.")
    if opportunity.apply_url or opportunity.url:
        score += 14
        reasons.append("Hay URL accionable.")
    if opportunity.source_type == "github_issue":
        score += 8
        reasons.append("Se puede responder en el issue con cuidado.")
    return _clamp(score), reasons


def _confidence_score(opportunity: NormalizedOpportunity, enrichment: dict, text: str) -> tuple[int, list[str]]:
    score = 35
    reasons = []
    if opportunity.evidence_snippets:
        score += min(30, len(opportunity.evidence_snippets) * 10)
        reasons.append("Hay evidencia textual para justificar el tiro.")
    if opportunity.pain_signals:
        score += min(20, len(opportunity.pain_signals) * 5)
    if opportunity.risk_level == "medium":
        score -= 8
    elif opportunity.risk_level == "high":
        score -= 25
    if enrichment.get("confidence") is not None:
        try:
            score = round((score + int(enrichment["confidence"])) / 2)
        except (TypeError, ValueError):
            pass
    if len(text) < 80:
        score -= 12
        reasons.append("La fuente trae poco contexto.")
    return _clamp(score), reasons


def _tier_for_score(total: int, *, expired: bool, suspicious: bool) -> str:
    if expired or suspicious:
        return "D"
    if total >= 75:
        return "A1"
    if total >= 62:
        return "A2"
    if total >= 45:
        return "B"
    if total >= 28:
        return "C"
    return "D"


def _next_best_action(tier: str, opportunity: NormalizedOpportunity) -> str:
    if tier == "A1":
        if any(signal.startswith("email:") for signal in opportunity.contact_signals):
            return "Contactar hoy por email con mensaje corto y evidencia concreta."
        return "Abrir la fuente hoy y responder por el canal disponible."
    if tier == "A2":
        return "Revisar esta semana, validar comprador y preparar outreach manual."
    if tier == "B":
        return "Guardar para nutrir; no gastar energia inmediata."
    if tier == "C":
        return "Mantener en backlog hasta que aparezca mejor evidencia de compra."
    return "Descartar o ignorar salvo que cambie la evidencia."


def _combined_text(opportunity: NormalizedOpportunity, enrichment: dict) -> str:
    parts = [
        opportunity.title,
        opportunity.company,
        opportunity.buyer_name,
        opportunity.raw_text,
        " ".join(opportunity.stack or []),
        " ".join(opportunity.required_skills or []),
        " ".join(opportunity.pain_signals or []),
        str(enrichment.get("summary", "") or ""),
        str(enrichment.get("fit_reason", "") or ""),
    ]
    return " ".join(part for part in parts if part).lower()


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _unique(values: list[str]) -> list[str]:
    seen = set()
    unique_values = []
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique_values.append(cleaned)
    return unique_values


def _clamp(value: int) -> int:
    return max(0, min(100, int(round(value))))
