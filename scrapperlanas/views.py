from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .automation import automation_lock, get_runtime_snapshot
from .auth import login_required
from .db import get_db
from .services.pipeline import (
    PIPELINE_STATES,
    get_policy_by_source_key,
    import_opportunities,
    list_due_policies,
    list_enabled_policies,
    policy_runtime_status,
    run_ingestion,
    run_ingestion_for_policies,
    update_opportunity_state,
)


bp = Blueprint("main", __name__)

KANBAN_COLUMNS = (
    {
        "state": "INTERESANTE",
        "label": "Interesante",
        "icon": "fa-solid fa-star",
        "panel_class": "border-blue-500/20",
        "header_class": "border-blue-500/20 bg-blue-500/10 text-blue-300",
        "count_class": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
    },
    {
        "state": "RESPONDIDO",
        "label": "Respondido",
        "icon": "fa-solid fa-reply",
        "panel_class": "border-teal-500/20",
        "header_class": "border-teal-500/20 bg-teal-500/10 text-teal-300",
        "count_class": "border border-teal-500/30 bg-teal-500/15 text-teal-200",
    },
    {
        "state": "APLICADO",
        "label": "Aplicado",
        "icon": "fa-solid fa-paper-plane",
        "panel_class": "border-fuchsia-500/20",
        "header_class": "border-fuchsia-500/20 bg-fuchsia-500/10 text-fuchsia-300",
        "count_class": "border border-fuchsia-500/30 bg-fuchsia-500/15 text-fuchsia-200",
    },
    {
        "state": "FOLLOW_UP",
        "label": "Follow-up",
        "icon": "fa-solid fa-comments",
        "panel_class": "border-amber-500/20",
        "header_class": "border-amber-500/20 bg-amber-500/10 text-amber-300",
        "count_class": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    },
    {
        "state": "GANADO",
        "label": "Ganado",
        "icon": "fa-solid fa-handshake",
        "panel_class": "border-emerald-500/20",
        "header_class": "border-emerald-500/20 bg-emerald-500/10 text-emerald-300",
        "count_class": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
    },
    {
        "state": "PERDIDO",
        "label": "Perdido",
        "icon": "fa-solid fa-folder-minus",
        "panel_class": "border-slate-700",
        "header_class": "border-slate-700 bg-slate-800/70 text-slate-300",
        "count_class": "border border-slate-600 bg-slate-800 text-slate-300",
    },
    {
        "state": "SOSPECHOSO",
        "label": "Sospechoso",
        "icon": "fa-solid fa-triangle-exclamation",
        "panel_class": "border-orange-500/20",
        "header_class": "border-orange-500/20 bg-orange-500/10 text-orange-300",
        "count_class": "border border-orange-500/30 bg-orange-500/15 text-orange-200",
    },
)

STATE_BADGE_STYLES = {
    "NUEVO": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
    "VISTO": "border border-sky-500/30 bg-sky-500/15 text-sky-200",
    "INTERESANTE": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
    "RESPONDIDO": "border border-teal-500/30 bg-teal-500/15 text-teal-200",
    "APLICADO": "border border-fuchsia-500/30 bg-fuchsia-500/15 text-fuchsia-200",
    "FOLLOW_UP": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    "GANADO": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
    "PERDIDO": "border border-slate-600 bg-slate-800 text-slate-300",
    "SOSPECHOSO": "border border-orange-500/30 bg-orange-500/15 text-orange-200",
    "DESCARTADO": "border border-rose-500/30 bg-rose-500/15 text-rose-200",
}

RISK_BADGE_STYLES = {
    "low": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
    "medium": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    "high": "border border-red-500/30 bg-red-500/15 text-red-200",
}

ACTION_COPY = {
    "apply_now": {
        "label": "Aplicar hoy",
        "summary": "Abrir la fuente, adaptar el pitch y mandar aplicacion hoy.",
    },
    "review_today": {
        "label": "Resolver hoy",
        "summary": "Leer requisitos completos y decidir hoy si entra al pipeline fuerte.",
    },
    "clarify_scope": {
        "label": "Validar alcance",
        "summary": "Confirmar remoto, stack y tipo de rol antes de invertir mas tiempo.",
    },
    "skip": {
        "label": "No gastar tiempo",
        "summary": "No conviene consumir energia aqui salvo que cambie el contexto.",
    },
}

FIT_COPY = {
    "strong_fit": "fit fuerte",
    "possible_fit": "fit posible",
    "weak_fit": "fit debil",
    "avoid": "evitar",
}

PRIORITY_STYLES = {
    "A1": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
    "A2": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
    "B1": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    "B2": "border border-slate-600 bg-slate-800 text-slate-300",
    "C": "border border-rose-500/30 bg-rose-500/15 text-rose-200",
}


@bp.route("/")
def index():
    if g.user is None:
        return redirect(url_for("auth.login"))
    return redirect(url_for("main.dashboard"))


