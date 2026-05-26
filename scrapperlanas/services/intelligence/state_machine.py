from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from .schemas import TransitionResult
from .utils import clean_text


CANONICAL_STATES = (
    "new",
    "triaged",
    "interesting",
    "outreach_drafted",
    "contacted",
    "replied",
    "proposal_drafted",
    "proposal_sent",
    "follow_up_due",
    "won",
    "lost",
    "suspicious",
    "discarded",
    "archived",
)

LEGACY_TO_CANONICAL = {
    "NUEVO": "new",
    "VISTO": "triaged",
    "INTERESANTE": "interesting",
    "RESPONDIDO": "replied",
    "APLICADO": "contacted",
    "FOLLOW_UP": "follow_up_due",
    "GANADO": "won",
    "PERDIDO": "lost",
    "SOSPECHOSO": "suspicious",
    "DESCARTADO": "discarded",
}

CANONICAL_TO_LEGACY = {
    "new": "NUEVO",
    "triaged": "VISTO",
    "interesting": "INTERESANTE",
    "outreach_drafted": "INTERESANTE",
    "contacted": "APLICADO",
    "replied": "RESPONDIDO",
    "proposal_drafted": "RESPONDIDO",
    "proposal_sent": "APLICADO",
    "follow_up_due": "FOLLOW_UP",
    "won": "GANADO",
    "lost": "PERDIDO",
    "suspicious": "SOSPECHOSO",
    "discarded": "DESCARTADO",
    "archived": "DESCARTADO",
}

ALLOWED_TRANSITIONS = {
    "new": {"triaged", "interesting", "suspicious", "discarded"},
    "triaged": {"interesting", "discarded", "suspicious"},
    "interesting": {"outreach_drafted", "contacted", "discarded"},
    "outreach_drafted": {"contacted", "discarded"},
    "contacted": {"replied", "follow_up_due", "lost"},
    "replied": {"proposal_drafted", "proposal_sent", "lost"},
    "proposal_drafted": {"proposal_sent", "discarded"},
    "proposal_sent": {"follow_up_due", "won", "lost"},
    "follow_up_due": {"contacted", "replied", "won", "lost", "discarded"},
    "suspicious": {"discarded", "archived", "interesting"},
    "discarded": {"archived", "interesting"},
    "won": {"archived"},
    "lost": {"archived"},
    "archived": set(),
}


def canonical_state(value: str) -> str:
    cleaned = clean_text(value)
    if cleaned in CANONICAL_STATES:
        return cleaned
    return LEGACY_TO_CANONICAL.get(cleaned.upper(), "new")


def available_transitions(state: str, context: Mapping[str, Any] | None = None) -> list[str]:
    context = context or {}
    current = canonical_state(state)
    return [
        candidate
        for candidate in sorted(ALLOWED_TRANSITIONS.get(current, set()))
        if transition_opportunity(current, candidate, context).allowed
    ]


def transition_opportunity(
    current_state: str,
    next_state: str,
    context: Mapping[str, Any] | None = None,
    *,
    actor: str | None = None,
    reason: str = "",
    override: bool = False,
) -> TransitionResult:
    context = context or {}
    current = canonical_state(current_state)
    target = canonical_state(next_state)
    metadata = {
        "actor": actor,
        "timestamp": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "override": override,
    }

    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        return TransitionResult(False, current, target, "transition_not_allowed", reason, metadata)

    guard_result = _guard(current, target, context)
    if guard_result != "ok":
        if override and clean_text(reason):
            metadata["guard_override"] = guard_result
            return TransitionResult(True, current, target, "manual_override", reason, metadata)
        return TransitionResult(False, current, target, guard_result, reason, metadata)

    return TransitionResult(True, current, target, "ok", reason, metadata)


def _guard(current: str, target: str, context: Mapping[str, Any]) -> str:
    has_evidence = bool(context.get("evidence") or context.get("evidence_snippets"))
    if target == "won" and not (context.get("manual_confirmation") or context.get("won_value") or has_evidence):
        return "won_requires_evidence_or_manual_confirmation"
    has_proposal = bool(context.get("proposal_exists") or context.get("proposal_value"))
    has_channel = bool(context.get("action_url") or context.get("apply_url") or context.get("url") or context.get("contact_channel"))
    if target == "proposal_sent" and (not has_proposal or not has_channel):
        return "proposal_sent_requires_proposal_and_channel"
    if current == "suspicious" and target == "interesting" and not context.get("reviewed_by_user"):
        return "suspicious_requires_manual_review"
    if current == "discarded" and target == "interesting" and not context.get("restored_by_user"):
        return "discarded_requires_manual_restore"
    if target == "follow_up_due" and current != "contacted" and not (context.get("last_contacted_at") or context.get("contacted")):
        return "follow_up_requires_previous_contact"
    if target == "contacted" and current in {"discarded", "archived", "suspicious"}:
        return "cannot_contact_blocked_state_without_restore"
    return "ok"
