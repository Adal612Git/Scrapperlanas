from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Mapping
from urllib.parse import urlparse


COMMON_COMPANY_SUFFIXES = (
    "s a de c v",
    "sa de cv",
    "s de rl de cv",
    "sapi de cv",
    "incorporated",
    "corporation",
    "company",
    "limited",
    "inc",
    "llc",
    "ltd",
    "gmbh",
    "corp",
    "co",
)

CLOSED_STATUSES = {"won", "lost", "ignored"}


def normalize_domain(value) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "@" in text and "://" not in text:
        text = text.rsplit("@", 1)[-1]
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.strip().lower().rstrip(".")
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_buyer_name(value) -> str:
    text = _ascii_fold(str(value or "").strip().lower())
    if not text:
        return ""
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in COMMON_COMPANY_SUFFIXES:
        text = re.sub(rf"(?:^|\s){re.escape(suffix)}$", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def find_or_create_buyer_account(db, opportunity: Mapping[str, Any]) -> dict | None:
    identity = _identity_from_opportunity(opportunity)
    if not identity["normalized_domain"] and not identity["normalized_name"]:
        return None

    account = _find_existing_account(db, identity)
    if account is not None:
        _enrich_account_identity(db, account["id"], identity)
        return dict(db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account["id"],)).fetchone())

    account_id = _create_buyer_account(db, identity)
    return dict(db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account_id,)).fetchone())