@bp.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    profile = _get_profile(db, g.user["id"])
    automation_summary = _automation_summary(db)
    filters = _build_filters(profile)
    rows = _fetch_opportunities(db, filters, limit=120)
    items = _sort_items([_serialize_opportunity(row) for row in rows])
    all_rows = _fetch_opportunities(db, _blank_filters(), limit=None)
    all_items = _sort_items([_serialize_opportunity(row) for row in all_rows])

    metrics = {
        "total": db.execute("SELECT COUNT(*) AS total FROM opportunities").fetchone()["total"],
        "active": db.execute(
            """
            SELECT COUNT(*) AS total
            FROM opportunities
            WHERE state IN ('NUEVO', 'VISTO', 'INTERESANTE', 'RESPONDIDO', 'APLICADO', 'FOLLOW_UP')
            """
        ).fetchone()["total"],
        "suspicious": db.execute(
            "SELECT COUNT(*) AS total FROM opportunities WHERE is_suspicious = 1"
        ).fetchone()["total"],
        "high_score": db.execute(
            "SELECT COUNT(*) AS total FROM opportunities WHERE score >= 70"
        ).fetchone()["total"],
        "cashflow": db.execute(
            """
            SELECT COALESCE(SUM(COALESCE(budget_max, budget_min, 0)), 0) AS total
            FROM opportunities
            WHERE score >= 70 AND state <> 'DESCARTADO'
            """
        ).fetchone()["total"],
    }
    score_average = db.execute(
        "SELECT ROUND(COALESCE(AVG(score), 0), 1) AS total FROM opportunities"
    ).fetchone()["total"]

    source_summary = db.execute(
        """
        SELECT source_label, COUNT(*) AS total
        FROM opportunities
        GROUP BY source_label
        ORDER BY total DESC, source_label ASC
        """
    ).fetchall()
    state_summary = db.execute(
        """
        SELECT state, COUNT(*) AS total
        FROM opportunities
        GROUP BY state
        ORDER BY total DESC, state ASC
        """
    ).fetchall()
    trend_summary = list(
        reversed(
            db.execute(
                """
                SELECT substr(COALESCE(posted_at, created_at), 1, 10) AS day, COUNT(*) AS total
                FROM opportunities
                GROUP BY day
                ORDER BY day DESC
                LIMIT 7
                """
            ).fetchall()
        )
    )
    sector_summary = db.execute(
        """
        SELECT
            COALESCE(NULLIF(sector, ''), 'Sin sector') AS sector_label,
            COUNT(*) AS total,
            COALESCE(SUM(COALESCE(budget_max, budget_min, 0)), 0) AS budget_total
        FROM opportunities
        GROUP BY sector_label
        ORDER BY budget_total DESC, total DESC, sector_label ASC
        LIMIT 6
        """
    ).fetchall()
    saved_views = db.execute(
        """
        SELECT *
        FROM saved_views
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    available_sources = db.execute(
        "SELECT DISTINCT source_label FROM opportunities ORDER BY source_label ASC"
    ).fetchall()
    pipeline_board = {column["state"]: [] for column in KANBAN_COLUMNS}
    for item in all_items:
        if item["state"] in pipeline_board and len(pipeline_board[item["state"]]) < 6:
            pipeline_board[item["state"]].append(item)
    high_priority_ratio = round((metrics["high_score"] / metrics["total"]) * 100) if metrics["total"] else 0
    focus_items = _focus_items(items or all_items)
    daily_plan = _daily_plan(items or all_items)

    return render_template(
        "dashboard.html",
        items=items,
        all_items=all_items,
        filters=filters,
        metrics=metrics,
        score_average=score_average,
        high_priority_ratio=high_priority_ratio,
        source_summary=source_summary,
        sector_summary=sector_summary,
        state_summary=state_summary,
        trend_summary=trend_summary,
        saved_views=saved_views,
        kanban_columns=KANBAN_COLUMNS,
        pipeline_board=pipeline_board,
        available_sources=[row["source_label"] for row in available_sources],
        active_query_string=_clean_query_string(filters),
        pipeline_states=PIPELINE_STATES,
        focus_items=focus_items,
        daily_plan=daily_plan,
        automation_summary=automation_summary,
    )


@bp.route("/ingest/run", methods=("POST",))
@login_required
def ingest_now():
    db = get_db()
    with automation_lock(current_app):
        stats = run_ingestion_for_policies(
            db,
            profile=_get_profile(db, g.user["id"]),
            trigger="manual_ui",
        )
    breakdown_parts = []
    for detail in stats.get("policy_details", []):
        fragment = (
            f"{detail['display_name']}: "
            f"{detail['fetched']} capt., "
            f"{detail['created']} nuevas, "
            f"{detail['updated']} act."
        )
        if detail.get("error"):
            fragment += f" error={detail['error']}"
        breakdown_parts.append(fragment)

    message = (
        "Ingesta completada. "
        f"Fuentes: {stats['policies_run']}, "
        f"capturadas: {stats['fetched']}, "
        f"nuevas: {stats['created']}, "
        f"actualizadas: {stats['updated']}."
    )
    if breakdown_parts:
        message += " " + " | ".join(breakdown_parts[:6])
    flash(
        message,
        "success",
    )
    for error in stats["errors"]:
        flash(error, "error")
    return redirect(url_for("main.dashboard"))


@bp.route("/internal/cron/ingest", methods=("GET",))
def ingest_from_cron():
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    db = get_db()
    force = _truthy_arg(request.args.get("force"))
    with automation_lock(current_app):
        stats = run_ingestion_for_policies(
            db,
            profile=None,
            due_only=not force,
            trigger="internal_api",
        )
    return jsonify({"ok": True, "mode": "force" if force else "due_only", "stats": stats})


@bp.route("/internal/automation/policies", methods=("GET",))
def internal_policies():
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    db = get_db()
    policies = [_serialize_policy_runtime_status(policy) for policy in list_enabled_policies(db)]
    return jsonify({"ok": True, "policies": policies})


@bp.route("/internal/automation/policies/due", methods=("GET",))
def internal_due_policies():
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    db = get_db()
    policies = [_serialize_policy_runtime_status(policy) for policy in list_due_policies(db)]
    return jsonify({"ok": True, "policies": policies})


@bp.route("/internal/health", methods=("GET",))
def internal_health():
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    db = get_db()
    automation_summary = _automation_summary(db)
    db_health = db.execute("SELECT 1 AS ok").fetchone()["ok"] == 1
    runtime = get_runtime_snapshot(current_app)
    recent_metrics = _recent_automation_metrics(db)
    recent_runs = _recent_automation_runs(db, limit=8)
    worker_required = str(current_app.config.get("AUTOMATION_MODE", "")).strip().lower() == "local"
    worker_ok = (not worker_required) or (runtime["worker_enabled"] and runtime["worker_alive"])
    healthy = db_health and worker_ok and (runtime["last_error"] is None)

    return jsonify(
        {
            "ok": healthy,
            "environment": current_app.config.get("ENVIRONMENT"),
            "automation_mode": current_app.config.get("AUTOMATION_MODE"),
            "db_ok": db_health,
            "worker_ok": worker_ok,
            "runtime": runtime,
            "automation_summary": automation_summary,
            "stability": recent_metrics,
            "recent_runs": recent_runs,
        }
    )


@bp.route("/internal/cron/ingest/policy/<string:source_key>", methods=("POST",))
def ingest_single_policy_from_cron(source_key: str):
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    db = get_db()
    policy = get_policy_by_source_key(db, source_key)
    if policy is None:
        return jsonify({"ok": False, "error": f"Unknown source policy: {source_key}"}), 404
    if not policy["enabled"]:
        return jsonify({"ok": False, "error": f"Source policy disabled: {source_key}"}), 409

    force = _truthy_arg(request.args.get("force"))
    with automation_lock(current_app):
        stats = run_ingestion_for_policies(
            db,
            profile=None,
            source_keys=(source_key,),
            due_only=not force,
            trigger="internal_api",
        )
    return jsonify({"ok": True, "mode": "force" if force else "due_only", "stats": stats})


@bp.route("/internal/import/opportunities", methods=("POST",))
def import_opportunities_internal():
    auth_error = _authorize_internal_request()
    if auth_error is not None:
        return auth_error

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "error": "Expected a JSON payload."}), 400

    source_key = "email_alerts"
    source_label = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        source_key = str(payload.get("source_key", source_key) or source_key).strip() or source_key
        raw_items = payload.get("items", payload)
        source_label = str(payload.get("source_label", "") or "").strip() or None
        if isinstance(raw_items, list):
            items = raw_items
        elif isinstance(raw_items, dict):
            items = [raw_items]
        else:
            return jsonify({"ok": False, "error": "Field 'items' must be an object or array."}), 400
    else:
        return jsonify({"ok": False, "error": "Unsupported JSON payload."}), 400

    if not items:
        return jsonify({"ok": False, "error": "No opportunities received."}), 400

    db = get_db()
    with automation_lock(current_app):
        stats = import_opportunities(
            db,
            source_key=source_key,
            source_label=source_label,
            items=items,
            profile=None,
            trigger="internal_import",
        )
    return jsonify({"ok": True, "stats": stats})


@bp.route("/dashboard/save-view", methods=("POST",))
@login_required
def save_view():
    name = request.form.get("name", "").strip()
    query_string = request.form.get("query_string", "").strip()
    db = get_db()

    if not name:
        flash("La vista guardada necesita un nombre.", "error")
        return redirect(url_for("main.dashboard"))

    db.execute(
        """
        INSERT INTO saved_views (user_id, name, query_string)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, name) DO UPDATE SET query_string = excluded.query_string
        """,
        (g.user["id"], name, query_string),
    )
    db.commit()
    flash("Vista guardada.", "success")
    return redirect(url_for("main.dashboard"))


@bp.route("/saved-views/<int:view_id>/delete", methods=("POST",))
@login_required
def delete_saved_view(view_id: int):
    db = get_db()
    db.execute(
        "DELETE FROM saved_views WHERE id = ? AND user_id = ?",
        (view_id, g.user["id"]),
    )
    db.commit()
    flash("Vista eliminada.", "success")
    return redirect(url_for("main.settings"))


@bp.route("/opportunities/<int:opportunity_id>", methods=("GET", "POST"))
@login_required
def opportunity_detail(opportunity_id: int):
    db = get_db()
    if request.method == "POST":
        note = request.form.get("note", "").strip()
        new_state = request.form.get("state", "").strip()
        try:
            update_opportunity_state(
                db,
                opportunity_id=opportunity_id,
                actor_user_id=g.user["id"],
                new_state=new_state,
                note=note,
            )
            flash("Estado actualizado.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        except LookupError:
            abort(404)
        return redirect(url_for("main.opportunity_detail", opportunity_id=opportunity_id))

    row = db.execute(
        "SELECT * FROM opportunities WHERE id = ?",
        (opportunity_id,),
    ).fetchone()
    if row is None:
        abort(404)

    events = db.execute(
        """
        SELECT e.*, u.email AS actor_email
        FROM opportunity_events e
        LEFT JOIN users u ON u.id = e.actor_user_id
        WHERE e.opportunity_id = ?
        ORDER BY e.created_at DESC, e.id DESC
        """,
        (opportunity_id,),
    ).fetchall()
    item = _serialize_opportunity(row)
    return render_template(
        "opportunity_detail.html",
        opportunity=item,
        events=events,
        pipeline_states=PIPELINE_STATES,
        quick_actions=_quick_actions(item),
    )


@bp.route("/settings", methods=("GET", "POST"))
@login_required
def settings():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_profile":
            db.execute(
                """
                UPDATE user_profiles
                SET keywords = ?, sectors = ?, min_budget = ?, preferred_sources = ?
                WHERE user_id = ?
                """,
                (
                    request.form.get("keywords", "").strip(),
                    request.form.get("sectors", "").strip(),
                    _safe_int(request.form.get("min_budget", "")),
                    request.form.get("preferred_sources", "").strip(),
                    g.user["id"],
                ),
            )
            db.commit()
            flash("Preferencias actualizadas.", "success")
            return redirect(url_for("main.settings"))

        if action == "update_policy":
            config_json = request.form.get("config_json", "").strip() or "{}"
            try:
                parsed = json.loads(config_json)
            except json.JSONDecodeError:
                flash("El JSON de la politica no es valido.", "error")
                return redirect(url_for("main.settings"))

            db.execute(
                """
                UPDATE source_policies
                SET enabled = ?,
                    frequency_minutes = ?,
                    risk_level = ?,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    1 if request.form.get("enabled") else 0,
                    max(1, _safe_int(request.form.get("frequency_minutes", "30"), 30)),
                    request.form.get("risk_level", "low").strip(),
                    json.dumps(parsed, ensure_ascii=False),
                    request.form.get("notes", "").strip(),
                    request.form.get("policy_id"),
                ),
            )
            db.commit()
            flash("Politica actualizada.", "success")
            return redirect(url_for("main.settings"))

    profile = _get_profile(db, g.user["id"])
    automation_summary = _automation_summary(db)
    n8n_summary = _n8n_summary()
    policies = [
        {
            **dict(row),
            **_serialize_policy_runtime_status(row),
            "config_pretty": json.dumps(json.loads(row["config_json"] or "{}"), indent=2, ensure_ascii=False),
        }
        for row in db.execute(
            "SELECT * FROM source_policies ORDER BY display_name ASC"
        ).fetchall()
    ]
    saved_views = db.execute(
        """
        SELECT *
        FROM saved_views
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template(
        "settings.html",
        profile=profile,
        policies=policies,
        saved_views=saved_views,
        automation_summary=automation_summary,
        n8n_summary=n8n_summary,
    )


@bp.route("/exports/opportunities.csv")
@login_required
def export_csv():
    db = get_db()
    filters = _build_filters(_get_profile(db, g.user["id"]))
    rows = _fetch_opportunities(db, filters, limit=None)
    items = _sort_items([_serialize_opportunity(row) for row in rows])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "titulo",
            "empresa",
            "fuente",
            "canal_aplicacion",
            "score",
            "prioridad_real",
            "estado",
            "sector",
            "fit_ia",
            "accion_ia",
            "paso_siguiente",
            "presupuesto",
            "riesgo",
            "url",
        ]
    )

    for item in items:
        writer.writerow(
            [
                item["id"],
                item["title"],
                item["company"],
                item["source_label"],
                item["application_host"],
                item["score"],
                item["priority_label"],
                item["state"],
                item["sector"],
                item["analysis"].get("fit_label", ""),
                item["analysis"].get("recommended_action", ""),
                item["action_summary"],
                item["budget_display"],
                item["risk_level"],
                item["url"],
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=scrapperlanas-opportunities.csv"},
    )


def _get_profile(db, user_id: int):
    return db.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def _build_filters(profile) -> dict:
    has_query = bool(request.args)
    return {
        "q": request.args.get("q", "").strip(),
        "state": request.args.get("state", "").strip(),
        "source": request.args.get("source", "").strip(),
        "sector": request.args.get("sector", "").strip() or (profile["sectors"] if profile and not has_query else ""),
        "risk": request.args.get("risk", "").strip(),
        "min_budget": request.args.get("min_budget", "").strip()
        or (str(profile["min_budget"]) if profile and profile["min_budget"] and not has_query else ""),
    }


def _blank_filters() -> dict:
    return {
        "q": "",
        "state": "",
        "source": "",
        "sector": "",
        "risk": "",
        "min_budget": "",
    }


def _fetch_opportunities(db, filters: dict, *, limit: int | None):
    query = "SELECT * FROM opportunities WHERE 1 = 1"
    params: list = []

    if filters["q"]:
        like_value = f"%{filters['q']}%"
        query += " AND (title LIKE ? OR company LIKE ? OR ai_summary LIKE ?)"
        params.extend([like_value, like_value, like_value])
    if filters["state"]:
        query += " AND state = ?"
        params.append(filters["state"])
    if filters["source"]:
        query += " AND source_label = ?"
        params.append(filters["source"])
    if filters["sector"]:
        query += " AND sector LIKE ?"
        params.append(f"%{filters['sector']}%")
    if filters["risk"]:
        query += " AND risk_level = ?"
        params.append(filters["risk"])
    if filters["min_budget"]:
        query += " AND COALESCE(budget_max, budget_min, 0) >= ?"
        params.append(_safe_int(filters["min_budget"]))

    query += " ORDER BY score DESC, COALESCE(posted_at, created_at) DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    return db.execute(query, tuple(params)).fetchall()


def _serialize_opportunity(row) -> dict:
    item = dict(row)
    item["stack"] = json.loads(item["stack"] or "[]")
    item["risk_reasons"] = json.loads(item["risk_reasons"] or "[]")
    item["analysis"] = json.loads(item.get("analysis_json") or "{}")
    item["budget_display"] = _format_budget(item)
    item["state_badge_class"] = STATE_BADGE_STYLES.get(item["state"], "border border-slate-600 bg-slate-800 text-slate-300")
    item["risk_badge_class"] = RISK_BADGE_STYLES.get(item["risk_level"], "border border-slate-600 bg-slate-800 text-slate-300")
    item["action_label"] = _action_label(item["analysis"].get("recommended_action"))
    item["action_summary"] = _action_summary(item["analysis"].get("recommended_action"))
    item["fit_label_display"] = _fit_label_display(item["analysis"].get("fit_label"))
    item["application_host"] = _application_host(item["url"])
    item["posted_display"] = _posted_display(item.get("posted_at"), item.get("created_at"))
    item["freshness_label"] = _freshness_label(item.get("posted_at"), item.get("created_at"))
    item.update(_priority_profile(item))
    return item


def _format_budget(item: dict) -> str:
    if item["budget_text"]:
        return item["budget_text"]
    if item["budget_min"] and item["budget_max"]:
        return f"{item['currency']} {item['budget_min']:,} - {item['budget_max']:,}"
    if item["budget_min"]:
        return f"{item['currency']} {item['budget_min']:,}"
    if item["budget_max"]:
        return f"{item['currency']} {item['budget_max']:,}"
    return "No especificado"


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy_arg(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _authorize_internal_request():
    cron_secret = str(current_app.config.get("CRON_SECRET", "") or "").strip()
    if not cron_secret:
        return jsonify({"ok": False, "error": "CRON_SECRET is not configured."}), 503

    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    expected = f"Bearer {cron_secret}"
    if auth_header != expected:
        return jsonify({"ok": False, "error": "Unauthorized cron request."}), 401
    return None


def _clean_query_string(filters: dict) -> str:
    return urlencode({key: value for key, value in filters.items() if value})


def _automation_summary(db) -> dict:
    mode_key = str(current_app.config.get("AUTOMATION_MODE", "manual") or "manual").strip().lower()
    mode_copy = {
        "manual": {
            "label": "Manual",
            "headline": "La ingesta no esta automatizada todavia.",
            "summary": "El boton del dashboard ejecuta una corrida manual. La IA corre dentro de cada ingesta, no todo el tiempo en segundo plano.",
        },
        "local": {
            "label": "Local",
            "headline": "La app puede autocorrer las fuentes vencidas mientras esta maquina siga prendida.",
            "summary": "Un worker local revisa fuentes vencidas cada pocos minutos, escribe logs y deja heartbeat. Si apagas la compu, este modo tambien se apaga.",
        },
        "n8n": {
            "label": "n8n",
            "headline": "n8n puede correr las fuentes vencidas sin tocar el dashboard.",
            "summary": "n8n consulta fuentes vencidas y dispara una por una. El boton del dashboard solo fuerza una corrida manual cuando quieres revisar algo ya.",
        },
        "vercel_cron": {
            "label": "Vercel cron",
            "headline": "El cron interno puede correr las fuentes vencidas de forma periodica.",
            "summary": "El scheduler externo pega a los endpoints internos y respeta la frecuencia por fuente. El boton manual sigue disponible para pruebas o corridas urgentes.",
        },
    }
    mode = mode_copy.get(mode_key, mode_copy["manual"])
    policy_statuses = [_serialize_policy_runtime_status(policy) for policy in list_enabled_policies(db)]
    due_count = sum(1 for policy in policy_statuses if policy["is_due"])
    future_minutes = [
        policy["minutes_until_due"]
        for policy in policy_statuses
        if not policy["is_due"] and policy["minutes_until_due"] is not None
    ]
    next_due_minutes = min(future_minutes) if future_minutes else (0 if due_count else None)
    has_secret = bool(str(current_app.config.get("CRON_SECRET", "") or "").strip())
    runtime = get_runtime_snapshot(current_app)
    automation_ready = mode_key == "local" or (has_secret and mode_key in {"n8n", "vercel_cron"})
    stability = _recent_automation_metrics(db)

    return {
        "mode_key": mode_key,
        "mode_label": mode["label"],
        "headline": mode["headline"],
        "summary": mode["summary"],
        "automation_ready": automation_ready,
        "enabled_count": len(policy_statuses),
        "due_count": due_count,
        "next_due_minutes": next_due_minutes,
        "next_due_label": _minutes_display(next_due_minutes),
        "policies": policy_statuses,
        "runtime": runtime,
        "stability": stability,
    }


def _n8n_summary() -> dict:
    public_url = str(current_app.config.get("N8N_PUBLIC_URL", "") or "").strip().rstrip("/")
    base_url = str(current_app.config.get("SCRAPPERLANAS_BASE_URL", "") or "").strip().rstrip("/")
    import_path = str(current_app.config.get("N8N_IMPORT_WEBHOOK_PATH", "") or "").strip().strip("/")
    scheduler_interval = max(1, int(current_app.config.get("N8N_SCHEDULER_INTERVAL_MINUTES", 15) or 15))
    workflows = (
        {
            "name": "Scrapperlanas Scheduler",
            "filename": "scrapperlanas_scheduler.json",
            "purpose": "Dispara la ingesta due-only sin depender del boton del dashboard.",
        },
        {
            "name": "Scrapperlanas Import Webhook",
            "filename": "scrapperlanas_import_webhook.json",
            "purpose": "Recibe JSON en n8n y lo reinyecta al import interno para correo, forms o webhooks externos.",
        },
    )
    import_webhook_url = f"{public_url}/webhook/{import_path}" if public_url and import_path else ""
    return {
        "enabled": str(current_app.config.get("AUTOMATION_MODE", "")).strip().lower() == "n8n",
        "cron_secret_configured": bool(str(current_app.config.get("CRON_SECRET", "") or "").strip()),
        "public_url": public_url,
        "base_url": base_url,
        "import_webhook_path": import_path,
        "import_webhook_url": import_webhook_url,
        "scheduler_interval_minutes": scheduler_interval,
        "bootstrap_force": bool(current_app.config.get("N8N_BOOTSTRAP_FORCE")),
        "workflows": workflows,
    }


def _serialize_policy_runtime_status(policy_row) -> dict:
    policy = dict(policy_row)
    runtime = policy_runtime_status(policy)
    return {
        **runtime,
        "enabled": bool(policy.get("enabled")),
        "last_run_display": _datetime_display(runtime.get("last_run_at")),
        "next_run_display": "ahora" if runtime["is_due"] else _datetime_display(runtime.get("next_run_at")),
    }


def _datetime_display(value) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "nunca"
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def _minutes_display(minutes: int | None) -> str:
    if minutes is None:
        return "sin programacion"
    if minutes <= 0:
        return "ahora"
    if minutes < 60:
        return f"en {minutes} min"
    hours, remainder = divmod(minutes, 60)
    if remainder == 0:
        return f"en {hours} h"
    return f"en {hours} h {remainder} min"


def _recent_automation_metrics(db) -> dict:
    since = (datetime.now(UTC) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    row = db.execute(
        """
        SELECT
            COUNT(*) AS total_runs,
            COALESCE(SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), 0) AS successful_runs,
            COALESCE(SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END), 0) AS failed_runs,
            COALESCE(SUM(created), 0) AS created_total,
            COALESCE(SUM(updated), 0) AS updated_total,
            COALESCE(SUM(fetched), 0) AS fetched_total,
            COALESCE(ROUND(AVG(duration_ms), 1), 0) AS average_duration_ms
        FROM automation_runs
        WHERE started_at >= ?
        """,
        (since,),
    ).fetchone()
    latest_error = db.execute(
        """
        SELECT source_key, display_name, error, finished_at
        FROM automation_runs
        WHERE status = 'error'
        ORDER BY finished_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "window": "24h",
        "total_runs": int(row["total_runs"] or 0),
        "successful_runs": int(row["successful_runs"] or 0),
        "failed_runs": int(row["failed_runs"] or 0),
        "fetched_total": int(row["fetched_total"] or 0),
        "created_total": int(row["created_total"] or 0),
        "updated_total": int(row["updated_total"] or 0),
        "average_duration_ms": float(row["average_duration_ms"] or 0),
        "latest_error": dict(latest_error) if latest_error else None,
    }


