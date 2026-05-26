from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal, Mapping

from .quality import BudgetQuality, BuyerIdentity, QualityAssessment


QualityDecision = Literal[
    "GOOD_LEAD",
    "BAD_LEAD",
    "SCAM",
    "DUPLICATE",
    "NOT_COMMERCIAL",
    "LOW_VALUE",
    "WRONG_BUDGET",
    "WRONG_BUYER",
    "WRONG_SKILLS",
    "NEEDS_REVIEW",
]

VALID_DECISIONS: set[str] = {
    "GOOD_LEAD",
    "BAD_LEAD",
    "SCAM",
    "DUPLICATE",
    "NOT_COMMERCIAL",
    "LOW_VALUE",
    "WRONG_BUDGET",
    "WRONG_BUYER",
    "WRONG_SKILLS",
    "NEEDS_REVIEW",
}

NEGATIVE_DECISIONS = {
    "BAD_LEAD",
    "SCAM",
    "DUPLICATE",
    "NOT_COMMERCIAL",
    "LOW_VALUE",
    "WRONG_BUDGET",
    "WRONG_BUYER",
    "WRONG_SKILLS",
}


@dataclass(frozen=True)
class QualityFeedback:
    opportunity_id: str
    decision: QualityDecision
    reason: str | None = None
    corrected_stage: str | None = None
    corrected_grade: str | None = None
    corrected_budget: dict[str, Any] | None = None
    corrected_buyer: dict[str, Any] | None = None
    created_at: datetime | str | None = None
    id: int | None = None
    actor_user_id: int | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.created_at, datetime):
            data["created_at"] = self.created_at.astimezone(UTC).replace(microsecond=0).isoformat()
        return data