def assign_buyer_account(db, opportunity_id: int) -> dict | None:
    opportunity = db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if opportunity is None:
        raise LookupError(f"Opportunity {opportunity_id} not found.")

    existing_account_id = opportunity["buyer_account_id"] if "buyer_account_id" in opportunity.keys() else None
    if existing_account_id:
        recalculate_buyer_account(db, int(existing_account_id))
        return dict(db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (existing_account_id,)).fetchone())

    account = find_or_create_buyer_account(db, dict(opportunity))
    if account is None:
        return None

    db.execute(
        """
        UPDATE opportunities
        SET buyer_account_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (account["id"], opportunity_id),
    )
    recalculate_buyer_account(db, int(account["id"]))
    return dict(db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account["id"],)).fetchone())


def recalculate_buyer_account(db, account_id: int) -> dict:
    account = db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account_id,)).fetchone()
    if account is None:
        raise LookupError(f"Buyer account {account_id} not found.")

    rows = db.execute(
        """
        SELECT *
        FROM opportunities
        WHERE buyer_account_id = ?
        ORDER BY COALESCE(posted_at, created_at) DESC, id DESC
        """,
        (account_id,),
    ).fetchall()
    opportunities = [dict(row) for row in rows]
    metrics = _account_metrics(opportunities)
    if opportunities:
        identity = _best_identity(account, opportunities)
        db.execute(
            """
            UPDATE buyer_accounts
            SET name = ?,
                normalized_name = ?,
                domain = ?,
                normalized_domain = ?,
                country = ?,
                website = ?,
                source_first_seen = ?,
                first_seen_at = ?,
                last_seen_at = ?,
                opportunity_count = ?,
                a1_count = ?,
                a2_count = ?,
                contacted_count = ?,
                replied_count = ?,
                proposal_count = ?,
                won_count = ?,
                lost_count = ?,
                ignored_count = ?,
                total_estimated_value = ?,
                total_proposal_value = ?,
                total_won_value = ?,
                account_score = ?,
                account_tier = ?,
                primary_pain_signals_json = ?,
                required_skills_json = ?,
                contact_signals_json = ?,
                evidence_summary_json = ?,
                last_contacted_at = ?,
                next_followup_at = ?,
                commercial_status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                identity["name"],
                identity["normalized_name"],
                identity["domain"],
                identity["normalized_domain"],
                identity["country"],
                identity["website"],
                identity["source_first_seen"],
                metrics["first_seen_at"],
                metrics["last_seen_at"],
                metrics["opportunity_count"],
                metrics["a1_count"],
                metrics["a2_count"],
                metrics["contacted_count"],
                metrics["replied_count"],
                metrics["proposal_count"],
                metrics["won_count"],
                metrics["lost_count"],
                metrics["ignored_count"],
                metrics["total_estimated_value"],
                metrics["total_proposal_value"],
                metrics["total_won_value"],
                metrics["account_score"],
                metrics["account_tier"],
                json.dumps(metrics["primary_pain_signals"], ensure_ascii=False),
                json.dumps(metrics["required_skills"], ensure_ascii=False),
                json.dumps(metrics["contact_signals"], ensure_ascii=False),
                json.dumps(metrics["evidence_summary"], ensure_ascii=False),
                metrics["last_contacted_at"],
                metrics["next_followup_at"],
                metrics["commercial_status"],
                account_id,
            ),
        )
    else:
        db.execute(
            """
            UPDATE buyer_accounts
            SET opportunity_count = 0,
                a1_count = 0,
                a2_count = 0,
                contacted_count = 0,
                replied_count = 0,
                proposal_count = 0,
                won_count = 0,
                lost_count = 0,
                ignored_count = 0,
                total_estimated_value = 0,
                total_proposal_value = 0,
                total_won_value = 0,
                account_score = 0,
                account_tier = 'cold',
                commercial_status = 'new',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (account_id,),
        )
    return dict(db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account_id,)).fetchone())


def assign_missing_buyer_accounts(db, *, limit: int | None = None) -> int:
    query = "SELECT id FROM opportunities WHERE buyer_account_id IS NULL ORDER BY id ASC"
    params: list[Any] = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = db.execute(query, tuple(params)).fetchall()
    assigned = 0
    for row in rows:
        if assign_buyer_account(db, int(row["id"])) is not None:
            assigned += 1
    db.commit()
    return assigned


def refresh_buyer_accounts(db) -> None:
    assign_missing_buyer_accounts(db)
    account_rows = db.execute("SELECT id FROM buyer_accounts ORDER BY id ASC").fetchall()
    for row in account_rows:
        recalculate_buyer_account(db, int(row["id"]))
    detect_possible_duplicates(db)
    db.commit()


def detect_possible_duplicates(db) -> list[dict]:
    accounts = [dict(row) for row in db.execute("SELECT * FROM buyer_accounts ORDER BY id ASC").fetchall()]
    candidates: list[dict] = []
    for index, left in enumerate(accounts):
        for right in accounts[index + 1 :]:
            candidate = _duplicate_candidate(left, right)
            if candidate is None:
                continue
            account_a_id = min(int(left["id"]), int(right["id"]))
            account_b_id = max(int(left["id"]), int(right["id"]))
            existing = db.execute(
                """
                SELECT *
                FROM buyer_account_duplicate_candidates
                WHERE account_a_id = ? AND account_b_id = ?
                """,
                (account_a_id, account_b_id),
            ).fetchone()
            if existing and existing["status"] != "open":
                continue
            if existing:
                db.execute(
                    """
                    UPDATE buyer_account_duplicate_candidates
                    SET reason = ?,
                        confidence = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (candidate["reason"], candidate["confidence"], existing["id"]),
                )
                candidate_id = existing["id"]
            else:
                cursor = db.execute(
                    """
                    INSERT INTO buyer_account_duplicate_candidates (
                        account_a_id,
                        account_b_id,
                        reason,
                        confidence,
                        status
                    ) VALUES (?, ?, ?, ?, 'open')
                    """,
                    (account_a_id, account_b_id, candidate["reason"], candidate["confidence"]),
                )
                candidate_id = cursor.lastrowid if db.engine == "sqlite" else None
            candidates.append(
                {
                    "id": candidate_id,
                    "account_a_id": account_a_id,
                    "account_b_id": account_b_id,
                    "reason": candidate["reason"],
                    "confidence": candidate["confidence"],
                    "status": "open",
                }
            )
    db.commit()
    return list_duplicate_candidates(db)


