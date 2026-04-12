from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from flask import current_app

from .ai import LocalAiAssistant
from .ingestion import NormalizedOpportunity, connector_registry


PIPELINE_STATES = (
    "NUEVO",
    "VISTO",
    "INTERESANTE",
    "RESPONDIDO",
    "APLICADO",
    "FOLLOW_UP",
    "GANADO",
    "PERDIDO",
    "SOSPECHOSO",
    "DESCARTADO",
)


KEYWORD_WEIGHTS = {
    "python": 16,
    "scraping": 20,
    "automation": 16,
    "n8n": 18,
    "make": 10,
    "zapier": 9,
    "rust": 12,
    "backend": 12,
    "api": 12,
    "integration": 10,
    "integrations": 10,
    "webhooks": 10,
    "workflow": 8,
    "workflows": 8,
    "pipeline": 9,
    "etl": 9,
    "crm": 8,
    "dashboard": 8,
    "postgresql": 10,
    "fastapi": 11,
    "django": 10,
    "flask": 10,
    "docker": 8,
    "linux": 6,
    "playwright": 12,
    "selenium": 11,
    "beautifulsoup": 10,
    "bs4": 10,
    "ollama": 8,
    "llm": 8,
    "ai": 4,
    "openai": 10,
    "deepseek": 10,
    "rag": 9,
    "agent": 7,
    "agents": 7,
    "langchain": 9,
    "embeddings": 8,
    "vector db": 8,
    "vector database": 8,
    "remote": 4,
    "contract": 10,
    "freelance": 12,
    "project": 8,
    "project-based": 10,
    "short-term": 7,
    "deliverable": 10,
    "hourly": 9,
    "fixed price": 10,
    "fixed-price": 10,
}

POSITIVE_SIGNAL_WEIGHTS = {
    "consultant": 8,
    "immediate": 4,
    "urgent": 4,
    "yc": 3,
    "milestone": 7,
    "quote": 5,
    "bid": 4,
    "budget": 6,
    "contractor": 7,
}

NEGATIVE_SIGNAL_WEIGHTS = {
    "engineering manager": -34,
    "manager": -24,
    "director": -26,
    "head of": -26,
    "vp ": -20,
    "vice president": -20,
    "talent acquisition": -24,
    "recruiter": -22,
    "full-time": -26,
    "permanent": -28,
    "employee benefits": -18,
    "benefits": -10,
    "salary": -12,
    "employment": -16,
    "careers": -14,
    "frontend": -24,
    "front-end": -24,
    "ui/ux": -18,
    "ui ux": -18,
    "ux/ui": -18,
    "designer": -18,
    "figma": -18,
    "ios": -12,
    "android": -12,
    "unity": -18,
    "game dev": -18,
    "game development": -18,
    "mobile": -10,
    "wordpress": -10,
    "wordpress-only": -16,
    "shopify": -10,
    "shopify theme": -16,
    "shopware": -12,
    "bubble": -12,
    "firmware": -16,
    "onsite": -14,
    "on-site": -14,
    "hybrid": -10,
}


