from __future__ import annotations

import re
from pathlib import Path

import pytest

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
            "SCRAPPERLANAS_BASE_URL": "http://web:8000",
            "N8N_PUBLIC_URL": "http://localhost:5678",
            "N8N_IMPORT_WEBHOOK_PATH": "scrapperlanas/import",
            "N8N_SCHEDULER_INTERVAL_MINUTES": 15,
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
    assert b"Dashboard (Inbox)" in response.data

    response = client.post(
        "/auth/logout",
        data={"csrf_token": get_csrf_token(client, "/dashboard")},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Iniciar sesion" in response.data

    response = login(client)
    assert response.status_code == 200
    assert b"Dashboard (Inbox)" in response.data


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


def test_detail_comment_persists_and_renders(app, client):
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
            "action_type": "note",
            "note": "Revisar presupuesto y enviar respuesta hoy.",
            "csrf_token": get_csrf_token(client, f"/opportunities/{opportunity_id}"),
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Comentario actualizado" in response.data

    with app.app_context():
        db = get_db()
        opportunity = db.execute(
            "SELECT operational_note FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        event = db.execute(
            """
            SELECT event_type, note
            FROM opportunity_events
            WHERE opportunity_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (opportunity_id,),
        ).fetchone()

    assert opportunity["operational_note"] == "Revisar presupuesto y enviar respuesta hoy."
    assert event["event_type"] == "NOTE_UPDATED"
    assert event["note"] == "Revisar presupuesto y enviar respuesta hoy."


def test_missing_csrf_rejected(client):
    response = client.post(
        "/auth/login",
        data={"email": "operator@example.com", "password": "supersecret"},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_security_headers_present(client):
    response = client.get("/auth/login")

    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["Cache-Control"] == "no-store"


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
            "SELECT source_key, source_label, title FROM opportunities WHERE url = ?",
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
        assert row["title"] == "Python automation contract"
        assert run_row["trigger_kind"] == "internal_import"
        assert run_row["source_key"] == "email_alerts"
        assert run_row["status"] == "ok"
        assert run_row["fetched"] == 1
        assert run_row["created"] == 1


def test_internal_import_endpoint_normalizes_email_alert_payload(app, client):
    response = client.post(
        "/internal/import/opportunities",
        headers={"Authorization": "Bearer cron-test-secret"},
        json={
            "source_key": "email_alerts",
            "items": [
                {
                    "subject": "Automation engineer contract",
                    "from": {"text": "Acme Talent <alerts@acme.example>"},
                    "date": "2026-04-09T12:00:00.000Z",
                    "textPlain": "Remote contract. Need Python, n8n and APIs. Budget USD 2500. https://example.com/jobs/automation-engineer",
                    "messageId": "mail-raw-001",
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
            "SELECT source_key, source_label, title, company, url FROM opportunities WHERE external_id = ?",
            ("mail-raw-001",),
        ).fetchone()

    assert row["source_key"] == "email_alerts"
    assert row["source_label"] == "Alertas por Correo"
    assert row["title"] == "Automation engineer contract"
    assert row["company"] == "Acme Talent"
    assert row["url"] == "https://example.com/jobs/automation-engineer"


def test_internal_import_endpoint_extracts_budget_from_email_text(app, client):
    response = client.post(
        "/internal/import/opportunities",
        headers={"Authorization": "Bearer cron-test-secret"},
        json={
            "source_key": "email_alerts",
            "items": [
                {
                    "subject": "Need scraping help",
                    "from": "Hiring Team <jobs@example.com>",
                    "text": "Remote contract. We need Python + n8n. Budget USD 3200. https://example.com/jobs/scraping-help",
                    "messageId": "mail-budget-001",
                }
            ],
        },
    )

    assert response.status_code == 200
    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT budget_min, budget_max, budget_text, currency FROM opportunities WHERE external_id = ?",
            ("mail-budget-001",),
        ).fetchone()

    assert row["budget_min"] == 3200
    assert row["budget_max"] == 3200
    assert row["budget_text"] == "USD 3,200"
    assert row["currency"] == "USD"


def test_internal_import_endpoint_rejects_large_payload(client):
    response = client.post(
        "/internal/import/opportunities",
        headers={"Authorization": "Bearer cron-test-secret"},
        json={
            "source_key": "email_alerts",
            "items": [
                {
                    "external_id": f"mail-{index}",
                    "title": f"Job {index}",
                    "company": "Inbox",
                    "url": f"https://example.com/jobs/{index}",
                    "raw_text": "Remote contract.",
                }
                for index in range(101)
            ],
        },
    )

    assert response.status_code == 413
    payload = response.get_json()
    assert payload["ok"] is False


def test_settings_page_shows_n8n_bootstrap_details(client):
    register(client)

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"Orquestador ya empaquetado" in response.data
    assert b"Scrapperlanas Scheduler" in response.data
    assert b"Scrapperlanas Import Webhook" in response.data
    assert b"http://localhost:5678/webhook/scrapperlanas/import" in response.data