def _recent_automation_runs(db, *, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT trigger_kind, source_key, display_name, status, fetched, created, updated, skipped, duration_ms, error, finished_at
        FROM automation_runs
        ORDER BY finished_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def _sort_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            -int(item.get("score") or 0),
            item.get("title", ""),
        ),
    )


def _action_label(value) -> str:
    key = str(value or "").strip()
    return ACTION_COPY.get(key, ACTION_COPY["clarify_scope"])["label"]


def _action_summary(value) -> str:
    key = str(value or "").strip()
    return ACTION_COPY.get(key, ACTION_COPY["clarify_scope"])["summary"]


def _fit_label_display(value) -> str:
    return FIT_COPY.get(str(value or "").strip(), "sin clasificar")


def _application_host(url: str) -> str:
    host = urlparse(str(url or "")).netloc.lower().replace("www.", "").strip()
    if not host:
        return "Canal no detectado"

    host_labels = {
        "job-boards.greenhouse.io": "Greenhouse directo",
        "boards.greenhouse.io": "Greenhouse directo",
        "api.lever.co": "Lever",
        "lever.co": "Lever directo",
        "workana.com": "Workana",
        "upwork.com": "Upwork",
        "weworkremotely.com": "We Work Remotely",
        "news.ycombinator.com": "Hacker News",
        "workatastartup.com": "Work at a Startup",
        "ycombinator.com": "Y Combinator Jobs",
    }
    for key, label in host_labels.items():
        if host == key or host.endswith(f".{key}"):
            return label
    return host


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _posted_display(posted_at, created_at) -> str:
    parsed = _parse_datetime(posted_at) or _parse_datetime(created_at)
    if parsed is None:
        return "Sin fecha"
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


