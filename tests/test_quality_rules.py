from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.quality_rules import (
    ensure_current_rule_version,
    list_quality_rule_versions,
    record_quality_rule_version,
    restore_quality_rule_version,
    rule_config_hash,
)


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite3"),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
            "AUTH_RATE_LIMIT_MAX_ATTEMPTS": 0,
        }
    )


def _reddit_config(db) -> dict:
    row = db.execute("SELECT config_json FROM source_policies WHERE source_key = 'reddit'").fetchone()
    return json.loads(row["config_json"])


def test_quality_rule_versions_capture_hash_and_active_state(app):
    with app.app_context():
        db = get_db()
        config = _reddit_config(db)

        initial = ensure_current_rule_version(db, source_key="reddit", config=config)
        assert initial["version_number"] == 1
        assert initial["is_active"] is True
        assert initial["config_hash"] == rule_config_hash(config)

        changed_config = dict(config)
        changed_config["reddit"] = {**changed_config["reddit"], "minCommercialIntentScore": 88}
        second = record_quality_rule_version(
            db,
            source_key="reddit",
            config=changed_config,
            reason="Tighten demo rules.",
            change_summary="Raise minimum intent.",
        )

        versions = list_quality_rule_versions(db, source_key="reddit")
        assert second["version_number"] == 2
        assert [version["version_number"] for version in versions] == [2, 1]
        assert versions[0]["is_active"] is True
        assert versions[1]["is_active"] is False
        assert versions[0]["config"]["reddit"]["minCommercialIntentScore"] == 88


def test_quality_rule_restore_creates_auditable_new_version(app):
    with app.app_context():
        db = get_db()
        config = _reddit_config(db)
        initial = ensure_current_rule_version(db, source_key="reddit", config=config)

        changed_config = dict(config)
        changed_config["reddit"] = {**changed_config["reddit"], "blocklist": ["nosleep", "conspiracy"]}
        record_quality_rule_version(
            db,
            source_key="reddit",
            config=changed_config,
            reason="Temporary strict blocklist.",
            change_summary="Demo hardening.",
        )
        db.execute(
            "UPDATE source_policies SET config_json = ? WHERE source_key = 'reddit'",
            (json.dumps(changed_config, ensure_ascii=False, indent=2),),
        )
        db.commit()

        restored = restore_quality_rule_version(
            db,
            version_id=initial["id"],
            reason="Restore baseline.",
        )

        assert restored["version_number"] == 3
        assert restored["is_active"] is True
        assert restored["config"] == config
        assert _reddit_config(db) == config

        versions = list_quality_rule_versions(db, source_key="reddit")
        assert [version["is_active"] for version in versions] == [True, False, False]
