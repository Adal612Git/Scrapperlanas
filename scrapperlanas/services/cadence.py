from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any


COMMERCIAL_STATUSES = (
    "new",
    "reviewed",
    "contacted",
    "followup_due",
    "replied",
    "discovery",
    "proposal",
    "won",
    "lost",
    "ignored",
    "snoozed",
)

CLOSED_COMMERCIAL_STATUSES = {"won", "lost", "ignored"}

LOST_REASONS = (
    "price",
    "no_budget",
    "no_response",
    "bad_fit",
    "deadline_missed",
    "competitor",
    "other",
)

IGNORED_REASONS = (
    "spammy",
    "low_value",
    "bad_fit",
    "old",
    "no_contact",
    "duplicate",
    "other",
)

STATUS_LABELS = {
    "new": "Nueva",
    "reviewed": "Revisada",
    "contacted": "Contactada",
    "followup_due": "Seguimiento vencido",
    "replied": "Respondio",
    "discovery": "Discovery",
    "proposal": "Propuesta",
    "won": "Ganada",
    "lost": "Perdida",
    "ignored": "Ignorada",
    "snoozed": "Pausada",
}


def add_business_days(start: datetime, days: int) -> datetime:
    if days <= 0:
        return start

    current = start
    remaining = days
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def apply_commercial_action(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int | None,
    action: str,
    notes: str = "",
    channel: str = "",
    contact_value: str = "",
    contact_url: str = "",
    proposal_value: int | None = None,
    won_value: int | None = None,
    lost_reason: str = "",
    ignored_reason: str = "",
    snoozed_until: str = "",
    message_snapshot: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict:
    opportunity = _get_opportunity(db, opportunity_id)
    if opportunity is None:
        raise LookupError(f"Opportunity {opportunity_id} not found.")

    action_key = str(action or "").strip().lower().replace("-", "_")
    if action_key == "snooze":
        action_key = "snoozed"
    if action_key not in {
        "status",
        "new",
        "reviewed",
        "contacted",
        "followup_due",
        "followup_completed",
        "replied",
        "discovery",
        "proposal",
        "won",
        "lost",
        "ignored",
        "snoozed",
        "note",
    }:
        raise ValueError(f"Accion comercial invalida: {action}.")

    now = datetime.now(UTC).replace(microsecond=0)
    now_text = now.isoformat()
    from_status = _normalize_status(opportunity["commercial_status"])
    to_status = from_status
    activity_type = action_key
    updates: dict[str, Any] = {"updated_at": _sql_current_timestamp()}
    clean_notes = str(notes or "").strip()
    clean_channel = str(channel or "").strip()
    metadata_payload = dict(metadata or {})

    if action_key == "status":
        requested_status = _normalize_status(metadata_payload.get("to_status"))
        if requested_status not in COMMERCIAL_STATUSES:
            raise ValueError("Estado comercial invalido.")
        return apply_commercial_action(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=actor_user_id,
            action=_action_for_status(requested_status),
            notes=clean_notes,
            channel=clean_channel,
            contact_value=contact_value,
            contact_url=contact_url,
            proposal_value=proposal_value,
            won_value=won_value,
            lost_reason=lost_reason,
            ignored_reason=ignored_reason,
            snoozed_until=snoozed_until,
            message_snapshot=message_snapshot,
            metadata=metadata_payload,
        )

    if action_key == "new":
        to_status = "new"
        activity_type = "created"
        updates["next_best_action"] = _initial_next_action(opportunity)

    elif action_key == "reviewed":
        to_status = "reviewed"
        activity_type = "reviewed"
        updates["next_best_action"] = _initial_next_action(opportunity, reviewed=True)

    elif action_key == "followup_due":
        to_status = "followup_due"
        activity_type = "followup_scheduled"
        updates["next_best_action"] = "Enviar follow-up pendiente"

    elif action_key == "contacted":
        to_status = "contacted"
        activity_type = "contacted"
        updates.update(
            {
                "last_contacted_at": now_text,
                "followup_count": 0,
                "next_followup_at": add_business_days(now, 3).isoformat(),
                "next_best_action": "Dar seguimiento en 3 dias habiles",
                "contact_channel": clean_channel,
                "contact_value": str(contact_value or "").strip(),
                "contact_url": str(contact_url or "").strip(),
            }
        )

    elif action_key == "followup_completed":
        activity_type = "followup_completed"
        to_status = "proposal" if from_status == "proposal" else "contacted"
        followup_count = int(opportunity["followup_count"] or 0) + 1
        updates["followup_count"] = followup_count
        if followup_count == 1:
            updates["next_followup_at"] = add_business_days(now, 5).isoformat()
            updates["next_best_action"] = "Enviar follow-up 2"
        elif followup_count == 2:
            updates["next_followup_at"] = add_business_days(now, 7).isoformat()
            updates["next_best_action"] = "Enviar cierre amable"
        else:
            updates["next_followup_at"] = None
            updates["next_best_action"] = "Cerrar o pausar oportunidad"

    elif action_key == "replied":
        to_status = "replied"
        activity_type = "replied"
        updates["next_followup_at"] = None
        updates["next_best_action"] = "Responder y calificar necesidad"

    elif action_key == "discovery":
        to_status = "discovery"
        activity_type = "discovery_started"
        updates["next_best_action"] = "Documentar discovery y preparar propuesta"

    elif action_key == "proposal":
        to_status = "proposal"
        activity_type = "proposal_sent"
        updates["next_followup_at"] = add_business_days(now, 3).isoformat()
        updates["next_best_action"] = "Seguir propuesta"
        if proposal_value is not None:
            updates["proposal_value"] = max(0, int(proposal_value))

    elif action_key == "won":
        to_status = "won"
        activity_type = "won"
        updates["next_followup_at"] = None
        updates["next_best_action"] = "Registrar aprendizaje y preparar entrega"
        if won_value is not None:
            updates["won_value"] = max(0, int(won_value))

    elif action_key == "lost":
        reason = str(lost_reason or "other").strip() or "other"
        if reason not in LOST_REASONS:
            raise ValueError("Motivo de perdida invalido.")
        to_status = "lost"
        activity_type = "lost"
        updates["next_followup_at"] = None
        updates["next_best_action"] = "No perseguir; revisar aprendizaje"
        updates["lost_reason"] = reason

    elif action_key == "ignored":
        reason = str(ignored_reason or "other").strip() or "other"
        if reason not in IGNORED_REASONS:
            raise ValueError("Motivo de ignorado invalido.")
        to_status = "ignored"
        activity_type = "ignored"
        updates["next_followup_at"] = None
        updates["next_best_action"] = "Descartada por criterio comercial"
        updates["ignored_reason"] = reason

    elif action_key == "snoozed":
        parsed_snooze = _parse_datetime(snoozed_until)
        if parsed_snooze is None:
            parsed_snooze = add_business_days(now, 3)
        to_status = "snoozed"
        activity_type = "snoozed"
        updates["snoozed_until"] = parsed_snooze.isoformat()
        updates["next_followup_at"] = parsed_snooze.isoformat()
        updates["next_best_action"] = "Retomar en fecha programada"

    elif action_key == "note":
        activity_type = "note_added"
        updates["commercial_notes"] = _append_note(opportunity["commercial_notes"], clean_notes, now)

    if action_key != "note":
        updates["commercial_status"] = to_status
        if to_status != from_status:
            updates["last_status_at"] = now_text
        if to_status in CLOSED_COMMERCIAL_STATUSES:
            updates["snoozed_until"] = None

    _update_opportunity(db, opportunity_id, updates)
    _create_activity(
        db,
        opportunity_id=opportunity_id,
        actor_user_id=actor_user_id,
        activity_type=activity_type,
        from_status=from_status,
        to_status=to_status,
        channel=clean_channel,
        message_snapshot=str(message_snapshot or "").strip(),
        notes=clean_notes,
        metadata_json=json.dumps(metadata_payload, ensure_ascii=False),
    )
    updated = _get_opportunity(db, opportunity_id)
    if updated is not None and updated["buyer_account_id"]:
        from .buyer_intelligence import recalculate_buyer_account

        recalculate_buyer_account(db, int(updated["buyer_account_id"]))
    db.commit()
    return dict(updated) if updated is not None else {}


def _get_opportunity(db, opportunity_id: int):
    return db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()


def _normalize_status(status: Any) -> str:
    value = str(status or "new").strip().lower()
    return value if value in COMMERCIAL_STATUSES else "new"


def _action_for_status(status: str) -> str:
    return status


def _initial_next_action(opportunity, *, reviewed: bool = False) -> str:
    tier = str(opportunity["score_tier"] or "").strip().upper()
    if tier == "A1":
        return "Contactar hoy"
    if tier == "A2":
        return "Revisar esta semana" if not reviewed else "Preparar contacto esta semana"
    return str(opportunity["next_best_action"] or "").strip() or "Revisar cuando haya capacidad"


def _update_opportunity(db, opportunity_id: int, updates: dict[str, Any]) -> None:
    assignments: list[str] = []
    params: list[Any] = []
    for column, value in updates.items():
        if isinstance(value, _SqlExpression):
            assignments.append(f"{column} = {value.expression}")
        else:
            assignments.append(f"{column} = ?")
            params.append(value)
    params.append(opportunity_id)
    db.execute(
        f"UPDATE opportunities SET {', '.join(assignments)} WHERE id = ?",
        tuple(params),
    )


def _create_activity(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int | None,
    activity_type: str,
    from_status: str,
    to_status: str,
    channel: str,
    message_snapshot: str,
    notes: str,
    metadata_json: str,
) -> None:
    db.execute(
        """
        INSERT INTO commercial_activities (
            opportunity_id,
            actor_user_id,
            activity_type,
            from_status,
            to_status,
            channel,
            message_snapshot,
            notes,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            actor_user_id,
            activity_type,
            from_status,
            to_status,
            channel,
            message_snapshot,
            notes,
            metadata_json,
        ),
    )


def _append_note(existing_notes: str, note: str, created_at: datetime) -> str:
    if not note:
        return str(existing_notes or "")
    stamp = created_at.strftime("%Y-%m-%d %H:%M UTC")
    entry = f"[{stamp}] {note}"
    existing = str(existing_notes or "").strip()
    return f"{existing}\n{entry}" if existing else entry


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}T09:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


class _SqlExpression:
    def __init__(self, expression: str) -> None:
        self.expression = expression


def _sql_current_timestamp() -> _SqlExpression:
    return _SqlExpression("CURRENT_TIMESTAMP")