def list_duplicate_candidates(db, *, status: str = "open") -> list[dict]:
    rows = db.execute(
        """
        SELECT
            c.*,
            a.name AS account_a_name,
            a.domain AS account_a_domain,
            a.account_score AS account_a_score,
            b.name AS account_b_name,
            b.domain AS account_b_domain,
            b.account_score AS account_b_score
        FROM buyer_account_duplicate_candidates c
        JOIN buyer_accounts a ON a.id = c.account_a_id
        JOIN buyer_accounts b ON b.id = c.account_b_id
        WHERE c.status = ?
        ORDER BY c.confidence DESC, c.updated_at DESC, c.id DESC
        """,
        (status,),
    ).fetchall()
    return [dict(row) for row in rows]


def merge_buyer_accounts(db, *, target_account_id: int, source_account_id: int, actor_user_id: int | None = None) -> dict:
    if target_account_id == source_account_id:
        raise ValueError("No se puede fusionar una cuenta consigo misma.")

    target = db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (target_account_id,)).fetchone()
    source = db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (source_account_id,)).fetchone()
    if target is None or source is None:
        raise LookupError("Cuenta origen o destino no existe.")

    db.execute(
        """
        UPDATE opportunities
        SET buyer_account_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE buyer_account_id = ?
        """,
        (target_account_id, source_account_id),
    )
    db.execute(
        """
        UPDATE buyer_account_duplicate_candidates
        SET status = 'merged',
            updated_at = CURRENT_TIMESTAMP
        WHERE (account_a_id = ? AND account_b_id = ?)
           OR (account_a_id = ? AND account_b_id = ?)
        """,
        (target_account_id, source_account_id, source_account_id, target_account_id),
    )
    db.execute(
        """
        INSERT INTO account_activities (
            buyer_account_id,
            actor_user_id,
            activity_type,
            related_account_id,
            notes,
            metadata_json
        ) VALUES (?, ?, 'account_merged', ?, ?, ?)
        """,
        (
            target_account_id,
            actor_user_id,
            source_account_id,
            f"Cuenta fusionada manualmente: {source['name']} -> {target['name']}.",
            json.dumps({"source_account_id": source_account_id, "target_account_id": target_account_id}, ensure_ascii=False),
        ),
    )
    db.execute("DELETE FROM buyer_accounts WHERE id = ?", (source_account_id,))
    updated = recalculate_buyer_account(db, target_account_id)
    detect_possible_duplicates(db)
    db.commit()
    return updated


