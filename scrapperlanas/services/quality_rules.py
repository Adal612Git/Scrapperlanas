from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_rule_config(config: dict[str, Any] | None) -> str:
    """Stable JSON representation used for version hashes."""
    return json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rule_config_hash(config: dict[str, Any] | None) -> str:
    return hashlib.sha256(canonical_rule_config(config).encode("utf-8")).hexdigest()


def ensure_current_rule_version(
    db,
    *,
    source_key: str,
    config: dict[str, Any] | None,
    actor_user_id: int | None = None,
):
    existing = db.execute(
        "SELECT id FROM quality_rule_versions WHERE source_key = ? LIMIT 1",
        (source_key,),
    ).fetchone()
    if existing is not None:
        return None
    return record_quality_rule_version(
        db,
        source_key=source_key,
        config=config or {},
        actor_user_id=actor_user_id,
        reason="Snapshot inicial",
        change_summary="Estado inicial antes de versionar reglas.",
        is_active=True,
    )


def list_quality_rule_versions(db, *, source_key: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, source_key, version_number, actor_user_id, config_hash, config_json,
               reason, change_summary, is_active, created_at
        FROM quality_rule_versions
        WHERE source_key = ?
        ORDER BY version_number DESC
        LIMIT ?
        """,
        (source_key, limit),
    ).fetchall()
    versions: list[dict[str, Any]] = []
    for row in rows:
        config = _loads_config(row["config_json"])
        versions.append(
            {
                "id": row["id"],
                "source_key": row["source_key"],
                "version_number": row["version_number"],
                "actor_user_id": row["actor_user_id"],
                "config_hash": row["config_hash"],
                "hash_short": str(row["config_hash"] or "")[:10],
                "config": config,
                "reason": row["reason"],
                "change_summary": row["change_summary"],
                "is_active": bool(row["is_active"]),
                "created_at": row["created_at"],
            }
        )
    return versions


def record_quality_rule_version(
    db,
    *,
    source_key: str,
    config: dict[str, Any],
    actor_user_id: int | None = None,
    reason: str = "",
    change_summary: str = "",
    is_active: bool = True,
) -> dict[str, Any]:
    version_number = _next_version_number(db, source_key)
    config_json = json.dumps(config or {}, ensure_ascii=False, indent=2, sort_keys=True)
    config_hash = rule_config_hash(config)

    if is_active:
        db.execute(
            "UPDATE quality_rule_versions SET is_active = 0 WHERE source_key = ?",
            (source_key,),
        )

    db.execute(
        """
        INSERT INTO quality_rule_versions (
            source_key, version_number, actor_user_id, config_hash, config_json,
            reason, change_summary, is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_key,
            version_number,
            actor_user_id,
            config_hash,
            config_json,
            reason.strip(),
            change_summary.strip(),
            1 if is_active else 0,
        ),
    )
    db.commit()
    row = db.execute(
        """
        SELECT id, source_key, version_number, actor_user_id, config_hash, config_json,
               reason, change_summary, is_active, created_at
        FROM quality_rule_versions
        WHERE source_key = ? AND version_number = ?
        """,
        (source_key, version_number),
    ).fetchone()
    return _serialize_version(row)


def restore_quality_rule_version(
    db,
    *,
    version_id: int,
    actor_user_id: int | None = None,
    reason: str = "",
) -> dict[str, Any]:
    row = db.execute(
        """
        SELECT id, source_key, version_number, config_json
        FROM quality_rule_versions
        WHERE id = ?
        """,
        (version_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"Quality rule version {version_id} does not exist.")

    config = _loads_config(row["config_json"])
    db.execute(
        "UPDATE source_policies SET config_json = ? WHERE source_key = ?",
        (json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True), row["source_key"]),
    )
    return record_quality_rule_version(
        db,
        source_key=row["source_key"],
        config=config,
        actor_user_id=actor_user_id,
        reason=reason.strip() or f"Rollback a v{row['version_number']}",
        change_summary=f"Restaurada desde version {row['version_number']}.",
        is_active=True,
    )


def _next_version_number(db, source_key: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(version_number), 0) AS last_version FROM quality_rule_versions WHERE source_key = ?",
        (source_key,),
    ).fetchone()
    return int(row["last_version"] or 0) + 1


def _loads_config(raw: str | None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_version(row) -> dict[str, Any]:
    config = _loads_config(row["config_json"])
    return {
        "id": row["id"],
        "source_key": row["source_key"],
        "version_number": row["version_number"],
        "actor_user_id": row["actor_user_id"],
        "config_hash": row["config_hash"],
        "hash_short": str(row["config_hash"] or "")[:10],
        "config": config,
        "reason": row["reason"],
        "change_summary": row["change_summary"],
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
    }
