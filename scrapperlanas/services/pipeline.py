from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from flask import current_app

from .ai import LocalAiAssistant
from .buyer_intelligence import assign_buyer_account, recalculate_buyer_account
from .ingestion import NormalizedOpportunity, connector_registry
from .quality import (
    QualityAssessment,
    assess_opportunity_quality,
    build_duplicate_context,
    should_assign_buyer_account,
)
from .scoring import score_commercial_opportunity


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
    "python": 14,
    "scraping": 18,
    "automation": 14,
    "n8n": 16,
    "rust": 12,
    "backend": 10,
    "api": 10,
    "integration": 10,
    "software development": 12,
    "database": 8,
    "data pipeline": 10,
    "dashboard": 8,
    "postgresql": 10,
    "flask": 8,
    "docker": 7,
    "ollama": 9,
    "llm": 9,
    "remote": 4,
    "contract": 3,
    "freelance": 6,
}

POSITIVE_SIGNAL_WEIGHTS = {
    "consultant": 8,
    "hourly": 7,
    "fixed price": 8,
    "bounty": 9,
    "paid": 6,
    "quote": 5,
    "solicitation": 8,
    "combined synopsis": 8,
    "sources sought": 5,
    "set-aside": 4,
    "immediate": 4,
    "urgent": 4,
    "yc": 3,
}

NEGATIVE_SIGNAL_WEIGHTS = {
    "engineering manager": -30,
    "manager": -22,
    "director": -24,
    "head of": -24,
    "frontend": -24,
    "front-end": -24,
    "designer": -16,
    "ios": -12,
    "android": -12,
    "mobile": -10,
    "wordpress": -12,
    "shopware": -12,
    "bubble": -12,
    "firmware": -16,
    "onsite": -12,
    "on-site": -12,
    "hybrid": -8,
}


