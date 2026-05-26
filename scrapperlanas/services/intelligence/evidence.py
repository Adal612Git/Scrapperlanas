from __future__ import annotations

from typing import Any, Mapping

from .utils import budget_amount, clean_text, coerce_list, parse_datetime, technical_matches


def collect_evidence(opportunity: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    amount = budget_amount(opportunity)
    if amount:
        currency = clean_text(opportunity.get("currency") or opportunity.get("budget_currency") or "USD")
        evidence.append(f"Presupuesto o valor detectado: {currency} {amount:,}.")

    deadline = parse_datetime(opportunity.get("deadline_at") or opportunity.get("deadline"))
    if deadline:
        evidence.append(f"Deadline extraido de la fuente: {deadline.date().isoformat()}.")

    pains = coerce_list(opportunity.get("pain_signals"))
    for pain in pains[:3]:
        evidence.append(f"Dolor tecnico detectado: {pain}.")

    matches = technical_matches(opportunity)
    if matches:
        evidence.append(f"Skill match: {', '.join(matches[:6])}.")

    contacts = coerce_list(opportunity.get("contact_signals"))
    if contacts:
        evidence.append("Hay senales de contacto o canal accionable.")
    elif clean_text(opportunity.get("buyer_domain")):
        evidence.append("Contactabilidad media: dominio disponible, email no detectado.")

    snippets = coerce_list(opportunity.get("evidence") or opportunity.get("evidence_snippets"))
    evidence.extend(snippets[:4])
    return list(dict.fromkeys(clean_text(item) for item in evidence if clean_text(item)))[:8]