def score_opportunity(
    opportunity: NormalizedOpportunity,
    *,
    preferred_keywords: tuple[str, ...] = (),
    min_budget: int = 0,
) -> int:
    text = f"{opportunity.title}\n{opportunity.raw_text}".lower()
    score = 8

    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in text:
            score += weight

    for signal, weight in POSITIVE_SIGNAL_WEIGHTS.items():
        if signal in text:
            score += weight

    if any(marker in text for marker in ("project", "project-based", "short-term", "deliverable", "milestone")):
        score += 6
    if any(marker in text for marker in ("hourly", "fixed price", "fixed-price", "budget")):
        score += 5

    combo_bonuses = (
        (("python", "scraping"), 12),
        (("api", "automation"), 10),
        (("n8n", "crm"), 12),
        (("llm", "automation"), 10),
        (("backend", "integration"), 10),
        (("data", "pipeline"), 8),
        (("playwright", "scraping"), 12),
        (("webhooks", "workflow"), 10),
        (("openai", "business"), 8),
        (("deepseek", "business"), 8),
        (("llm", "workflow"), 8),
        (("ai", "automation"), 8),
        (("python", "automation"), 10),
        (("api", "integration"), 8),
    )
    for tokens, weight in combo_bonuses:
        if all(token in text for token in tokens):
            score += weight

    for signal, weight in NEGATIVE_SIGNAL_WEIGHTS.items():
        if signal in text:
            score += weight

    for keyword in preferred_keywords:
        normalized = keyword.strip().lower()
        if normalized and normalized not in KEYWORD_WEIGHTS and normalized in text:
            score += 6

    budget_anchor = opportunity.budget_max or opportunity.budget_min
    if budget_anchor:
        if budget_anchor >= 4000:
            score += 18
        elif budget_anchor >= 2000:
            score += 12
        elif budget_anchor >= 1000:
            score += 7
        else:
            score += 2
        if min_budget and budget_anchor < min_budget:
            score -= 12
    elif min_budget:
        score -= 6

    if any(marker in text for marker in ("freelance", "contract", "hourly", "fixed price", "fixed-price")):
        score += 4
    if any(marker in text for marker in ("full-time", "permanent", "salary", "benefits", "employment")):
        score -= 12
    if any(marker in text for marker in ("wordpress", "shopify", "figma", "ui ux", "ui/ux")) and not any(
        marker in text for marker in ("backend", "api", "automation", "scraping", "integration", "workflow")
    ):
        score -= 10

    if opportunity.posted_at:
        published_at = _parse_posted_at(opportunity.posted_at)
        if published_at is not None:
            age_days = (datetime.now(UTC) - published_at.astimezone(UTC)).days
            if age_days <= 1:
                score += 8
            elif age_days <= 7:
                score += 4

    if opportunity.risk_level == "medium":
        score -= 8
    elif opportunity.risk_level == "high":
        score -= 20

    if opportunity.is_suspicious:
        score -= 12

    return max(0, min(score, 99))


def create_event(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int | None,
    event_type: str,
    previous_state: str | None = None,
    new_state: str | None = None,
    note: str = "",
) -> None:
    db.execute(
        """
        INSERT INTO opportunity_events (
            opportunity_id,
            actor_user_id,
            event_type,
            previous_state,
            new_state,
            note
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            opportunity_id,
            actor_user_id,
            event_type,
            previous_state,
            new_state,
            note,
        ),
    )


def update_opportunity_state(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int,
    new_state: str,
    note: str = "",
) -> None:
    if new_state not in PIPELINE_STATES:
        raise ValueError("Estado no permitido.")

    opportunity = db.execute(
        "SELECT state FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    if opportunity is None:
        raise LookupError("Oportunidad no encontrada.")

    previous_state = opportunity["state"]

    if new_state != previous_state:
        db.execute(
            """
            UPDATE opportunities
            SET state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_state, opportunity_id),
        )
        create_event(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=actor_user_id,
            event_type="STATE_CHANGED",
            previous_state=previous_state,
            new_state=new_state,
            note=note.strip(),
        )
    elif note.strip():
        create_event(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=actor_user_id,
            event_type="NOTE_ADDED",
            previous_state=previous_state,
            new_state=previous_state,
            note=note.strip(),
        )
    else:
        return

    db.commit()