def record_feedback(
    db,
    *,
    opportunity_id: int | str,
    decision: str,
    actor_user_id: int | None = None,
    reason: str | None = None,
    corrected_stage: str | None = None,
    corrected_grade: str | None = None,
    corrected_budget: Mapping[str, Any] | None = None,
    corrected_buyer: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> QualityFeedback:
    normalized_decision = _normalize_decision(decision)
    db.execute(
        """
        INSERT INTO quality_feedback (
            opportunity_id,
            actor_user_id,
            decision,
            reason,
            corrected_stage,
            corrected_grade,
            corrected_budget_json,
            corrected_buyer_json,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(opportunity_id),
            actor_user_id,
            normalized_decision,
            _clean(reason),
            _clean(corrected_stage),
            _clean(corrected_grade).upper(),
            json.dumps(dict(corrected_budget or {}), ensure_ascii=False),
            json.dumps(dict(corrected_buyer or {}), ensure_ascii=False),
            json.dumps(dict(metadata or {}), ensure_ascii=False),
        ),
    )
    db.commit()
    feedback = get_feedback_for_opportunity(db, opportunity_id, limit=1)
    return feedback[0]


def get_feedback_for_opportunity(db, opportunity_id: int | str, *, limit: int = 20) -> list[QualityFeedback]:
    rows = db.execute(
        """
        SELECT *
        FROM quality_feedback
        WHERE opportunity_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (int(opportunity_id), int(limit)),
    ).fetchall()
    return [_feedback_from_row(row) for row in rows]


def get_latest_feedback(db, opportunity_id: int | str) -> QualityFeedback | None:
    rows = get_feedback_for_opportunity(db, opportunity_id, limit=1)
    return rows[0] if rows else None


def get_feedback_stats(db, *, limit: int = 500) -> dict[str, Any]:
    rows = db.execute(
        """
        SELECT f.*, o.source_key, o.source_label, o.buyer_name, o.company, o.buyer_domain, o.title, o.url
        FROM quality_feedback f
        JOIN opportunities o ON o.id = f.opportunity_id
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()

    decision_counts: dict[str, int] = {}
    source_counts: dict[str, dict[str, Any]] = {}
    author_counts: dict[str, dict[str, Any]] = {}
    false_positives: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        decision = str(row_dict.get("decision") or "").upper()
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

        source_key = str(row_dict.get("source_key") or row_dict.get("source_label") or "unknown").strip() or "unknown"
        source = source_counts.setdefault(source_key, {"source": source_key, "total": 0, "negative": 0, "scam": 0})
        source["total"] += 1
        if decision in NEGATIVE_DECISIONS:
            source["negative"] += 1
        if decision == "SCAM":
            source["scam"] += 1

        author = _author_key(row_dict)
        if author:
            author_bucket = author_counts.setdefault(author, {"author": author, "total": 0, "negative": 0, "scam": 0})
            author_bucket["total"] += 1
            if decision in NEGATIVE_DECISIONS:
                author_bucket["negative"] += 1
            if decision == "SCAM":
                author_bucket["scam"] += 1

        if decision in {"BAD_LEAD", "SCAM", "NOT_COMMERCIAL", "LOW_VALUE", "WRONG_BUDGET", "WRONG_BUYER"}:
            false_positives.append(
                {
                    "opportunity_id": row_dict.get("opportunity_id"),
                    "title": row_dict.get("title"),
                    "source": row_dict.get("source_label") or row_dict.get("source_key"),
                    "decision": decision,
                    "reason": row_dict.get("reason") or "",
                    "created_at": row_dict.get("created_at") or "",
                }
            )

    source_penalties = {
        source: _trust_penalty(bucket["negative"], bucket["scam"], bucket["total"])
        for source, bucket in source_counts.items()
    }
    author_penalties = {
        author: _trust_penalty(bucket["negative"], bucket["scam"], bucket["total"])
        for author, bucket in author_counts.items()
    }
    return {
        "total_feedback": len(rows),
        "decision_counts": decision_counts,
        "source_feedback": sorted(source_counts.values(), key=lambda item: (-item["negative"], item["source"])),
        "author_feedback": sorted(author_counts.values(), key=lambda item: (-item["negative"], item["author"])),
        "source_trust_penalties": source_penalties,
        "author_trust_penalties": author_penalties,
        "latest_false_positives": false_positives[:10],
    }


def apply_feedback_overrides(
    opportunity: Mapping[str, Any] | Any,
    quality: QualityAssessment,
    feedback: QualityFeedback | Mapping[str, Any] | None,
    *,
    stats: Mapping[str, Any] | None = None,
) -> QualityAssessment:
    adjusted = _apply_trust_learning(opportunity, quality, stats)
    if feedback is None:
        return adjusted

    decision = _feedback_value(feedback, "decision").upper()
    if decision not in VALID_DECISIONS:
        return adjusted

    corrected_stage = _feedback_value(feedback, "corrected_stage").upper()
    corrected_grade = _feedback_value(feedback, "corrected_grade").upper()
    feedback_reason = f"human_feedback_{decision.lower()}"
    reasons = _unique([*adjusted.rejection_reasons, feedback_reason])
    risk_reasons = list(adjusted.risk_reasons)
    trust_reasons = _unique([*adjusted.trust_reasons, "Feedback humano aplicado."])

    if decision == "GOOD_LEAD":
        target_stage = corrected_stage or _good_lead_stage(adjusted)
        target_grade = corrected_grade if corrected_grade in {"A1", "A2", "B", "C", "D"} else _good_lead_grade(adjusted)
        return replace(
            adjusted,
            quality_stage=target_stage,
            grade=target_grade,
            quality_score=max(adjusted.quality_score, 76 if target_stage == "READY_TO_CONTACT" else 62),
            final_score_cap=max(adjusted.final_score_cap, 82 if target_stage == "READY_TO_CONTACT" else 70),
            is_contactable=target_stage == "READY_TO_CONTACT",
            rejection_reasons=_unique([reason for reason in reasons if reason not in {"buyer_identity_weak", "budget_no_currency_budget_signal"}]),
            trust_reasons=trust_reasons,
            buyer_confidence=max(adjusted.buyer_confidence, 50),
        )

    if decision == "SCAM":
        return replace(
            adjusted,
            risk_score=max(adjusted.risk_score, 86),
            risk_level="CRITICAL",
            risk_reasons=_unique([*risk_reasons, "human_marked_scam"])[:8],
            quality_stage="SUSPECT",
            grade="D",
            quality_score=min(adjusted.quality_score, 10),
            final_score_cap=min(adjusted.final_score_cap, 10),
            is_contactable=False,
            rejection_reasons=reasons,
            trust_reasons=trust_reasons,
        )

    if decision == "DUPLICATE":
        return replace(
            adjusted,
            quality_stage="DUPLICATE",
            grade="REJECTED",
            quality_score=min(adjusted.quality_score, 10),
            final_score_cap=min(adjusted.final_score_cap, 10),
            is_contactable=False,
            rejection_reasons=reasons,
            trust_reasons=trust_reasons,
        )

    if decision == "WRONG_BUDGET":
        corrected_budget = _feedback_json(feedback, "corrected_budget")
        budget = _corrected_budget(corrected_budget) if corrected_budget else replace(
            adjusted.budget,
            is_valid_commercial_budget=False,
            confidence=min(adjusted.budget.confidence, 35),
            rejection_reason="human_marked_wrong_budget",
        )
        return replace(
            adjusted,
            budget=budget,
            budget_confidence=budget.confidence,
            quality_stage=corrected_stage or "REVIEW_REQUIRED",
            grade=corrected_grade if corrected_grade in {"A1", "A2", "B", "C", "D"} else min_grade(adjusted.grade, "B"),
            is_contactable=False,
            final_score_cap=min(adjusted.final_score_cap, 64),
            rejection_reasons=reasons,
            trust_reasons=trust_reasons,
        )

    if decision == "WRONG_BUYER":
        corrected_buyer = _feedback_json(feedback, "corrected_buyer")
        buyer = _corrected_buyer(corrected_buyer) if corrected_buyer else replace(
            adjusted.buyer_identity,
            confidence=min(adjusted.buyer_identity.confidence, 25),
        )
        return replace(
            adjusted,
            buyer_identity=buyer,
            buyer_confidence=buyer.confidence,
            quality_stage=corrected_stage or "REVIEW_REQUIRED",
            grade=corrected_grade if corrected_grade in {"A1", "A2", "B", "C", "D"} else min_grade(adjusted.grade, "C"),
            is_contactable=False,
            final_score_cap=min(adjusted.final_score_cap, 45),
            rejection_reasons=_unique([*reasons, "buyer_identity_weak"]),
            trust_reasons=trust_reasons,
        )

    if decision == "NEEDS_REVIEW":
        return replace(
            adjusted,
            quality_stage=corrected_stage or "REVIEW_REQUIRED",
            grade=corrected_grade if corrected_grade in {"A1", "A2", "B", "C", "D"} else min_grade(adjusted.grade, "B"),
            is_contactable=False,
            final_score_cap=min(adjusted.final_score_cap, 72),
            rejection_reasons=reasons,
            trust_reasons=trust_reasons,
        )

    low_stage = "LOW_VALUE"
    if decision == "NOT_COMMERCIAL":
        low_stage = "REJECTED_NOISE"
    return replace(
        adjusted,
        quality_stage=corrected_stage or low_stage,
        grade=corrected_grade if corrected_grade in {"C", "D", "REJECTED"} else "D",
        quality_score=min(adjusted.quality_score, 35),
        final_score_cap=min(adjusted.final_score_cap, 35),
        is_contactable=False,
        rejection_reasons=reasons,
        trust_reasons=trust_reasons,
    )


def min_grade(left: str, right: str) -> str:
    rank = {"A1": 5, "A2": 4, "B": 3, "C": 2, "D": 1, "REJECTED": 0}
    left_key = str(left or "D").upper()
    right_key = str(right or "D").upper()
    return left_key if rank.get(left_key, 0) <= rank.get(right_key, 0) else right_key


def _apply_trust_learning(
    opportunity: Mapping[str, Any] | Any,
    quality: QualityAssessment,
    stats: Mapping[str, Any] | None,
) -> QualityAssessment:
    if not stats:
        return quality
    source_key = _value(opportunity, "source_key") or _value(opportunity, "source_label")
    author = _author_key(
        {
            "buyer_name": _value(opportunity, "buyer_name"),
            "company": _value(opportunity, "company"),
            "buyer_domain": _value(opportunity, "buyer_domain"),
        }
    )
    source_penalty = int((stats.get("source_trust_penalties") or {}).get(source_key, 0) or 0)
    author_penalty = int((stats.get("author_trust_penalties") or {}).get(author, 0) or 0)
    penalty = max(source_penalty, author_penalty)
    if penalty <= 0:
        return quality
    cap = max(10, quality.final_score_cap - penalty)
    score = max(0, min(cap, quality.quality_score - penalty))
    stage = quality.quality_stage
    reasons = list(quality.rejection_reasons)
    if penalty >= 25 and stage == "READY_TO_CONTACT":
        stage = "REVIEW_REQUIRED"
        reasons.append("trust_penalty_from_feedback")
    return replace(
        quality,
        source_trust_score=max(0, quality.source_trust_score - source_penalty),
        author_trust_score=max(0, quality.author_trust_score - author_penalty),
        quality_score=score,
        final_score_cap=cap,
        quality_stage=stage,
        is_contactable=stage == "READY_TO_CONTACT",
        rejection_reasons=_unique(reasons),
        trust_reasons=_unique([*quality.trust_reasons, "Trust ajustado por feedback historico."]),
    )


def _trust_penalty(negative: int, scam: int, total: int) -> int:
    if scam >= 2:
        return 70
    if negative >= 3:
        return 35
    if total >= 3 and negative / max(1, total) >= 0.7:
        return 30
    if negative >= 2:
        return 18
    return 0


def _good_lead_stage(quality: QualityAssessment) -> str:
    if quality.risk_level in {"HIGH", "CRITICAL"}:
        return "REVIEW_REQUIRED"
    if quality.buyer_confidence >= 50 and (
        quality.budget.is_valid_commercial_budget
        or quality.commercial_intent_label in {"DIRECT_HIRING", "PROCUREMENT", "RFP", "SERVICE_REQUEST"}
    ):
        return "READY_TO_CONTACT"
    return "WATCHLIST"


def _good_lead_grade(quality: QualityAssessment) -> str:
    if quality.risk_level in {"HIGH", "CRITICAL"}:
        return "B"
    if quality.buyer_confidence >= 70 and quality.budget.is_valid_commercial_budget:
        return "A2"
    return "B"


def _corrected_budget(value: Mapping[str, Any]) -> BudgetQuality:
    valid_value = value.get("is_valid_commercial_budget")
    if valid_value is None:
        valid_value = value.get("isValidCommercialBudget")
    return BudgetQuality(
        amount_min=_optional_int(value.get("amount_min") or value.get("amountMin")),
        amount_max=_optional_int(value.get("amount_max") or value.get("amountMax")),
        currency=_clean(value.get("currency")) or "USD",
        unit=_clean(value.get("unit")) or "unknown",
        confidence=max(0, min(100, _optional_int(value.get("confidence")) or 85)),
        raw_evidence=_clean(value.get("raw_evidence") or value.get("rawEvidence")),
        is_estimated=bool(value.get("is_estimated") or value.get("isEstimated")),
        is_valid_commercial_budget=True if valid_value is None else bool(valid_value),
    )


def _corrected_buyer(value: Mapping[str, Any]) -> BuyerIdentity:
    return BuyerIdentity(
        name=_clean(value.get("name")),
        organization=_clean(value.get("organization")),
        domain=_clean(value.get("domain")),
        contact_method=_clean(value.get("contact_method") or value.get("contactMethod")),
        confidence=max(0, min(100, _optional_int(value.get("confidence")) or 85)),
        type=_clean(value.get("type")) or "unknown",
    )


def _feedback_from_row(row) -> QualityFeedback:
    data = dict(row)
    return QualityFeedback(
        id=data.get("id"),
        opportunity_id=str(data.get("opportunity_id") or ""),
        actor_user_id=data.get("actor_user_id"),
        decision=_normalize_decision(data.get("decision")),
        reason=data.get("reason") or None,
        corrected_stage=data.get("corrected_stage") or None,
        corrected_grade=data.get("corrected_grade") or None,
        corrected_budget=_load_json(data.get("corrected_budget_json"), default={}),
        corrected_buyer=_load_json(data.get("corrected_buyer_json"), default={}),
        metadata=_load_json(data.get("metadata_json"), default={}),
        created_at=data.get("created_at"),
    )


def _normalize_decision(value: Any) -> QualityDecision:
    decision = str(value or "").strip().upper()
    if decision not in VALID_DECISIONS:
        raise ValueError("Decision de feedback no permitida.")
    return decision  # type: ignore[return-value]


def _feedback_value(feedback: QualityFeedback | Mapping[str, Any], key: str) -> str:
    if isinstance(feedback, QualityFeedback):
        return _clean(getattr(feedback, key, ""))
    return _clean(feedback.get(key))


def _feedback_json(feedback: QualityFeedback | Mapping[str, Any], key: str) -> dict[str, Any]:
    if isinstance(feedback, QualityFeedback):
        value = getattr(feedback, key, None)
    else:
        value = feedback.get(key)
    return value if isinstance(value, dict) else {}


def _author_key(row: Mapping[str, Any]) -> str:
    for key in ("buyer_domain", "buyer_name", "company"):
        value = _clean(row.get(key)).lower()
        if value and value not in {"reddit.com", "old.reddit.com", "www.reddit.com"}:
            return value
    return ""


def _value(obj: Any, key: str) -> str:
    if isinstance(obj, Mapping):
        return _clean(obj.get(key))
    return _clean(getattr(obj, key, ""))


def _optional_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _load_json(raw_value: Any, *, default):
    if isinstance(raw_value, (dict, list)):
        return raw_value
    try:
        return json.loads(raw_value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value or "").strip()))