def _freshness_label(posted_at, created_at) -> str:
    parsed = _parse_datetime(posted_at) or _parse_datetime(created_at)
    if parsed is None:
        return "Fecha no clara"

    age_days = (datetime.now(UTC) - parsed).days
    if age_days <= 1:
        return "muy fresca"
    if age_days <= 3:
        return "reciente"
    if age_days <= 7:
        return "de esta semana"
    return "backlog"


def _priority_profile(item: dict) -> dict:
    text = " ".join(
        str(part or "")
        for part in (
            item.get("title"),
            item.get("company"),
            item.get("raw_text"),
            " ".join(item.get("stack") or ()),
        )
    ).lower()
    priority_score = int(item.get("score") or 0)
    reasons: list[str] = []
    action = str(item.get("analysis", {}).get("recommended_action") or "").strip()
    fit_label = str(item.get("analysis", {}).get("fit_label") or "").strip()
    state = str(item.get("state") or "").strip()
    management_role = _looks_management_role(text)
    noncore_role = _looks_noncore_role(text)

    if action == "apply_now":
        priority_score += 14
        reasons.append("la IA lo ve listo para aplicar")
    elif action == "review_today":
        priority_score += 8
        reasons.append("merece decision hoy")
    elif action == "clarify_scope":
        priority_score += 2
    elif action == "skip":
        priority_score -= 18

    if fit_label == "strong_fit":
        priority_score += 8
        reasons.append("encaja fuerte con el stack objetivo")
    elif fit_label == "possible_fit":
        priority_score += 4
    elif fit_label == "avoid":
        priority_score -= 14

    if _looks_contract_now(text):
        priority_score += 8
        reasons.append("huele a oportunidad mas monetizable")
    if _looks_remote_text(text):
        priority_score += 4
    if management_role:
        priority_score -= 18
        reasons.append("es mas rol de gestion que de ejecucion")
    if noncore_role:
        priority_score -= 14
        reasons.append("menos alineado al nicho actual")
    if _looks_on_site(text):
        priority_score -= 10
    if item.get("risk_level") == "high":
        priority_score -= 20
    elif item.get("risk_level") == "medium":
        priority_score -= 6

    freshness = _freshness_label(item.get("posted_at"), item.get("created_at"))
    if freshness == "muy fresca":
        priority_score += 5
    elif freshness == "reciente":
        priority_score += 3

    if state in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}:
        priority_score -= 40

    if priority_score >= 60:
        tier = "A1"
        label = "golpear hoy"
    elif priority_score >= 48:
        tier = "A2"
        label = "resolver hoy"
    elif priority_score >= 36:
        tier = "B1"
        label = "revisar hoy"
    elif priority_score >= 24:
        tier = "B2"
        label = "backlog util"
    else:
        tier = "C"
        label = "baja prioridad"

    if not reasons:
        reasons.append("se mantiene en cola por score y claridad general")

    return {
        "priority_score": max(0, min(priority_score, 99)),
        "priority_tier": tier,
        "priority_label": label,
        "priority_badge_class": PRIORITY_STYLES[tier],
        "priority_reason": ". ".join(reasons[:2]).capitalize() + ".",
        "is_focus_candidate": (
            tier != "C"
            and action != "skip"
            and item.get("risk_level") != "high"
            and not management_role
            and not noncore_role
        ),
    }


