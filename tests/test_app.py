from __future__ import annotations

import re
from pathlib import Path

import pytest

from scrapperlanas import auth as auth_module
from scrapperlanas import create_app
from scrapperlanas.db import get_db


@pytest.fixture()
def app(tmp_path: Path):
    database_path = tmp_path / "test.sqlite3"
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(database_path),
            "SECRET_KEY": "test-secret",
            "CRON_SECRET": "cron-test-secret",
            "OLLAMA_ENABLED": False,
            "CSRF_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
            "AUTOMATION_MODE": "n8n",
            "LOTO_SIGNAL_BASE_URL": "http://web:8000",
            "N8N_PUBLIC_URL": "http://localhost:5678",
            "N8N_IMPORT_WEBHOOK_PATH": "loto-signal/import",
            "N8N_SCHEDULER_INTERVAL_MINUTES": 15,
            "AUTH_RATE_LIMIT_MAX_ATTEMPTS": 0,
        }
    )

    with app.app_context():
        db = get_db()
        db.execute("UPDATE source_policies SET enabled = 0")
        db.execute("UPDATE source_policies SET enabled = 1 WHERE source_key = 'sample_feed'")
        db.commit()

    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def get_csrf_token(client, path: str) -> str:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode("utf-8")


def register(client, email: str = "operator@example.com", password: str = "supersecret"):
    return client.post(
        "/auth/register",
        data={
            "email": email,
            "password": password,
            "confirm_password": password,
            "csrf_token": get_csrf_token(client, "/auth/register"),
        },
        follow_redirects=True,
    )


def login(client, email: str = "operator@example.com", password: str = "supersecret"):
    return client.post(
        "/auth/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": get_csrf_token(client, "/auth/login"),
        },
        follow_redirects=True,
    )


