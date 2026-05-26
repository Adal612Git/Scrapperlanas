from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from .schemas import NextBestAction
from .utils import budget_amount, clean_text, coerce_list, parse_datetime


def recommend_next_best_action(opportunity: Mapping[str, Any], *, now: datetime | None = None) -> NextBestAction:
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)
    grade = clean_text(opportunity.get("grade") or opportunity.get("score_tier") or opportunity.get("priority_tier")).upper()
    status = clean_text(opportunity.get("commercial_status") or opportunity.get("canonical_state") or "new").lower()
    status = status.replace("-", "_")
    if status == "followup_due":
        status = "follow_up_due"
    state = clean_text(opportunity.get("state")).upper()
    contacts = coerce_list(opportunity.get("contact_signals"))
    contactability_high = bool(contacts or opportunity.get("buyer_domain") or opportunity.get("apply_url") or opportunity.get("url"))
    duplicate_confidence = float(opportunity.get("duplicate_confidence") or 0)
    scam_risk = int(opportunity.get("scam_risk") or 0)
    deadline = parse_datetime(opportunity.get("deadline_at") or opportunity.get("deadline"))
    last_contacted = parse_datetime(opportunity.get("last_contacted_at"))
    days_since_contact = (runtime_now - last_contacted).days if last_contacted else None

    if duplicate_confidence > 0.85:
        return _action("REVIEW_DUPLICATE", "Revisar duplicado", "Hay alta probabilidad de que esta cuenta ya exista.", 95, 88, "duplicate_confidence > 0.85")
    if scam_risk >= 65 or state == "SOSPECHOSO":
        return _action("REVIEW_RISK", "Revisar riesgo", "Antes de contactar, valida fuente, comprador y evidencia.", 92, 82, "Riesgo alto o estado sospechoso.")
    if deadline and deadline < runtime_now and status in {"new", "reviewed"}:
        return _action("DISCARD_LOW_VALUE", "Descartar o esperar", "El deadline ya vencio y no hubo contacto previo.", 72, 78, "Deadline vencido.")
    if grade in {"A1", "A2"} and status in {"new", "reviewed", ""} and contactability_high:
        return _action("REDACT_OUTREACH", "Redactar mensaje", "Preparar outreach con evidencia y CTA breve.", 100 if grade == "A1" else 88, 90, "Alta intencion y canal accionable.")
    if grade in {"A1", "A2"} and status in {"new", "reviewed", ""}:
        return _action(
            "INVESTIGATE_CONTACT",
            "Investigar contacto",
            "Buscar email, formulario o contacto responsable antes de redactar.",
            86,
            82,
            "Alta intencion con contactabilidad baja.",
            required_fields=["contact_channel", "contact_value"],
        )
    if status == "contacted" and days_since_contact is not None and days_since_contact >= 3:
        return _action("SEND_FOLLOW_UP", "Enviar follow-up", "Ya hubo contacto y pasaron al menos 3 dias.", 84, 86, "Contacto previo sin respuesta.")
    if status == "contacted":
        return _action("WAIT", "Esperar respuesta", "Hubo contacto reciente; evita insistir demasiado pronto.", 46, 72, "Contacto reciente.")
    if status == "replied" and budget_amount(opportunity):
        return _action("PREPARE_PROPOSAL", "Preparar propuesta", "Hay respuesta y valor visible; conviene aterrizar alcance.", 90, 88, "Respuesta recibida con presupuesto.")
    if status in {"proposal", "proposal_sent", "proposal_drafted"}:
        return _action("CREATE_FOLLOW_UP", "Crear follow-up", "Mantener propuesta viva con siguiente paso concreto.", 78, 82, "Propuesta abierta.")
    if grade in {"C", "D"}:
        return _action("DISCARD_LOW_VALUE", "Descartar bajo valor", "No hay suficiente valor, fit o evidencia para perseguir ahora.", 42, 75, "Score bajo.")
    return _action("WAIT", "Mantener en observacion", "Guardar y esperar mejor evidencia o contacto.", 38, 70, "Oportunidad plausible pero incompleta.")


def _action(
    action_type: str,
    label: str,
    description: str,
    priority: int,
    confidence: int,
    reason: str,
    *,
    required_fields: list[str] | None = None,
    blocking_issues: list[str] | None = None,
) -> NextBestAction:
    return NextBestAction(
        action_type=action_type,
        label=label,
        description=description,
        priority=priority,
        confidence=confidence,
        reason=reason,
        required_fields=required_fields or [],
        blocking_issues=blocking_issues or [],
        suggested_payload={"action_type": action_type},
    )