def score_opportunity(
    opportunity: NormalizedOpportunity,
    *,
    preferred_keywords: tuple[str, ...] = (),
    min_budget: int = 0,
) -> int:
    text = f"{opportunity.title}\n{opportunity.raw_text}".lower()
    score = 10

    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in text:
            score += weight

    for signal, weight in POSITIVE_SIGNAL_WEIGHTS.items():
        if signal in text:
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
        opportunity = _normalized_opportunity_from_payload(item, policy)
        if opportunity is None:
            invalid_items += 1
            continue
        normalized.append(opportunity)

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
        apply_age_window=False,
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
    apply_age_window: bool = True,
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

    duplicate_context = build_duplicate_context(opportunities)
    prepared_opportunities = []
    for opportunity in opportunities:
        quality = assess_opportunity_quality(
            opportunity,
            policy_config=policy_config,
            duplicate_hint=duplicate_context.get(id(opportunity), {}),
        )
        base_score = score_opportunity(
            opportunity,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
        )
        base_score = min(base_score, quality.final_score_cap)
        prepared_opportunities.append((base_score, quality, opportunity))

    prepared_opportunities.sort(key=lambda item: item[0], reverse=True)

    for base_score, quality, opportunity in prepared_opportunities:
        if not opportunity.url:
            policy_stats["skipped"] += 1
            continue
        if min_score and base_score < min_score:
            policy_stats["skipped"] += 1
            continue
        if apply_age_window and not _within_age_window(
            opportunity.posted_at,
            max_age_days=max_age_days,
            now=runtime_now,
        ):
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
            quality=quality,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
            now=runtime_now,
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
    quality: QualityAssessment,
    preferred_keywords: tuple[str, ...] = (),
    min_budget: int = 0,
    now: datetime | None = None,
) -> tuple[int, int]:
    if not quality.budget.is_valid_commercial_budget:
        opportunity.budget_min = None
        opportunity.budget_max = None
        opportunity.estimated_value = None
        opportunity.budget_text = ""
    elif quality.budget.amount_min is not None or quality.budget.amount_max is not None:
        opportunity.budget_min = quality.budget.amount_min
        opportunity.budget_max = quality.budget.amount_max
        opportunity.estimated_value = quality.budget.amount_max or quality.budget.amount_min
        opportunity.budget_text = quality.budget.raw_evidence
        opportunity.currency = quality.budget.currency

    if quality.risk_level in {"HIGH", "CRITICAL"}:
        opportunity.risk_level = "high"
        opportunity.is_suspicious = True
    elif quality.risk_level == "MEDIUM" and opportunity.risk_level == "low":
        opportunity.risk_level = "medium"
    opportunity.risk_reasons = list(dict.fromkeys([*opportunity.risk_reasons, *quality.risk_reasons]))

    commercial_score = score_commercial_opportunity(
        opportunity,
        base_score=base_score,
        enrichment=enrichment,
        preferred_keywords=preferred_keywords,
        min_budget=min_budget,
        now=now,
        quality=quality,
    )
    opportunity.score = commercial_score["score_total"]
    opportunity.ai_summary = enrichment["summary"]
    opportunity.suggested_reply = enrichment["suggested_reply"]
    opportunity.sector = enrichment["sector"] or opportunity.sector
    opportunity.stack = enrichment["stack"] or opportunity.stack
    opportunity.required_skills = opportunity.required_skills or opportunity.stack
    opportunity.estimated_value = commercial_score.get("estimated_value") or opportunity.estimated_value
    opportunity.deadline_at = commercial_score.get("deadline_at") or opportunity.deadline_at
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
            "commercial_score": commercial_score,
            "quality": quality.to_dict(),
        },
        ensure_ascii=False,
    )

    existing = db.execute(
        "SELECT id, buyer_account_id, commercial_status, next_best_action FROM opportunities WHERE url = ?",
        (opportunity.url,),
    ).fetchone()

    next_best_action = _next_best_action_for_ingestion(existing, commercial_score)
    commercial_status = _commercial_status_for_quality(existing, quality)
    ignored_reason = _ignored_reason_for_quality(quality)

    if existing:
        db.execute(
            """
            UPDATE opportunities
            SET external_id = ?,
                source_key = ?,
                source_type = ?,
                source_label = ?,
                title = ?,
                company = ?,
                buyer_name = ?,
                buyer_domain = ?,
                country = ?,
                apply_url = ?,
                budget_min = ?,
                budget_max = ?,
                estimated_value = ?,
                budget_text = ?,
                currency = ?,
                sector = ?,
                stack = ?,
                required_skills = ?,
                pain_signals = ?,
                contact_signals = ?,
                evidence_snippets = ?,
                posted_at = ?,
                deadline_at = ?,
                risk_level = ?,
                risk_reasons = ?,
                score = ?,
                score_total = ?,
                score_money = ?,
                score_fit = ?,
                score_urgency = ?,
                score_contactability = ?,
                score_confidence = ?,
                score_tier = ?,
                score_reasons = ?,
                next_best_action = ?,
                ai_summary = ?,
                suggested_reply = ?,
                analysis_json = ?,
                commercial_status = ?,
                ignored_reason = ?,
                is_suspicious = ?,
                raw_text = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                opportunity.external_id,
                opportunity.source_key,
                opportunity.source_type,
                opportunity.source_label,
                opportunity.title,
                opportunity.company,
                opportunity.buyer_name,
                opportunity.buyer_domain,
                opportunity.country,
                opportunity.apply_url,
                opportunity.budget_min,
                opportunity.budget_max,
                opportunity.estimated_value,
                opportunity.budget_text,
                opportunity.currency,
                opportunity.sector,
                json.dumps(opportunity.stack, ensure_ascii=False),
                json.dumps(opportunity.required_skills, ensure_ascii=False),
                json.dumps(opportunity.pain_signals, ensure_ascii=False),
                json.dumps(opportunity.contact_signals, ensure_ascii=False),
                json.dumps(opportunity.evidence_snippets, ensure_ascii=False),
                opportunity.posted_at,
                opportunity.deadline_at,
                opportunity.risk_level,
                json.dumps(opportunity.risk_reasons, ensure_ascii=False),
                opportunity.score,
                commercial_score["score_total"],
                commercial_score["score_money"],
                commercial_score["score_fit"],
                commercial_score["score_urgency"],
                commercial_score["score_contactability"],
                commercial_score["score_confidence"],
                commercial_score["score_tier"],
                json.dumps(commercial_score["score_reasons"], ensure_ascii=False),
                next_best_action,
                opportunity.ai_summary,
                opportunity.suggested_reply,
                analysis_json,
                commercial_status,
                ignored_reason,
                int(opportunity.is_suspicious),
                opportunity.raw_text,
                existing["id"],
            ),
        )
        if should_assign_buyer_account(quality):
            assign_buyer_account(db, int(existing["id"]))
        elif existing["buyer_account_id"]:
            account_id = int(existing["buyer_account_id"])
            db.execute(
                "UPDATE opportunities SET buyer_account_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing["id"],),
            )
            recalculate_buyer_account(db, account_id)
        _maybe_generate_outreach_drafts(
            db,
            opportunity_id=int(existing["id"]),
            source_key=opportunity.source_key,
            score_tier=commercial_score["score_tier"],
        )
        return 0, 1

    initial_state = _initial_state_for_quality(quality, opportunity)
    insert_query = """
    INSERT INTO opportunities (
        external_id,
        source_key,
        source_type,
        source_label,
        title,
        company,
        buyer_name,
        buyer_domain,
        country,
        url,
        apply_url,
        budget_min,
        budget_max,
        estimated_value,
        budget_text,
        currency,
        sector,
        stack,
        required_skills,
        pain_signals,
        contact_signals,
        evidence_snippets,
        posted_at,
        deadline_at,
        risk_level,
        risk_reasons,
        score,
        score_total,
        score_money,
        score_fit,
        score_urgency,
        score_contactability,
        score_confidence,
        score_tier,
        score_reasons,
        next_best_action,
        ai_summary,
        suggested_reply,
        analysis_json,
        state,
        commercial_status,
        ignored_reason,
        is_suspicious,
        raw_text
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    if db.engine == "postgres":
        insert_query += " RETURNING id"

    cursor = db.execute(
        insert_query,
        (
            opportunity.external_id,
            opportunity.source_key,
            opportunity.source_type,
            opportunity.source_label,
            opportunity.title,
            opportunity.company,
            opportunity.buyer_name,
            opportunity.buyer_domain,
            opportunity.country,
            opportunity.url,
            opportunity.apply_url,
            opportunity.budget_min,
            opportunity.budget_max,
            opportunity.estimated_value,
            opportunity.budget_text,
            opportunity.currency,
            opportunity.sector,
            json.dumps(opportunity.stack, ensure_ascii=False),
            json.dumps(opportunity.required_skills, ensure_ascii=False),
            json.dumps(opportunity.pain_signals, ensure_ascii=False),
            json.dumps(opportunity.contact_signals, ensure_ascii=False),
            json.dumps(opportunity.evidence_snippets, ensure_ascii=False),
            opportunity.posted_at,
            opportunity.deadline_at,
            opportunity.risk_level,
            json.dumps(opportunity.risk_reasons, ensure_ascii=False),
            opportunity.score,
            commercial_score["score_total"],
            commercial_score["score_money"],
            commercial_score["score_fit"],
            commercial_score["score_urgency"],
            commercial_score["score_contactability"],
            commercial_score["score_confidence"],
            commercial_score["score_tier"],
            json.dumps(commercial_score["score_reasons"], ensure_ascii=False),
            next_best_action,
            opportunity.ai_summary,
            opportunity.suggested_reply,
            analysis_json,
            initial_state,
            commercial_status,
            ignored_reason,
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
    if should_assign_buyer_account(quality):
        assign_buyer_account(db, int(opportunity_id))
    _maybe_generate_outreach_drafts(
        db,
        opportunity_id=int(opportunity_id),
        source_key=opportunity.source_key,
        score_tier=commercial_score["score_tier"],
    )
    return 1, 0


def _maybe_generate_outreach_drafts(db, *, opportunity_id: int, source_key: str, score_tier: str) -> None:
    if source_key not in {
        "hn_algolia",
        "greenhouse_jobs",
        "lever_postings",
        "ashby_jobs",
        "workable_jobs",
        "ted_eu",
        "uk_find_tender",
        "uk_contracts_finder",
        "worldbank_procurement",
    }:
        return
    if str(score_tier or "").upper() not in {"A1", "A2"}:
        return
    try:
        from .outreach import persist_outreach_drafts

        persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=False)
    except Exception:
        return


def _next_best_action_for_ingestion(existing, commercial_score: dict) -> str:
    tier = str(commercial_score.get("score_tier") or "").strip().upper()
    initial_action = commercial_score.get("next_best_action") or ""
    if tier == "A1":
        initial_action = "Contactar hoy"
    elif tier == "A2":
        initial_action = "Revisar esta semana"

    if not existing:
        return initial_action

    commercial_status = str(existing["commercial_status"] or "new").strip().lower()
    if commercial_status in {"contacted", "followup_due", "replied", "discovery", "proposal", "won", "lost", "ignored", "snoozed"}:
        return str(existing["next_best_action"] or "").strip() or initial_action
    return initial_action


def _initial_state_for_quality(quality: QualityAssessment, opportunity: NormalizedOpportunity) -> str:
    if quality.quality_stage == "SUSPECT" or opportunity.is_suspicious:
        return "SOSPECHOSO"
    if quality.quality_stage in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}:
        return "DESCARTADO"
    if quality.quality_stage in {"WATCHLIST", "REVIEW_REQUIRED"}:
        return "VISTO"
    return "NUEVO"


def _commercial_status_for_quality(existing, quality: QualityAssessment) -> str:
    if existing:
        current = str(existing["commercial_status"] or "new").strip().lower()
        if current in {"contacted", "followup_due", "replied", "discovery", "proposal", "won", "lost", "snoozed"}:
            return current
    if quality.quality_stage in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}:
        return "ignored"
    if quality.quality_stage in {"WATCHLIST", "REVIEW_REQUIRED", "SUSPECT"}:
        return "reviewed"
    return "new"


def _ignored_reason_for_quality(quality: QualityAssessment) -> str:
    if quality.quality_stage == "DUPLICATE":
        return "duplicate"
    if quality.quality_stage == "LOW_VALUE":
        return "low_value"
    if quality.quality_stage == "REJECTED_NOISE":
        return "spammy"
    return ""


def _normalized_opportunity_from_payload(item: dict, policy: dict) -> NormalizedOpportunity | None:
    url = str(item.get("url", "") or "").strip()
    if not url:
        return None

    stack = _coerce_list(item.get("stack"))
    risk_reasons = _coerce_list(item.get("risk_reasons"))
    company = str(item.get("company", "") or "").strip()
    title = str(item.get("title", "") or "").strip() or "Sin titulo"
    body_parts = (
        title,
        company,
        str(item.get("raw_text", "") or "").strip(),
        str(item.get("location", "") or "").strip(),
    )
    raw_text = "\n".join(part for part in body_parts if part)
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
        source_type=str(item.get("source_type", "") or _source_type_for_policy(policy["source_key"])).strip(),
        buyer_name=str(item.get("buyer_name", "") or company).strip(),
        buyer_domain=str(item.get("buyer_domain", "") or _domain_from_url(url)).strip(),
        country=str(item.get("country", "") or "").strip(),
        apply_url=str(item.get("apply_url", "") or url).strip(),
        budget_min=_safe_int_or_none(item.get("budget_min")),
        budget_max=_safe_int_or_none(item.get("budget_max")),
        estimated_value=_safe_int_or_none(item.get("estimated_value")),
        budget_text=str(item.get("budget_text", "") or "").strip(),
        currency=str(item.get("currency", "") or "USD").strip() or "USD",
        sector=str(item.get("sector", "") or "").strip(),
        stack=stack,
        required_skills=_coerce_list(item.get("required_skills")) or stack,
        pain_signals=_coerce_list(item.get("pain_signals")),
        contact_signals=_coerce_list(item.get("contact_signals")),
        evidence_snippets=_coerce_list(item.get("evidence_snippets")),
        posted_at=str(item.get("posted_at", "") or "").strip() or None,
        deadline_at=str(item.get("deadline_at", "") or "").strip() or None,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        is_suspicious=bool(item.get("is_suspicious")),
    )


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


def _domain_from_url(value: str) -> str:
    from urllib.parse import urlparse

    return urlparse(str(value or "")).netloc.lower().replace("www.", "").strip()


def _source_type_for_policy(source_key: str) -> str:
    mapping = {
        "sample_feed": "direct_rfp",
        "reddit": "community",
        "workana_projects": "direct_rfp",
        "greenhouse": "hiring_signal",
        "lever": "hiring_signal",
        "weworkremotely": "hiring_signal",
        "hackernews_jobs": "hiring_signal",
        "hn_algolia": "community_signal",
        "greenhouse_jobs": "hiring_signal",
        "lever_postings": "hiring_signal",
        "ashby_jobs": "hiring_signal",
        "workable_jobs": "hiring_signal",
        "ted_eu": "procurement",
        "uk_find_tender": "procurement",
        "uk_contracts_finder": "procurement",
        "worldbank_procurement": "procurement",
        "github_issues": "github_issue",
        "sam_gov": "procurement",
        "public_pages": "direct_rfp",
        "email_alerts": "direct_rfp",
    }
    return mapping.get(source_key, "direct_rfp")


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