def ignore_duplicate_candidate(db, *, candidate_id: int) -> None:
    row = db.execute(
        "SELECT id FROM buyer_account_duplicate_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Duplicate candidate {candidate_id} not found.")
    db.execute(
        """
        UPDATE buyer_account_duplicate_candidates
        SET status = 'ignored',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (candidate_id,),
    )
    db.commit()


def _identity_from_opportunity(opportunity: Mapping[str, Any]) -> dict:
    buyer_domain = normalize_domain(opportunity.get("buyer_domain"))
    contact_domain = _domain_from_contact_signals(_json_list(opportunity.get("contact_signals")))
    domain = buyer_domain or contact_domain
    buyer_name = _clean(opportunity.get("buyer_name")) or _clean(opportunity.get("company"))
    if not buyer_name and domain:
        buyer_name = domain
    normalized_name = normalize_buyer_name(buyer_name)
    source = _clean(opportunity.get("source_key") or opportunity.get("source_label"))
    country = _clean(opportunity.get("country"))
    website = f"https://{domain}" if domain else ""
    return {
        "name": buyer_name,
        "normalized_name": normalized_name,
        "domain": domain,
        "normalized_domain": domain,
        "country": country,
        "website": website,
        "source_first_seen": source,
    }


def _find_existing_account(db, identity: dict):
    if identity["normalized_domain"]:
        account = db.execute(
            "SELECT * FROM buyer_accounts WHERE normalized_domain = ?",
            (identity["normalized_domain"],),
        ).fetchone()
        if account is not None:
            return account

    if identity["normalized_name"]:
        account = db.execute(
            """
            SELECT *
            FROM buyer_accounts
            WHERE normalized_name = ?
              AND (normalized_domain = '' OR normalized_domain IS NULL)
            ORDER BY id ASC
            LIMIT 1
            """,
            (identity["normalized_name"],),
        ).fetchone()
        if account is not None:
            return account
    return None


def _create_buyer_account(db, identity: dict) -> int:
    insert_query = """
    INSERT INTO buyer_accounts (
        name,
        normalized_name,
        domain,
        normalized_domain,
        country,
        website,
        source_first_seen
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """
    if db.engine == "postgres":
        insert_query += " RETURNING id"
    cursor = db.execute(
        insert_query,
        (
            identity["name"] or identity["domain"] or "Cuenta sin nombre",
            identity["normalized_name"],
            identity["domain"],
            identity["normalized_domain"],
            identity["country"],
            identity["website"],
            identity["source_first_seen"],
        ),
    )
    if db.engine == "postgres":
        return int(cursor.fetchone()["id"])
    return int(cursor.lastrowid)


def _enrich_account_identity(db, account_id: int, identity: dict) -> None:
    account = db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account_id,)).fetchone()
    if account is None:
        return
    db.execute(
        """
        UPDATE buyer_accounts
        SET name = ?,
            normalized_name = ?,
            domain = ?,
            normalized_domain = ?,
            country = ?,
            website = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            account["name"] or identity["name"] or identity["domain"] or "Cuenta sin nombre",
            account["normalized_name"] or identity["normalized_name"],
            account["domain"] or identity["domain"],
            account["normalized_domain"] or identity["normalized_domain"],
            account["country"] or identity["country"],
            account["website"] or identity["website"],
            account_id,
        ),
    )


def _best_identity(account, opportunities: list[dict]) -> dict:
    identities = [_identity_from_opportunity(opportunity) for opportunity in opportunities]
    domain = _first_nonempty([identity["domain"] for identity in identities]) or account["domain"]
    normalized_domain = normalize_domain(domain) or account["normalized_domain"]
    name = _first_nonempty([identity["name"] for identity in identities]) or account["name"] or domain or "Cuenta sin nombre"
    normalized_name = normalize_buyer_name(name) or account["normalized_name"]
    country = _first_nonempty([identity["country"] for identity in identities]) or account["country"]
    source_first_seen = account["source_first_seen"] or _first_nonempty([identity["source_first_seen"] for identity in identities])
    return {
        "name": name,
        "normalized_name": normalized_name,
        "domain": domain,
        "normalized_domain": normalized_domain,
        "country": country,
        "website": f"https://{normalized_domain}" if normalized_domain else account["website"],
        "source_first_seen": source_first_seen,
    }