def update_opportunity_status(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int,
    new_state: str,
    substate: str = "",
    note: str = "",
) -> None:
    if new_state not in PIPELINE_STATES:
        raise ValueError("Estado no permitido.")

    opportunity = db.execute(
        "SELECT state FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    if opportunity is None:
        raise LookupError("Oportunidad no encontrada.")

    previous_state = opportunity["state"]
    db.execute(
        """
        UPDATE opportunities
        SET state = ?, substate = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_state, substate.strip(), opportunity_id),
    )
    create_event(
        db,
        opportunity_id=opportunity_id,
        actor_user_id=actor_user_id,
        event_type="STATE_CHANGED",
        previous_state=previous_state,
        new_state=new_state,
        note=note.strip(),
    )
    db.commit()


def update_opportunity_note(
    db,
    *,
    opportunity_id: int,
    actor_user_id: int,
    note: str,
) -> None:
    note = note.strip()
    opportunity = db.execute(
        "SELECT operational_note FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    if opportunity is None:
        raise LookupError("Oportunidad no encontrada.")

    db.execute(
        """
        UPDATE opportunities
        SET operational_note = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (note, opportunity_id),
    )
    create_event(
        db,
        opportunity_id=opportunity_id,
        actor_user_id=actor_user_id,
        event_type="NOTE_UPDATED",
        previous_state=None,
        new_state=None,
        note=note,
    )
    db.commit()


def run_ingestion(db, profile=None) -> dict:
    return run_ingestion_for_policies(db, profile=profile)


def run_ingestion_for_policies(
    db,
    *,
    profile=None,
    source_keys: tuple[str, ...] | None = None,
    due_only: bool = False,
    now: datetime | None = None,
    trigger: str = "manual",
) -> dict:
    assistant = LocalAiAssistant()
    connectors = connector_registry()
    selected_source_keys = None if source_keys is None else tuple(dict.fromkeys(source_keys))
    stats = {
        "policies_run": 0,
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "policy_details": [],
        "due_only": due_only,
        "selected_source_keys": list(selected_source_keys or ()),
    }
    enrichments_remaining = max(0, int(current_app.config["AI_MAX_ENRICHMENTS_PER_RUN"]))
    min_score_for_remote_enrichment = int(current_app.config["AI_MIN_SCORE_FOR_REMOTE_ENRICHMENT"])
    policies = (
        list_due_policies(db, source_keys=selected_source_keys, now=now)
        if due_only
        else list_enabled_policies(db, source_keys=selected_source_keys)
    )

    for policy in policies:
        policy_started_at = _utcnow()
        policy_stats = {
            "source_key": policy["source_key"],
            "display_name": policy["display_name"],
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "error": None,
        }
        connector = connectors.get(policy["source_key"])
        if connector is None:
            policy_stats["error"] = f"Sin conector para {policy['source_key']}."
            _record_automation_run(
                db,
                trigger=trigger,
                policy_stats=policy_stats,
                started_at=policy_started_at,
                finished_at=_utcnow(),
            )
            _merge_policy_stats(stats, policy_stats)
            continue

        stats["policies_run"] += 1
        try:
            opportunities = connector.fetch(policy)
        except Exception as exc:
            policy_stats["error"] = f"{policy['display_name']}: {exc}"
            _record_automation_run(
                db,
                trigger=trigger,
                policy_stats=policy_stats,
                started_at=policy_started_at,
                finished_at=_utcnow(),
            )
            _merge_policy_stats(stats, policy_stats)
            continue

        policy_stats, enrichments_remaining = ingest_policy_opportunities(
            db,
            policy,
            opportunities=opportunities,
            profile=profile,
            assistant=assistant,
            enrichments_remaining=enrichments_remaining,
            min_score_for_remote_enrichment=min_score_for_remote_enrichment,
            now=now,
            existing_stats=policy_stats,
        )
        _record_automation_run(
            db,
            trigger=trigger,
            policy_stats=policy_stats,
            started_at=policy_started_at,
            finished_at=_utcnow(),
        )
        _merge_policy_stats(stats, policy_stats)

    return stats


def import_opportunities(
    db,
    *,
    source_key: str,
    items: list[dict],
    source_label: str | None = None,
    profile=None,
    trigger: str = "import_api",
) -> dict:
    started_at = _utcnow()
    policy = get_policy_by_source_key(db, source_key)
    if policy is None:
        policy = {
            "id": None,
            "source_key": source_key,
            "display_name": source_label or source_key.replace("_", " ").title(),
            "risk_level": "low",
            "config_json": "{}",
        }
    else:
        policy = dict(policy)
        if source_label:
            policy["display_name"] = source_label

    normalized: list[NormalizedOpportunity] = []
    invalid_items = 0
    for item in items:
        if not isinstance(item, dict):
            invalid_items += 1
            continue
        prepared_item = _prepare_import_payload_item(item, policy)
        opportunity = _normalized_opportunity_from_payload(prepared_item, policy)
        if opportunity is None:
            invalid_items += 1
            continue
        normalized.append(opportunity)

    normalized = _dedupe_import_payloads(normalized)

    stats = {
        "policies_run": 1,
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "skipped": invalid_items,
        "errors": [],
        "policy_details": [],
        "imported_items": len(normalized),
        "invalid_items": invalid_items,
    }
    assistant = LocalAiAssistant()
    min_score_for_remote_enrichment = int(current_app.config["AI_MIN_SCORE_FOR_REMOTE_ENRICHMENT"])
    policy_stats, _remaining = ingest_policy_opportunities(
        db,
        policy,
        opportunities=normalized,
        profile=profile,
        assistant=assistant,
        enrichments_remaining=max(0, int(current_app.config["AI_MAX_ENRICHMENTS_PER_RUN"])),
        min_score_for_remote_enrichment=min_score_for_remote_enrichment,
        now=None,
        existing_stats={
            "source_key": policy["source_key"],
            "display_name": policy["display_name"],
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": invalid_items,
            "error": None,
        },
    )
    _record_automation_run(
        db,
        trigger=trigger,
        policy_stats=policy_stats,
        started_at=started_at,
        finished_at=_utcnow(),
    )
    _merge_policy_stats(stats, policy_stats)
    return stats


def list_enabled_policies(
    db,
    *,
    source_keys: tuple[str, ...] | None = None,
) -> list:
    if source_keys is not None and not source_keys:
        return []

    query = "SELECT * FROM source_policies WHERE enabled = 1"
    params: list[str] = []
    if source_keys:
        placeholders = ", ".join("?" for _ in source_keys)
        query += f" AND source_key IN ({placeholders})"
        params.extend(source_keys)
    query += " ORDER BY id ASC"
    return db.execute(query, tuple(params)).fetchall()


def list_due_policies(
    db,
    *,
    source_keys: tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> list:
    runtime_now = now or datetime.now(UTC)
    due_policies = []
    for policy in list_enabled_policies(db, source_keys=source_keys):
        if policy_runtime_status(policy, now=runtime_now)["is_due"]:
            due_policies.append(policy)
    return due_policies


def get_policy_by_source_key(db, source_key: str):
    return db.execute(
        "SELECT * FROM source_policies WHERE source_key = ?",
        (source_key,),
    ).fetchone()


def policy_runtime_status(policy_row, *, now: datetime | None = None) -> dict:
    policy = dict(policy_row)
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)
    frequency_minutes = max(1, int(policy.get("frequency_minutes") or 1))
    last_run_at = _parse_posted_at(policy.get("last_run_at"))

    if last_run_at is None:
        return {
            "source_key": policy["source_key"],
            "display_name": policy["display_name"],
            "frequency_minutes": frequency_minutes,
            "last_run_at": None,
            "next_run_at": None,
            "is_due": True,
            "seconds_until_due": 0,
            "minutes_until_due": 0,
        }

    next_run_at = last_run_at.astimezone(UTC) + timedelta(minutes=frequency_minutes)
    seconds_until_due = max(0, int((next_run_at - runtime_now).total_seconds()))
    return {
        "source_key": policy["source_key"],
        "display_name": policy["display_name"],
        "frequency_minutes": frequency_minutes,
        "last_run_at": last_run_at.astimezone(UTC).replace(microsecond=0).isoformat(),
        "next_run_at": next_run_at.replace(microsecond=0).isoformat(),
        "is_due": next_run_at <= runtime_now,
        "seconds_until_due": 0 if next_run_at <= runtime_now else seconds_until_due,
        "minutes_until_due": 0 if next_run_at <= runtime_now else max(1, seconds_until_due // 60),
    }


def ingest_policy_opportunities(
    db,
    policy_row,
    *,
    opportunities: list[NormalizedOpportunity],
    profile=None,
    assistant: LocalAiAssistant | None = None,
    enrichments_remaining: int,
    min_score_for_remote_enrichment: int,
    now: datetime | None = None,
    existing_stats: dict | None = None,
) -> tuple[dict, int]:
    policy = dict(policy_row)
    policy_stats = existing_stats or {
        "source_key": policy["source_key"],
        "display_name": policy["display_name"],
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "error": None,
    }
    assistant = assistant or LocalAiAssistant()
    preferred_keywords, min_budget = _profile_context(profile)
    policy_config = _load_policy_config(policy)
    min_score = max(0, _safe_int(policy_config.get("min_score"), default=0))
    max_age_days = max(0, _safe_int(policy_config.get("max_age_days"), default=0))
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)

    if policy.get("id") is not None:
        db.execute(
            "UPDATE source_policies SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (policy["id"],),
        )

    prepared_opportunities = []
    for opportunity in opportunities:
        base_score = score_opportunity(
            opportunity,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
        )
        prepared_opportunities.append((base_score, opportunity))

    prepared_opportunities.sort(key=lambda item: item[0], reverse=True)

    for base_score, opportunity in prepared_opportunities:
        if not opportunity.url:
            policy_stats["skipped"] += 1
            continue
        if min_score and base_score < min_score:
            policy_stats["skipped"] += 1
            continue
        if not _within_age_window(opportunity.posted_at, max_age_days=max_age_days, now=runtime_now):
            policy_stats["skipped"] += 1
            continue

        policy_stats["fetched"] += 1
        use_remote_enrichment = (
            enrichments_remaining > 0 and base_score >= min_score_for_remote_enrichment
        )

        enrichment = assistant.enrich(
            title=opportunity.title,
            raw_text=opportunity.raw_text,
            stack=opportunity.stack,
            budget_text=opportunity.budget_text,
            source_label=opportunity.source_label,
            risk_level=opportunity.risk_level,
            sector=opportunity.sector,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
            base_score=base_score,
            force_heuristic=not use_remote_enrichment,
        )
        if use_remote_enrichment:
            enrichments_remaining -= 1

        created, updated = _store_enriched_opportunity(
            db,
            opportunity=opportunity,
            enrichment=enrichment,
            base_score=base_score,
        )
        policy_stats["created"] += created
        policy_stats["updated"] += updated

    db.commit()
    return policy_stats, enrichments_remaining


def _profile_context(profile) -> tuple[tuple[str, ...], int]:
    if not profile:
        return (), 0

    raw_keywords = ",".join(
        part
        for part in (
            profile["keywords"] or "",
            profile["sectors"] or "",
        )
        if part
    )
    preferred_keywords = tuple(
        keyword.strip().lower()
        for keyword in raw_keywords.split(",")
        if keyword.strip()
    )
    return preferred_keywords, int(profile["min_budget"] or 0)


def _parse_posted_at(value) -> datetime | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)

    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            return None

    return None