def test_register_login_logout(client):
    response = register(client)
    assert response.status_code == 200
    assert b"Inbox inteligente" in response.data

    response = client.post(
        "/auth/logout",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Iniciar sesion" in response.data

    response = login(client)
    assert response.status_code == 200
    assert b"Inbox inteligente" in response.data


def test_logout_requires_post(client):
    response = client.get("/auth/logout")
    assert response.status_code == 405


def test_ingestion_creates_demo_opportunities(app, client):
    register(client)
    response = client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ingesta completada" in response.data
    assert b"Python scraper para directorios B2B con export CSV" in response.data

    with app.app_context():
        db = get_db()
        total = db.execute("SELECT COUNT(*) AS total FROM opportunities").fetchone()["total"]
        suspicious = db.execute(
            "SELECT COUNT(*) AS total FROM opportunities WHERE state = 'SOSPECHOSO'"
        ).fetchone()["total"]
        assert total == 4
        assert suspicious == 1


def test_state_change_creates_audit_event(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    with app.app_context():
        db = get_db()
        opportunity_id = db.execute(
            "SELECT id FROM opportunities ORDER BY score DESC LIMIT 1"
        ).fetchone()["id"]

    response = client.post(
        f"/opportunities/{opportunity_id}",
        data={
            "state": "APLICADO",
            "note": "Propuesta enviada al cliente.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Estado actualizado" in response.data

    with app.app_context():
        db = get_db()
        opportunity = db.execute(
            "SELECT state FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        event = db.execute(
            """
            SELECT event_type, previous_state, new_state, note
            FROM opportunity_events
            WHERE opportunity_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()

        assert opportunity["state"] == "APLICADO"
        assert event["event_type"] == "STATE_CHANGED"
        assert event["previous_state"] == "NUEVO"
        assert event["new_state"] == "APLICADO"
        assert event["note"] == "Propuesta enviada al cliente."


def test_commercial_contact_and_followup_flow(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    with app.app_context():
        db = get_db()
        opportunity_id = db.execute(
            "SELECT id FROM opportunities ORDER BY score_total DESC, score DESC LIMIT 1"
        ).fetchone()["id"]

    response = client.post(
        f"/opportunities/{opportunity_id}/contacted",
        data={
            "contact_channel": "email",
            "contact_value": "buyer@example.com",
            "notes": "Mensaje inicial enviado.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Movimiento comercial guardado" in response.data

    with app.app_context():
        db = get_db()
        row = db.execute(
            """
            SELECT commercial_status, followup_count, last_contacted_at, next_followup_at,
                   contact_channel, contact_value, next_best_action
            FROM opportunities
            WHERE id = ?
            """,
            (opportunity_id,),
        ).fetchone()
        activity = db.execute(
            """
            SELECT activity_type, from_status, to_status, channel, notes
            FROM commercial_activities
            WHERE opportunity_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()

        assert row["commercial_status"] == "contacted"
        assert row["followup_count"] == 0
        assert row["last_contacted_at"]
        assert row["next_followup_at"]
        assert row["contact_channel"] == "email"
        assert row["contact_value"] == "buyer@example.com"
        assert row["next_best_action"] == "Dar seguimiento en 3 dias habiles"
        assert activity["activity_type"] == "contacted"
        assert activity["from_status"] == "new"
        assert activity["to_status"] == "contacted"
        assert activity["channel"] == "email"
        assert activity["notes"] == "Mensaje inicial enviado."

    response = client.post(
        f"/opportunities/{opportunity_id}/followup-completed",
        data={
            "notes": "Follow-up 1 enviado.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT commercial_status, followup_count, next_followup_at, next_best_action FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        assert row["commercial_status"] == "contacted"
        assert row["followup_count"] == 1
        assert row["next_followup_at"]
        assert row["next_best_action"] == "Enviar follow-up 2"


def test_commercial_proposal_won_and_source_metrics(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    with app.app_context():
        db = get_db()
        opportunity_id = db.execute(
            "SELECT id FROM opportunities ORDER BY score_total DESC, score DESC LIMIT 1"
        ).fetchone()["id"]

    client.post(
        f"/opportunities/{opportunity_id}/contacted",
        data={"csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}")},
        follow_redirects=True,
    )
    client.post(
        f"/opportunities/{opportunity_id}/replied",
        data={"notes": "Pidio propuesta.", "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}")},
        follow_redirects=True,
    )
    client.post(
        f"/opportunities/{opportunity_id}/proposal",
        data={
            "proposal_value": "7500",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )
    response = client.post(
        f"/opportunities/{opportunity_id}/won",
        data={
            "won_value": "6500",
            "notes": "Cierre ganado.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Ganada" in response.data

    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT commercial_status, proposal_value, won_value, next_followup_at FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        activities = db.execute(
            "SELECT activity_type FROM commercial_activities WHERE opportunity_id = ? ORDER BY id",
            (opportunity_id,),
        ).fetchall()

        assert row["commercial_status"] == "won"
        assert row["proposal_value"] == 7500
        assert row["won_value"] == 6500
        assert row["next_followup_at"] is None
        assert [activity["activity_type"] for activity in activities][-4:] == [
            "contacted",
            "replied",
            "proposal_sent",
            "won",
        ]

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert b"Copiloto comercial" in dashboard.data
    assert b"Calidad por fuente" in dashboard.data


def test_api_intelligence_summary_uses_quality_visibility(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    response = client.get("/api/intelligence/summary")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["summary"]["summary"]


def test_quality_command_center_and_audit_export(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    response = client.get("/quality")
    assert response.status_code == 200
    assert b"Quality Command Center" in response.data
    assert b"Calidad por fuente" in response.data

    audit = client.get("/exports/quality-audit.csv")
    assert audit.status_code == 200
    text = audit.data.decode("utf-8")
    assert "qualityStage" in text
    assert "explanationHeadline" in text
    assert "sourceTrustScore" in text


def test_quality_feedback_route_recomputes_opportunity(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )
    with app.app_context():
        db = get_db()
        opportunity_id = db.execute(
            "SELECT id FROM opportunities ORDER BY score_total DESC, score DESC LIMIT 1"
        ).fetchone()["id"]

    response = client.post(
        f"/opportunities/{opportunity_id}/quality-feedback",
        data={
            "decision": "BAD_LEAD",
            "reason": "Test false positive.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Feedback guardado" in response.data
    with app.app_context():
        db = get_db()
        feedback = db.execute(
            "SELECT decision, reason FROM quality_feedback WHERE opportunity_id = ? ORDER BY id DESC LIMIT 1",
            (opportunity_id,),
        ).fetchone()
        row = db.execute("SELECT analysis_json, state FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()

    assert feedback["decision"] == "BAD_LEAD"
    assert feedback["reason"] == "Test false positive."
    assert "human_feedback_bad_lead" in row["analysis_json"]


def test_recompute_quality_cli(app):
    runner = app.test_cli_runner()
    with app.app_context():
        db = get_db()
        db.execute(
            """
            INSERT INTO opportunities (source_key, source_label, source_type, title, company, buyer_name, buyer_domain, url, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "reddit",
                "Reddit Public JSON",
                "community",
                "Could you please rate my proposal?",
                "Upwork",
                "Upwork",
                "reddit.com",
                "https://reddit.com/r/Upwork/comments/cli",
                "Review my proposal please.",
            ),
        )
        db.commit()

    result = runner.invoke(args=["recompute-quality", "--limit", "10"])

    assert result.exit_code == 0
    assert "Processed:" in result.output


def test_outreach_generate_regenerate_and_copy_log(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    with app.app_context():
        db = get_db()
        opportunity_id = db.execute(
            "SELECT id FROM opportunities ORDER BY score_total DESC, score DESC LIMIT 1"
        ).fetchone()["id"]

    response = client.post(
        f"/opportunities/{opportunity_id}/outreach/generate",
        data={"csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Mensajes comerciales listos" in response.data
    assert b"Mensajes listos" in response.data
    assert b"Copiar" in response.data

    with app.app_context():
        db = get_db()
        draft = db.execute(
            """
            SELECT id, body
            FROM outreach_drafts
            WHERE opportunity_id = ? AND draft_type = 'initial' AND is_active = 1
            LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()
        active_before = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND is_active = 1",
            (opportunity_id,),
        ).fetchone()["total"]
        assert draft is not None
        assert draft["body"]

    response = client.post(
        f"/opportunities/{opportunity_id}/outreach/{draft['id']}/copy-log",
        data={"csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}")},
        follow_redirects=True,
    )
    assert response.status_code == 200

    response = client.post(
        f"/opportunities/{opportunity_id}/outreach/regenerate",
        data={"csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Mensajes regenerados" in response.data

    with app.app_context():
        db = get_db()
        activity = db.execute(
            """
            SELECT activity_type, message_snapshot
            FROM commercial_activities
            WHERE opportunity_id = ? AND activity_type = 'message_copied'
            ORDER BY id DESC
            LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()
        active_after = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND is_active = 1",
            (opportunity_id,),
        ).fetchone()["total"]
        inactive_after = db.execute(
            "SELECT COUNT(*) AS total FROM outreach_drafts WHERE opportunity_id = ? AND is_active = 0",
            (opportunity_id,),
        ).fetchone()["total"]

        assert activity["activity_type"] == "message_copied"
        assert activity["message_snapshot"]
        assert active_after == active_before
        assert inactive_after == active_before


def test_accounts_pages_render_after_ingestion(app, client):
    register(client)
    client.post(
        "/ingest/run",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )

    accounts_response = client.get("/accounts")
    assert accounts_response.status_code == 200
    assert b"Cuentas cliente" in accounts_response.data

    with app.app_context():
        db = get_db()
        account_id = db.execute(
            "SELECT id FROM buyer_accounts ORDER BY account_score DESC, id ASC LIMIT 1"
        ).fetchone()["id"]

    detail_response = client.get(f"/accounts/{account_id}")
    assert detail_response.status_code == 200
    assert b"Resumen de cuenta" in detail_response.data
    assert b"Oportunidades relacionadas" in detail_response.data

    duplicates_response = client.get("/accounts/duplicates")
    assert duplicates_response.status_code == 200
    assert b"Posibles duplicados" in duplicates_response.data


def test_missing_csrf_rejected(client):
    response = client.post(
        "/auth/login",
        data={"email": "operator@example.com", "password": "supersecret"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_login_rate_limit_blocks_repeated_failures(app, client):
    auth_module._AUTH_ATTEMPTS.clear()
    app.config["AUTH_RATE_LIMIT_MAX_ATTEMPTS"] = 2
    app.config["AUTH_RATE_LIMIT_WINDOW_SECONDS"] = 3600
    csrf_token = get_csrf_token(client, "/auth/login")

    for _index in range(2):
        response = client.post(
            "/auth/login",
            data={
                "email": "unknown@example.com",
                "password": "bad-password",
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 200

    blocked = client.post(
        "/auth/login",
        data={
            "email": "unknown@example.com",
            "password": "bad-password",
            "csrf_token": csrf_token,
        },
    )
    assert blocked.status_code == 429


def test_security_headers_present(app, client):
    app.config["HSTS_ENABLED"] = True
    response = client.get("/auth/login", base_url="https://localhost")

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def test_production_rejects_weak_runtime_config(tmp_path: Path):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(
            {
                "ENVIRONMENT": "production",
                "DEBUG": False,
                "SECRET_KEY": "short",
                "CRON_SECRET": "also-short",
                "DATABASE": str(tmp_path / "prod.sqlite3"),
                "SESSION_COOKIE_SECURE": True,
            }
        )


def test_internal_cron_requires_secret(client):
    response = client.get("/internal/cron/ingest")
    assert response.status_code == 401


def test_internal_cron_runs_ingestion(monkeypatch, client):
    def fake_run_ingestion_for_policies(db, profile=None, source_keys=None, due_only=False, trigger="manual"):
        assert profile is None
        assert source_keys is None
        assert due_only is True
        assert trigger == "internal_api"
        return {
            "policies_run": 1,
            "fetched": 4,
            "created": 2,
            "updated": 1,
            "skipped": 0,
            "errors": [],
        }

    monkeypatch.setattr("scrapperlanas.views.run_ingestion_for_policies", fake_run_ingestion_for_policies)

    response = client.get(
        "/internal/cron/ingest",
        headers={"Authorization": "Bearer cron-test-secret"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["mode"] == "due_only"
    assert payload["stats"]["created"] == 2


def test_internal_health_reports_runtime(client):
    response = client.get(
        "/internal/health",
        headers={"Authorization": "Bearer cron-test-secret"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["db_ok"] is True
    assert payload["runtime"]["worker_enabled"] is False
    assert payload["stability"]["window"] == "24h"


def test_due_policies_endpoint_lists_due_sources(client):
    response = client.get(
        "/internal/automation/policies/due",
        headers={"Authorization": "Bearer cron-test-secret"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["policies"]
    assert payload["policies"][0]["source_key"] == "sample_feed"
    assert payload["policies"][0]["is_due"] is True


def test_single_policy_cron_route_runs_specific_source(monkeypatch, client):
    def fake_run_ingestion_for_policies(db, profile=None, source_keys=None, due_only=False, trigger="manual"):
        assert profile is None
        assert source_keys == ("sample_feed",)
        assert due_only is True
        assert trigger == "internal_api"
        return {
            "policies_run": 1,
            "fetched": 4,
            "created": 1,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        }

    monkeypatch.setattr("scrapperlanas.views.run_ingestion_for_policies", fake_run_ingestion_for_policies)

    response = client.post(
        "/internal/cron/ingest/policy/sample_feed",
        headers={"Authorization": "Bearer cron-test-secret"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["stats"]["created"] == 1


def test_internal_import_endpoint_creates_opportunity(app, client):
    response = client.post(
        "/internal/import/opportunities",
        headers={"Authorization": "Bearer cron-test-secret"},
        json={
            "source_key": "email_alerts",
            "source_label": "Alertas por Correo",
            "items": [
                {
                    "external_id": "mail-001",
                    "title": "Python automation contract",
                    "company": "Inbox Leads",
                    "url": "https://example.com/jobs/python-automation-contract",
                    "raw_text": "Remote contract. Need Python, n8n and API integrations. Budget USD 2400.",
                    "posted_at": "2026-04-01T12:00:00Z",
                    "budget_min": 2400,
                    "currency": "USD",
                    "stack": ["Python", "n8n", "API"],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["stats"]["created"] == 1

    with app.app_context():
        db = get_db()
        row = db.execute(
            """
            SELECT source_key, source_label, source_type, title, buyer_name, score_total, score_tier, next_best_action
            FROM opportunities
            WHERE url = ?
            """,
            ("https://example.com/jobs/python-automation-contract",),
        ).fetchone()
        run_row = db.execute(
            """
            SELECT trigger_kind, source_key, status, fetched, created
            FROM automation_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        assert row["source_key"] == "email_alerts"
        assert row["source_label"] == "Alertas por Correo"
        assert row["source_type"] == "direct_rfp"
        assert row["buyer_name"] == "Inbox Leads"
        assert row["title"] == "Python automation contract"
        assert row["score_total"] > 0
        assert row["score_tier"] in {"A1", "A2", "B", "C", "D"}
        assert row["next_best_action"]
        assert run_row["trigger_kind"] == "internal_import"
        assert run_row["source_key"] == "email_alerts"
        assert run_row["status"] == "ok"
        assert run_row["fetched"] == 1
        assert run_row["created"] == 1


def test_settings_page_shows_n8n_bootstrap_details(client):
    register(client)

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"n8n empaquetado" in response.data
    assert b"Loto Signal Scheduler" in response.data
    assert b"Loto Signal Import Webhook" in response.data
    assert b"http://localhost:5678/webhook/loto-signal/import" in response.data