def _account_metrics(opportunities: list[dict]) -> dict:
    opportunity_count = len(opportunities)
    active = [opportunity for opportunity in opportunities if _status(opportunity) not in {"ignored", "lost"}]
    active_a1_count = sum(1 for opportunity in active if _tier(opportunity) == "A1")
    active_a2_count = sum(1 for opportunity in active if _tier(opportunity) == "A2")
    ignored_count = sum(1 for opportunity in opportunities if _status(opportunity) == "ignored")
    lost_count = sum(1 for opportunity in opportunities if _status(opportunity) == "lost")
    won_count = sum(1 for opportunity in opportunities if _status(opportunity) == "won")
    proposal_count = sum(1 for opportunity in opportunities if _status(opportunity) in {"proposal", "won"})
    replied_count = sum(1 for opportunity in opportunities if _status(opportunity) in {"replied", "discovery", "proposal", "won", "lost"})
    contacted_count = sum(
        1
        for opportunity in opportunities
        if _status(opportunity) in {"contacted", "followup_due", "replied", "discovery", "proposal", "won", "lost"}
        or opportunity.get("last_contacted_at")
    )
    scores = [int(opportunity.get("score_total") or opportunity.get("score") or 0) for opportunity in active]
    average_score = round(sum(scores) / len(scores)) if scores else 0
    total_estimated_value = sum(_estimated_value(opportunity) for opportunity in opportunities)
    total_proposal_value = sum(_int_value(opportunity.get("proposal_value")) for opportunity in opportunities)
    total_won_value = sum(_int_value(opportunity.get("won_value")) for opportunity in opportunities)
    account_score = _account_score(
        average_score=average_score,
        opportunity_count=opportunity_count,
        active_a1_count=active_a1_count,
        active_a2_count=active_a2_count,
        replied_count=replied_count,
        proposal_count=proposal_count,
        won_count=won_count,
        lost_count=lost_count,
        ignored_count=ignored_count,
        total_estimated_value=total_estimated_value,
        total_proposal_value=total_proposal_value,
        total_won_value=total_won_value,
        opportunities=opportunities,
    )
    account_tier = _account_tier(
        score=account_score,
        opportunity_count=opportunity_count,
        active_a1_count=active_a1_count,
        active_a2_count=active_a2_count,
        ignored_count=ignored_count,
    )
    return {
        "opportunity_count": opportunity_count,
        "a1_count": sum(1 for opportunity in opportunities if _tier(opportunity) == "A1"),
        "a2_count": sum(1 for opportunity in opportunities if _tier(opportunity) == "A2"),
        "contacted_count": contacted_count,
        "replied_count": replied_count,
        "proposal_count": proposal_count,
        "won_count": won_count,
        "lost_count": lost_count,
        "ignored_count": ignored_count,
        "total_estimated_value": total_estimated_value,
        "total_proposal_value": total_proposal_value,
        "total_won_value": total_won_value,
        "account_score": account_score,
        "account_tier": account_tier,
        "primary_pain_signals": _top_json_values(opportunities, "pain_signals", limit=6),
        "required_skills": _top_json_values(opportunities, "required_skills", limit=6),
        "contact_signals": _top_json_values(opportunities, "contact_signals", limit=5),
        "evidence_summary": _top_json_values(opportunities, "evidence_snippets", limit=5),
        "last_contacted_at": _max_text(opportunity.get("last_contacted_at") for opportunity in opportunities),
        "next_followup_at": _min_future_or_due(opportunity.get("next_followup_at") for opportunity in opportunities if _status(opportunity) not in CLOSED_STATUSES),
        "commercial_status": _account_status(opportunities),
        "first_seen_at": _min_text(opportunity.get("created_at") for opportunity in opportunities) or "",
        "last_seen_at": _max_text((opportunity.get("posted_at") or opportunity.get("created_at")) for opportunity in opportunities) or "",
    }


def _account_score(
    *,
    average_score: int,
    opportunity_count: int,
    active_a1_count: int,
    active_a2_count: int,
    replied_count: int,
    proposal_count: int,
    won_count: int,
    lost_count: int,
    ignored_count: int,
    total_estimated_value: int,
    total_proposal_value: int,
    total_won_value: int,
    opportunities: list[dict],
) -> int:
    score = average_score
    score += min(15, max(0, opportunity_count - 1) * 5)
    score += min(24, active_a1_count * 12)
    score += min(12, active_a2_count * 6)
    score += min(18, replied_count * 5 + proposal_count * 8 + won_count * 10)
    if total_won_value:
        score += 12
    elif total_proposal_value:
        score += 8
    elif total_estimated_value >= 10000:
        score += 5
    score -= ignored_count * 18
    score -= sum(_lost_penalty(opportunity) for opportunity in opportunities if _status(opportunity) == "lost")
    return max(0, min(100, int(round(score))))