def _focus_items(items: list[dict]) -> list[dict]:
    candidates = [
        item
        for item in items
        if item.get("is_focus_candidate") and item.get("state") not in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            -int(item.get("score") or 0),
            item.get("title", ""),
        )
    )
    return candidates[:8]


def _daily_plan(items: list[dict]) -> dict:
    active_items = [
        item
        for item in items
        if item.get("state") not in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
    ]
    return {
        "apply_now": sum(1 for item in active_items if item.get("analysis", {}).get("recommended_action") == "apply_now"),
        "review_today": sum(1 for item in active_items if item.get("analysis", {}).get("recommended_action") == "review_today"),
        "focus_now": sum(1 for item in active_items if item.get("priority_tier") in {"A1", "A2"}),
        "fresh": sum(1 for item in active_items if item.get("freshness_label") in {"muy fresca", "reciente"}),
    }


def _quick_actions(item: dict) -> list[dict]:
    actions = (
        {
            "state": "INTERESANTE",
            "label": "Priorizar",
            "note": "Marcada como prioridad real desde detalle.",
            "button_class": "border-blue-500/30 bg-blue-600 text-white hover:bg-blue-500",
            "icon": "fa-solid fa-star",
        },
        {
            "state": "APLICADO",
            "label": "Marcar aplicada",
            "note": "Aplicacion enviada desde la fuente original.",
            "button_class": "border-fuchsia-500/30 bg-fuchsia-600 text-white hover:bg-fuchsia-500",
            "icon": "fa-solid fa-paper-plane",
        },
        {
            "state": "FOLLOW_UP",
            "label": "Mover a follow-up",
            "note": "Pendiente seguimiento comercial.",
            "button_class": "border-amber-500/30 bg-amber-500/15 text-amber-200 hover:bg-amber-500/20",
            "icon": "fa-solid fa-comments",
        },
        {
            "state": "DESCARTADO",
            "label": "Descartar",
            "note": "Descartada por baja prioridad o mal encaje.",
            "button_class": "border-rose-500/30 bg-rose-500/15 text-rose-200 hover:bg-rose-500/20",
            "icon": "fa-solid fa-ban",
        },
    )
    return [action for action in actions if action["state"] != item.get("state")]


def _looks_contract_now(text: str) -> bool:
    hints = ("contract", "freelance", "consultant", "hourly", "fixed price", "budget", "$", "usd")
    return any(hint in text for hint in hints)


def _looks_remote_text(text: str) -> bool:
    hints = ("remote", "worldwide", "distributed", "anywhere", "work from home")
    return any(hint in text for hint in hints)


def _looks_on_site(text: str) -> bool:
    hints = ("onsite", "on-site", "hybrid", "in office", "office-based")
    return any(hint in text for hint in hints)


def _looks_management_role(text: str) -> bool:
    hints = ("engineering manager", "manager", "director", "head of", "vp ", "vice president")
    return any(hint in text for hint in hints)


def _looks_noncore_role(text: str) -> bool:
    hints = (
        "frontend",
        "front-end",
        "ios",
        "android",
        "mobile",
        "designer",
        "design lead",
        "wordpress",
        "shopware",
        "bubble.io",
        "bubble",
        "firmware",
        "devrel",
    )
    return any(hint in text for hint in hints)