def _load_policy_config(policy_row) -> dict:
    try:
        return json.loads(policy_row.get("config_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _within_age_window(posted_at, *, max_age_days: int, now: datetime) -> bool:
    if max_age_days <= 0:
        return True
    published_at = _parse_posted_at(posted_at)
    if published_at is None:
        return True
    return (now - published_at.astimezone(UTC)).days <= max_age_days


def _store_enriched_opportunity(
    db,
    *,
    opportunity: NormalizedOpportunity,
    enrichment: dict,
    base_score: int,
) -> tuple[int, int]:
    opportunity.score = max(0, min(99, base_score + enrichment.get("score_delta", 0)))
    opportunity.ai_summary = enrichment["summary"]
    opportunity.suggested_reply = enrichment["suggested_reply"]
    opportunity.sector = enrichment["sector"] or opportunity.sector
    opportunity.stack = enrichment["stack"] or opportunity.stack
    analysis_json = json.dumps(
        {
            "fit_label": enrichment.get("fit_label"),
            "fit_reason": enrichment.get("fit_reason"),
            "recommended_action": enrichment.get("recommended_action"),
            "recommended_state": enrichment.get("recommended_state"),
            "confidence": enrichment.get("confidence"),
            "score_delta": enrichment.get("score_delta"),
            "semantic_score": enrichment.get("semantic_score"),
            "missing_info": enrichment.get("missing_info"),
            "model_used": enrichment.get("model_used"),
        },
        ensure_ascii=False,
    )

    existing = db.execute(
        "SELECT id FROM opportunities WHERE url = ?",
        (opportunity.url,),
    ).fetchone()

    if existing:
        db.execute(
            """
            UPDATE opportunities
            SET external_id = ?,
                source_key = ?,
                source_label = ?,
                title = ?,
                company = ?,
                budget_min = ?,
                budget_max = ?,
                budget_text = ?,
                currency = ?,
                sector = ?,
                stack = ?,
                posted_at = ?,
                risk_level = ?,
                risk_reasons = ?,
                score = ?,
                ai_summary = ?,
                suggested_reply = ?,
                analysis_json = ?,
                is_suspicious = ?,
                raw_text = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                opportunity.external_id,
                opportunity.source_key,
                opportunity.source_label,
                opportunity.title,
                opportunity.company,
                opportunity.budget_min,
                opportunity.budget_max,
                opportunity.budget_text,
                opportunity.currency,
                opportunity.sector,
                json.dumps(opportunity.stack, ensure_ascii=False),
                opportunity.posted_at,
                opportunity.risk_level,
                json.dumps(opportunity.risk_reasons, ensure_ascii=False),
                opportunity.score,
                opportunity.ai_summary,
                opportunity.suggested_reply,
                analysis_json,
                int(opportunity.is_suspicious),
                opportunity.raw_text,
                existing["id"],
            ),
        )
        return 0, 1

    initial_state = "SOSPECHOSO" if opportunity.is_suspicious else "NUEVO"
    insert_query = """
    INSERT INTO opportunities (
        external_id,
        source_key,
        source_label,
        title,
        company,
        url,
        budget_min,
        budget_max,
        budget_text,
        currency,
        sector,
        stack,
        posted_at,
        risk_level,
        risk_reasons,
        score,
        ai_summary,
        suggested_reply,
        analysis_json,
        state,
        is_suspicious,
        raw_text
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    if db.engine == "postgres":
        insert_query += " RETURNING id"

    cursor = db.execute(
        insert_query,
        (
            opportunity.external_id,
            opportunity.source_key,
            opportunity.source_label,
            opportunity.title,
            opportunity.company,
            opportunity.url,
            opportunity.budget_min,
            opportunity.budget_max,
            opportunity.budget_text,
            opportunity.currency,
            opportunity.sector,
            json.dumps(opportunity.stack, ensure_ascii=False),
            opportunity.posted_at,
            opportunity.risk_level,
            json.dumps(opportunity.risk_reasons, ensure_ascii=False),
            opportunity.score,
            opportunity.ai_summary,
            opportunity.suggested_reply,
            analysis_json,
            initial_state,
            int(opportunity.is_suspicious),
            opportunity.raw_text,
        ),
    )
    opportunity_id = cursor.fetchone()["id"] if db.engine == "postgres" else cursor.lastrowid
    create_event(
        db,
        opportunity_id=opportunity_id,
        actor_user_id=None,
        event_type="INGESTED",
        new_state=initial_state,
        note=f"Ingestado desde {opportunity.source_label} con score {opportunity.score}.",
    )
    return 1, 0


def _normalized_opportunity_from_payload(item: dict, policy: dict) -> NormalizedOpportunity | None:
    url = str(item.get("url", "") or "").strip()
    if not url:
        return None

    stack = _coerce_list(item.get("stack"))
    risk_reasons = _coerce_list(item.get("risk_reasons"))
    company = _clean_email_sender(str(item.get("company", "") or "").strip())
    title = str(item.get("title", "") or "").strip() or "Sin titulo"
    body_parts = (
        title,
        company,
        str(item.get("raw_text", "") or "").strip(),
        str(item.get("location", "") or "").strip(),
    )
    raw_text = "\n".join(part for part in body_parts if part)
    budget_min = _safe_int_or_none(item.get("budget_min"))
    budget_max = _safe_int_or_none(item.get("budget_max"))
    budget_text = str(item.get("budget_text", "") or "").strip()
    currency = str(item.get("currency", "") or "USD").strip() or "USD"
    if not budget_text and raw_text:
        inferred_min, inferred_max, inferred_text, inferred_currency = extract_budget(raw_text)
        if budget_min is None:
            budget_min = inferred_min
        if budget_max is None:
            budget_max = inferred_max
        if inferred_text:
            budget_text = inferred_text
        if currency == "USD" and inferred_currency:
            currency = inferred_currency
    risk_level = str(item.get("risk_level", "") or "").strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = str(policy.get("risk_level", "low") or "low").strip().lower()

    return NormalizedOpportunity(
        external_id=str(item.get("external_id", "") or url).strip(),
        source_key=str(item.get("source_key", "") or policy["source_key"]).strip(),
        source_label=str(item.get("source_label", "") or policy["display_name"]).strip(),
        title=title,
        company=company,
        url=url,
        raw_text=raw_text or title,
        budget_min=budget_min,
        budget_max=budget_max,
        budget_text=budget_text,
        currency=currency,
        sector=str(item.get("sector", "") or "").strip(),
        stack=stack,
        posted_at=_normalize_posted_at(item.get("posted_at")),
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        is_suspicious=bool(item.get("is_suspicious")),
    )


def _prepare_import_payload_item(item: dict, policy: dict) -> dict:
    source_key = str(item.get("source_key", "") or policy["source_key"]).strip() or policy["source_key"]
    if source_key != "email_alerts":
        return item

    subject = str(item.get("title", "") or item.get("subject", "") or "").strip()
    from_value = item.get("from", "") or item.get("sender", "") or ""
    if isinstance(from_value, dict):
        company = str(
            from_value.get("text")
            or from_value.get("value")
            or from_value.get("address")
            or from_value.get("email")
            or ""
        ).strip()
    else:
        company = _clean_email_sender(str(from_value).strip())
    company = str(item.get("company", "") or company).strip()
    body = _first_non_empty(
        str(item.get("raw_text", "") or "").strip(),
        str(item.get("text", "") or "").strip(),
        str(item.get("textPlain", "") or "").strip(),
        str(item.get("body", "") or "").strip(),
        str(item.get("html", "") or "").strip(),
    )

    prepared = dict(item)
    if subject and not prepared.get("title"):
        prepared["title"] = subject
    if company and not prepared.get("company"):
        prepared["company"] = company
    if body and not prepared.get("raw_text"):
        prepared["raw_text"] = body
    if not prepared.get("posted_at"):
        posted_at = _first_non_empty(
            str(item.get("posted_at", "") or "").strip(),
            str(item.get("date", "") or "").strip(),
            str(item.get("internalDate", "") or "").strip(),
            str(item.get("headers", {}).get("date", "") if isinstance(item.get("headers"), dict) else "").strip(),
        )
        if posted_at:
            prepared["posted_at"] = posted_at

    if not prepared.get("external_id"):
        prepared["external_id"] = _first_non_empty(
            str(item.get("messageId", "") or "").strip(),
            str(item.get("uid", "") or "").strip(),
            str(item.get("id", "") or "").strip(),
            subject,
        )

    if not prepared.get("url"):
        link_match = re.search(r"https?://[^\s<>\"]+", body or "", flags=re.IGNORECASE)
        if link_match:
            prepared["url"] = link_match.group(0).rstrip(").,;]")

    return prepared


def _dedupe_import_payloads(opportunities: list[NormalizedOpportunity]) -> list[NormalizedOpportunity]:
    unique_by_url: dict[str, NormalizedOpportunity] = {}
    unique_by_external_id: dict[str, NormalizedOpportunity] = {}
    deduped: list[NormalizedOpportunity] = []

    for opportunity in opportunities:
        if opportunity.url and opportunity.url in unique_by_url:
            continue
        if opportunity.external_id and opportunity.external_id in unique_by_external_id:
            continue
        if opportunity.url:
            unique_by_url[opportunity.url] = opportunity
        if opportunity.external_id:
            unique_by_external_id[opportunity.external_id] = opportunity
        deduped.append(opportunity)

    return deduped


def _clean_email_sender(value: str) -> str:
    cleaned = " ".join((value or "").split()).strip()
    if not cleaned:
        return ""
    match = re.match(r"^(?P<name>.+?)\s*<[^>]+>$", cleaned)
    if match:
        candidate = match.group("name").strip(" -|")
        if candidate:
            return candidate
    return cleaned


def _normalize_posted_at(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _to_iso_datetime(value)
    text = str(value).strip()
    if not text:
        return None
    return _to_iso_datetime(text) or text


def _coerce_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _record_automation_run(
    db,
    *,
    trigger: str,
    policy_stats: dict,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    status = "error" if policy_stats.get("error") else "ok"
    duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
    db.execute(
        """
        INSERT INTO automation_runs (
            trigger_kind,
            source_key,
            display_name,
            status,
            fetched,
            created,
            updated,
            skipped,
            duration_ms,
            error,
            details_json,
            started_at,
            finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(trigger or "manual").strip() or "manual",
            policy_stats.get("source_key", ""),
            policy_stats.get("display_name", ""),
            status,
            int(policy_stats.get("fetched", 0) or 0),
            int(policy_stats.get("created", 0) or 0),
            int(policy_stats.get("updated", 0) or 0),
            int(policy_stats.get("skipped", 0) or 0),
            duration_ms,
            str(policy_stats.get("error", "") or ""),
            json.dumps(policy_stats, ensure_ascii=False),
            started_at.replace(microsecond=0).isoformat(),
            finished_at.replace(microsecond=0).isoformat(),
        ),
    )
    db.commit()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _merge_policy_stats(stats: dict, policy_stats: dict) -> None:
    stats["fetched"] += policy_stats.get("fetched", 0)
    stats["created"] += policy_stats.get("created", 0)
    stats["updated"] += policy_stats.get("updated", 0)
    stats["skipped"] += policy_stats.get("skipped", 0)
    if policy_stats.get("error"):
        stats["errors"].append(policy_stats["error"])
    stats["policy_details"].append(policy_stats)