def _account_tier(*, score: int, opportunity_count: int, active_a1_count: int, active_a2_count: int, ignored_count: int) -> str:
    if opportunity_count and ignored_count >= max(1, (opportunity_count + 1) // 2):
        return "noisy"
    if score >= 80 or active_a1_count >= 2:
        return "hot"
    if score >= 60 or (active_a1_count + active_a2_count) > 0:
        return "warm"
    if score >= 40:
        return "nurture"
    return "cold"


def _duplicate_candidate(left: dict, right: dict) -> dict | None:
    left_domain = normalize_domain(left.get("normalized_domain") or left.get("domain"))
    right_domain = normalize_domain(right.get("normalized_domain") or right.get("domain"))
    if left_domain and right_domain:
        return None
    left_name = normalize_buyer_name(left.get("normalized_name") or left.get("name"))
    right_name = normalize_buyer_name(right.get("normalized_name") or right.get("name"))
    if not left_name or not right_name:
        return None
    if left_name == right_name:
        return {"reason": "Nombre normalizado exacto sin dominio compartido.", "confidence": 90}
    if min(len(left_name), len(right_name)) < 5:
        return None
    similarity = SequenceMatcher(None, left_name, right_name).ratio()
    if similarity >= 0.86:
        return {
            "reason": f"Nombres similares sin dominio confiable ({left_name} / {right_name}).",
            "confidence": int(round(similarity * 100)),
        }
    return None


def _domain_from_contact_signals(signals: list[str]) -> str:
    for signal in signals:
        match = re.search(r"[\w.+-]+@([\w.-]+\.[a-z]{2,})", signal, flags=re.IGNORECASE)
        if match:
            return normalize_domain(match.group(1))
    return ""


def _json_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean(item) for item in value if _clean(item)]
    if isinstance(value, tuple):
        return [_clean(item) for item in value if _clean(item)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return _json_list(parsed)
    cleaned = _clean(value)
    return [cleaned] if cleaned else []


def _top_json_values(opportunities: list[dict], field: str, *, limit: int) -> list[str]:
    counter: Counter[str] = Counter()
    for opportunity in opportunities:
        for value in _json_list(opportunity.get(field)):
            counter[_clean(value)] += 1
    return [value for value, _count in counter.most_common(limit)]


def _status(opportunity: Mapping[str, Any]) -> str:
    return str(opportunity.get("commercial_status") or "new").strip().lower()


def _tier(opportunity: Mapping[str, Any]) -> str:
    return str(opportunity.get("score_tier") or "").strip().upper()


def _estimated_value(opportunity: Mapping[str, Any]) -> int:
    return (
        _int_value(opportunity.get("estimated_value"))
        or _int_value(opportunity.get("budget_max"))
        or _int_value(opportunity.get("budget_min"))
    )


def _lost_penalty(opportunity: Mapping[str, Any]) -> int:
    reason = str(opportunity.get("lost_reason") or "").strip()
    if reason == "bad_fit":
        return 14
    if reason == "no_response":
        return 5
    if reason == "price":
        return 3
    return 8


def _account_status(opportunities: list[dict]) -> str:
    statuses = [_status(opportunity) for opportunity in opportunities]
    for status in ("proposal", "discovery", "replied", "followup_due", "contacted"):
        if status in statuses:
            return status
    if statuses and all(status == "won" for status in statuses):
        return "won"
    if statuses and all(status == "ignored" for status in statuses):
        return "ignored"
    return "new"


def _int_value(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean(value) -> str:
    return " ".join(str(value or "").split()).strip()


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _first_nonempty(values) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _min_text(values) -> str | None:
    cleaned = [str(value) for value in values if value]
    return min(cleaned) if cleaned else None


def _max_text(values) -> str | None:
    cleaned = [str(value) for value in values if value]
    return max(cleaned) if cleaned else None


def _min_future_or_due(values) -> str | None:
    cleaned = [str(value) for value in values if value]
    return min(cleaned) if cleaned else None
