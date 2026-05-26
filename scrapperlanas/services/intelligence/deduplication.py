from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Mapping

from .schemas import DedupeAssessment
from .utils import clean_text, coerce_list, normalize_domain


def compare_accounts(left: Mapping[str, Any], right: Mapping[str, Any]) -> DedupeAssessment:
    reasons: list[str] = []
    conflicts: list[str] = []
    score = 0.0

    left_domain = normalize_domain(left.get("buyer_domain") or left.get("domain") or left.get("normalized_domain"))
    right_domain = normalize_domain(right.get("buyer_domain") or right.get("domain") or right.get("normalized_domain"))
    if left_domain and right_domain:
        if left_domain == right_domain:
            score += 0.45
            reasons.append("Dominio normalizado identico.")
        else:
            conflicts.append("domain")

    left_name = clean_text(left.get("buyer_name") or left.get("company") or left.get("name")).lower()
    right_name = clean_text(right.get("buyer_name") or right.get("company") or right.get("name")).lower()
    if left_name and right_name:
        similarity = SequenceMatcher(None, left_name, right_name).ratio()
        if similarity >= 0.92:
            score += 0.25
            reasons.append("Nombre de comprador casi identico.")
        elif similarity >= 0.78:
            score += 0.14
            reasons.append("Nombre de comprador similar.")

    if clean_text(left.get("country")).lower() and clean_text(right.get("country")).lower():
        if clean_text(left.get("country")).lower() == clean_text(right.get("country")).lower():
            score += 0.08
        else:
            conflicts.append("country")

    left_contacts = set(item.lower() for item in coerce_list(left.get("contact_signals")))
    right_contacts = set(item.lower() for item in coerce_list(right.get("contact_signals")))
    if left_contacts and right_contacts and left_contacts.intersection(right_contacts):
        score += 0.16
        reasons.append("Contactos solapados.")

    score += min(0.06, _overlap(left.get("skills") or left.get("required_skills"), right.get("skills") or right.get("required_skills")) * 0.08)
    score += min(0.08, _overlap(left.get("pain_signals"), right.get("pain_signals")) * 0.10)
    score = round(min(1.0, score), 2)

    if score >= 0.85:
        action = "merge"
    elif score >= 0.55:
        action = "review"
    else:
        action = "keep_separate"

    return DedupeAssessment(score, action, reasons or ["No hay evidencia fuerte de duplicado."], conflicts)


def _overlap(left_value: Any, right_value: Any) -> float:
    left = set(item.lower() for item in coerce_list(left_value))
    right = set(item.lower() for item in coerce_list(right_value))
    if not left or not right:
        return 0.0
    return len(left.intersection(right)) / max(len(left), len(right))
