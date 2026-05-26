from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from .services.buyer_intelligence import (
    assign_missing_buyer_accounts,
    detect_possible_duplicates,
    ignore_duplicate_candidate,
    list_duplicate_candidates,
    merge_buyer_accounts,
    recalculate_buyer_account,
)
from .services.cadence import (
    COMMERCIAL_STATUSES,
    IGNORED_REASONS,
    LOST_REASONS,
    STATUS_LABELS,
    apply_commercial_action,
)
from .services.connectors.base import ERROR_STATUSES
from .services.connectors.registry import connector_for_provider
from .services.intelligence import (
    available_transitions,
    collect_evidence,
    compare_accounts,
    compose_outreach,
    copilot_mode,
    recommend_next_best_action,
    score_v2,
    transition_opportunity,
)
from .services.intelligence.risk import assess_risk
from .services.intelligence.state_machine import CANONICAL_TO_LEGACY, LEGACY_TO_CANONICAL
from .services.quality import assess_opportunity_quality
from .services.lead_explainer import explain_opportunity
from .services.quality_memory import VALID_DECISIONS, get_feedback_for_opportunity, get_feedback_stats, record_feedback
from .services.outreach import (
    activate_outreach_draft,
    list_active_outreach_drafts,
    persist_outreach_drafts,
    recommended_followup_type,
    serialize_outreach_draft,
)
from .services.pipeline import (
    PIPELINE_STATES,
    get_policy_by_source_key,
    import_opportunities,
    list_due_policies,
    list_enabled_policies,
    policy_runtime_status,
    recompute_quality,
    recompute_quality_for_opportunity,
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
    "B": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    "B1": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
    "B2": "border border-slate-600 bg-slate-800 text-slate-300",
    "C": "border border-rose-500/30 bg-rose-500/15 text-rose-200",
    "D": "border border-slate-700 bg-slate-950 text-slate-400",
}

PRIORITY_LABELS = {
    "A1": "contactar hoy",
    "A2": "esta semana",
    "B": "nutrir",
    "B1": "revisar hoy",
    "B2": "backlog util",
    "C": "ruido",
    "D": "descartar",
}

ATS_PROVIDERS = ("greenhouse", "lever", "ashby", "workable", "unknown")
TARGET_PRIORITIES = ("high", "medium", "low")
TARGET_SOURCE_KEYS = {
    "greenhouse": "greenhouse_jobs",
    "lever": "lever_postings",
    "ashby": "ashby_jobs",
    "workable": "workable_jobs",
}
TARGET_SOURCE_LABELS = {
    "greenhouse": "Greenhouse Target Signals",
    "lever": "Lever Target Signals",
    "ashby": "Ashby Target Signals",
    "workable": "Workable Target Signals",
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
    _refresh_buyer_accounts_light(db)
    profile = _get_profile(db, g.user["id"])
    automation_summary = _automation_summary(db)
    filters = _build_filters(profile)
    rows = _fetch_opportunities(db, filters, limit=240)
    items = _apply_quality_visibility(_sort_items([_serialize_opportunity(row) for row in rows]), filters)
    items = _apply_quick_filter(items, filters)
    items = items[:120]
    all_rows = _fetch_opportunities(db, _blank_filters(), limit=None)
    all_items = _sort_items([_serialize_opportunity(row) for row in all_rows])
    _attach_outreach_summary(db, items)
    _attach_outreach_summary(db, all_items)

    quality_metrics = _quality_metrics(all_items)
    metrics = {
        "total": len(all_items),
        "active": quality_metrics["contactable"] + quality_metrics["review_required"] + quality_metrics["watchlist"],
        "suspicious": quality_metrics["suspect"],
        "high_score": sum(1 for item in all_items if item["quality"]["is_contactable"]),
        "cashflow": sum(_budget_anchor(item) for item in all_items if item["quality"]["is_contactable"]),
        "quality": quality_metrics,
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
    sales_cockpit = _sales_cockpit(all_items)
    sales_actions = _sales_actions(all_items)
    source_performance = _source_performance(all_items)
    hot_accounts = _hot_accounts(db, limit=6)
    target_signals = _target_signal_rows(db, limit=6)
    duplicate_candidates_count = db.execute(
        "SELECT COUNT(*) AS total FROM buyer_account_duplicate_candidates WHERE status = 'open'"
    ).fetchone()["total"]
    a1_items = _tier_items(all_items, tier="A1", limit=6)
    a2_items = _tier_items(all_items, tier="A2", limit=6)
    copilot_summary = _copilot_summary(all_items, source_performance=source_performance)

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
        sales_cockpit=sales_cockpit,
        sales_actions=sales_actions,
        source_performance=source_performance,
        hot_accounts=hot_accounts,
        target_signals=target_signals,
        duplicate_candidates_count=duplicate_candidates_count,
        a1_items=a1_items,
        a2_items=a2_items,
        copilot_summary=copilot_summary,
        automation_summary=automation_summary,
    )


@bp.route("/quality")
@login_required
def quality_command_center():
    db = get_db()
    rows = _fetch_opportunities(db, _blank_filters(), limit=None)
    all_items = _sort_items([_serialize_opportunity(row) for row in rows])
    quality_metrics = _quality_metrics(all_items)
    reddit_policy = get_policy_by_source_key(db, "reddit")
    reddit_config = _policy_config(reddit_policy)
    feedback_stats = get_feedback_stats(db)
    return render_template(
        "quality.html",
        items=all_items,
        metrics=quality_metrics,
        source_performance=_source_performance(all_items),
        feedback_stats=feedback_stats,
        reddit_policy=reddit_policy,
        reddit_config=reddit_config,
        golden_status=_golden_dataset_status(),
        feedback_decisions=sorted(VALID_DECISIONS),
    )


@bp.route("/quality/rules", methods=("POST",))
@login_required
def quality_rules_update():
    db = get_db()
    policy = get_policy_by_source_key(db, "reddit")
    if policy is None:
        flash("No existe politica de Reddit para actualizar.", "error")
        return redirect(url_for("main.quality_command_center"))

    config = _policy_config(policy)
    reddit_config = dict(config.get("reddit") if isinstance(config.get("reddit"), dict) else {})
    reddit_config.update(
        {
            "allowlist": _split_rule_list(request.form.get("allowlist")),
            "greylist": _split_rule_list(request.form.get("greylist")),
            "blocklist": _split_rule_list(request.form.get("blocklist")),
            "minCommercialIntentScore": _safe_int(request.form.get("minCommercialIntentScore"), 65),
            "hideRejectedByDefault": request.form.get("hideRejectedByDefault") == "on",
            "hideDuplicates": request.form.get("hideDuplicates") == "on",
        }
    )
    quality_config = dict(config.get("quality") if isinstance(config.get("quality"), dict) else {})
    quality_config.update(
        {
            "minReadyToContactScore": _safe_int(request.form.get("minReadyToContactScore"), 75),
            "minBuyerConfidence": _safe_int(request.form.get("minBuyerConfidence"), 50),
            "rejectCriticalRisk": request.form.get("rejectCriticalRisk") == "on",
            "hideDuplicates": request.form.get("hideDuplicates") == "on",
        }
    )
    config["reddit"] = reddit_config
    config["quality"] = quality_config
    db.execute(
        "UPDATE source_policies SET config_json = ? WHERE source_key = 'reddit'",
        (json.dumps(config, ensure_ascii=False, indent=2),),
    )
    db.commit()
    flash("Reglas de calidad actualizadas. Reprocesa calidad para aplicar sobre datos existentes.", "success")
    return redirect(url_for("main.quality_command_center"))


@bp.route("/quality/recompute", methods=("POST",))
@login_required
def quality_recompute():
    db = get_db()
    summary = recompute_quality(db, limit=_safe_int(request.form.get("limit"), 500), profile=_get_profile(db, g.user["id"]))
    flash(
        "Calidad recalculada. "
        f"Processed: {summary['processed']}, Updated: {summary['updated']}, "
        f"Rejected: {summary['rejected']}, Duplicates hidden: {summary['duplicates_hidden']}, Errors: {summary['errors']}.",
        "success" if summary["errors"] == 0 else "error",
    )
    return redirect(url_for("main.quality_command_center"))


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


@bp.route("/opportunities/bulk", methods=("POST",))
@login_required
def opportunities_bulk_action():
    ids = [_safe_int(value) for value in request.form.getlist("opportunity_ids")]
    ids = [value for value in ids if value > 0]
    action = request.form.get("bulk_action", "").strip()
    target_state = request.form.get("target_state", "").strip()
    if not ids:
        flash("Selecciona al menos una oportunidad para aplicar una accion masiva.", "error")
        return redirect(url_for("main.dashboard"))

    db = get_db()
    changed = 0
    for opportunity_id in ids[:100]:
        row = db.execute("SELECT state FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
        if row is None:
            continue
        if action == "move_stage" and target_state in PIPELINE_STATES:
            update_opportunity_state(
                db,
                opportunity_id=opportunity_id,
                actor_user_id=g.user["id"],
                new_state=target_state,
                note="Accion masiva desde Inbox inteligente.",
            )
            changed += 1
        elif action == "discard":
            update_opportunity_state(
                db,
                opportunity_id=opportunity_id,
                actor_user_id=g.user["id"],
                new_state="DESCARTADO",
                note="Descartada por accion masiva desde Inbox inteligente.",
            )
            changed += 1
    flash(f"Accion masiva aplicada a {changed} oportunidades.", "success" if changed else "error")
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


@bp.route("/api/intelligence/summary", methods=("GET",))
@login_required
def api_intelligence_summary():
    db = get_db()
    filters = _blank_filters()
    rows = _fetch_opportunities(db, _blank_filters(), limit=None)
    items = _apply_quality_visibility(_sort_items([_serialize_opportunity(row) for row in rows]), filters)
    items = _apply_quick_filter(items, filters)
    return jsonify({"ok": True, "summary": _copilot_summary(items, source_performance=_source_performance(items))})


@bp.route("/api/opportunities/<int:opportunity_id>/intelligence", methods=("GET",))
@login_required
def api_opportunity_intelligence(opportunity_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if row is None:
        return jsonify({"ok": False, "error": "Opportunity not found."}), 404
    item = _serialize_opportunity(row)
    return jsonify({"ok": True, "opportunity_id": opportunity_id, "intelligence": item["intelligence"]})


@bp.route("/api/opportunities/<int:opportunity_id>/transition", methods=("POST",))
@login_required
def api_opportunity_transition(opportunity_id: int):
    db = get_db()
    opportunity = db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if opportunity is None:
        return jsonify({"ok": False, "error": "Opportunity not found."}), 404

    payload = request.get_json(silent=True) or {}
    target = str(payload.get("next_state") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    override = _truthy_arg(payload.get("override"))
    context = {
        **dict(opportunity),
        **(payload.get("context") if isinstance(payload.get("context"), dict) else {}),
    }
    transition = transition_opportunity(
        opportunity["state"],
        target,
        context,
        actor=str(g.user["id"]),
        reason=reason,
        override=override,
    )
    if not transition.allowed:
        return jsonify({"ok": False, "transition": transition.to_dict()}), 409

    legacy_state = CANONICAL_TO_LEGACY.get(transition.next_state)
    if legacy_state:
        update_opportunity_state(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=g.user["id"],
            new_state=legacy_state,
            note=reason or f"Transition Intelligence V2: {transition.previous_state}->{transition.next_state}",
        )
    return jsonify({"ok": True, "transition": transition.to_dict(), "legacy_state": legacy_state})


@bp.route("/api/opportunities/<int:opportunity_id>/outreach/draft", methods=("POST",))
@login_required
def api_opportunity_outreach_draft(opportunity_id: int):
    db = get_db()
    opportunity = db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if opportunity is None:
        return jsonify({"ok": False, "error": "Opportunity not found."}), 404
    payload = request.get_json(silent=True) or {}
    item = _serialize_opportunity(opportunity)
    draft = compose_outreach(item, tone=str(payload.get("tone") or "consultivo"))
    return jsonify({"ok": True, "draft": draft.to_dict()})


@bp.route("/api/accounts/<int:account_id>/merge-suggestions", methods=("GET",))
@login_required
def api_account_merge_suggestions(account_id: int):
    db = get_db()
    account = db.execute("SELECT * FROM buyer_accounts WHERE id = ?", (account_id,)).fetchone()
    if account is None:
        return jsonify({"ok": False, "error": "Account not found."}), 404
    candidates = db.execute(
        """
        SELECT *
        FROM buyer_accounts
        WHERE id <> ?
        ORDER BY updated_at DESC
        LIMIT 30
        """,
        (account_id,),
    ).fetchall()
    suggestions = []
    for candidate in candidates:
        assessment = compare_accounts(dict(account), dict(candidate))
        if assessment.duplicate_confidence >= 0.55:
            suggestions.append({"account": dict(candidate), "assessment": assessment.to_dict()})
    suggestions.sort(key=lambda item: item["assessment"]["duplicate_confidence"], reverse=True)
    return jsonify({"ok": True, "suggestions": suggestions[:8]})


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
    commercial_activities = db.execute(
        """
        SELECT a.*, u.email AS actor_email
        FROM commercial_activities a
        LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE a.opportunity_id = ?
        ORDER BY a.created_at DESC, a.id DESC
        """,
        (opportunity_id,),
    ).fetchall()
    item = _serialize_opportunity(row)
    outreach_drafts = list_active_outreach_drafts(db, opportunity_id=opportunity_id)
    outreach_by_type = _drafts_by_type(outreach_drafts)
    feedback_history = get_feedback_for_opportunity(db, opportunity_id, limit=12)
    return render_template(
        "opportunity_detail.html",
        opportunity=item,
        events=events,
        commercial_activities=commercial_activities,
        outreach_drafts=outreach_drafts,
        outreach_by_type=outreach_by_type,
        outreach_primary=outreach_by_type.get("initial"),
        outreach_followup=outreach_by_type.get(recommended_followup_type(item.get("followup_count"))),
        pipeline_states=PIPELINE_STATES,
        commercial_statuses=COMMERCIAL_STATUSES,
        commercial_status_labels=STATUS_LABELS,
        lost_reasons=LOST_REASONS,
        ignored_reasons=IGNORED_REASONS,
        quick_actions=_quick_actions(item),
        feedback_history=feedback_history,
        feedback_decisions=sorted(VALID_DECISIONS),
    )


@bp.route("/opportunities/<int:opportunity_id>/quality-feedback", methods=("POST",))
@login_required
def opportunity_quality_feedback(opportunity_id: int):
    decision = request.form.get("decision", "").strip().upper()
    if decision not in VALID_DECISIONS:
        flash("Decision de feedback no permitida.", "error")
        return _commercial_redirect(opportunity_id)

    db = get_db()
    row = db.execute("SELECT id FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if row is None:
        abort(404)
    try:
        record_feedback(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=g.user["id"],
            decision=decision,
            reason=request.form.get("reason", "").strip(),
            corrected_stage=request.form.get("corrected_stage", "").strip(),
            corrected_grade=request.form.get("corrected_grade", "").strip(),
            metadata={"source": "ui"},
        )
        recompute_quality_for_opportunity(db, opportunity_id, profile=_get_profile(db, g.user["id"]))
    except ValueError as exc:
        flash(str(exc), "error")
    except LookupError:
        abort(404)
    else:
        flash("Feedback guardado y calidad recalculada.", "success")
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/status", methods=("POST",))
@login_required
def commercial_status_update(opportunity_id: int):
    status = request.form.get("commercial_status", "").strip()
    _run_commercial_action(
        opportunity_id,
        "status",
        notes=request.form.get("notes", "").strip(),
        metadata={"to_status": status},
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/contacted", methods=("POST",))
@login_required
def commercial_contacted(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "contacted",
        notes=request.form.get("notes", "").strip(),
        channel=request.form.get("contact_channel", "").strip(),
        contact_value=request.form.get("contact_value", "").strip(),
        contact_url=request.form.get("contact_url", "").strip(),
        message_snapshot=request.form.get("message_snapshot", "").strip(),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/followup-completed", methods=("POST",))
@login_required
def commercial_followup_completed(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "followup_completed",
        notes=request.form.get("notes", "").strip(),
        message_snapshot=request.form.get("message_snapshot", "").strip(),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/replied", methods=("POST",))
@login_required
def commercial_replied(opportunity_id: int):
    _run_commercial_action(opportunity_id, "replied", notes=request.form.get("notes", "").strip())
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/proposal", methods=("POST",))
@login_required
def commercial_proposal(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "proposal",
        notes=request.form.get("notes", "").strip(),
        proposal_value=_optional_int(request.form.get("proposal_value")),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/won", methods=("POST",))
@login_required
def commercial_won(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "won",
        notes=request.form.get("notes", "").strip(),
        won_value=_optional_int(request.form.get("won_value")),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/lost", methods=("POST",))
@login_required
def commercial_lost(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "lost",
        notes=request.form.get("notes", "").strip(),
        lost_reason=request.form.get("lost_reason", "other").strip(),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/ignored", methods=("POST",))
@login_required
def commercial_ignored(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "ignored",
        notes=request.form.get("notes", "").strip(),
        ignored_reason=request.form.get("ignored_reason", "other").strip(),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/snooze", methods=("POST",))
@login_required
def commercial_snooze(opportunity_id: int):
    _run_commercial_action(
        opportunity_id,
        "snoozed",
        notes=request.form.get("notes", "").strip(),
        snoozed_until=request.form.get("snoozed_until", "").strip(),
    )
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/note", methods=("POST",))
@login_required
def commercial_note(opportunity_id: int):
    _run_commercial_action(opportunity_id, "note", notes=request.form.get("notes", "").strip())
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/outreach/generate", methods=("POST",))
@login_required
def outreach_generate(opportunity_id: int):
    db = get_db()
    try:
        drafts = persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=False)
    except LookupError:
        abort(404)
    flash(f"Mensajes comerciales listos: {len(drafts)} drafts activos.", "success")
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/outreach/regenerate", methods=("POST",))
@login_required
def outreach_regenerate(opportunity_id: int):
    db = get_db()
    try:
        drafts = persist_outreach_drafts(db, opportunity_id=opportunity_id, regenerate=True)
    except LookupError:
        abort(404)
    flash(f"Mensajes regenerados: {len(drafts)} drafts activos.", "success")
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/outreach/<int:draft_id>/activate", methods=("POST",))
@login_required
def outreach_activate(opportunity_id: int, draft_id: int):
    db = get_db()
    try:
        activate_outreach_draft(db, opportunity_id=opportunity_id, draft_id=draft_id)
    except LookupError:
        abort(404)
    flash("Draft activado.", "success")
    return _commercial_redirect(opportunity_id)


@bp.route("/opportunities/<int:opportunity_id>/outreach/<int:draft_id>/copy-log", methods=("POST",))
@login_required
def outreach_copy_log(opportunity_id: int, draft_id: int):
    db = get_db()
    draft = db.execute(
        "SELECT * FROM outreach_drafts WHERE id = ? AND opportunity_id = ?",
        (draft_id, opportunity_id),
    ).fetchone()
    if draft is None:
        abort(404)
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
            g.user["id"],
            "message_copied",
            None,
            None,
            draft["channel"],
            draft["body"],
            f"Copiado draft {draft['draft_type']}.",
            json.dumps({"draft_id": draft_id, "draft_type": draft["draft_type"]}, ensure_ascii=False),
        ),
    )
    db.commit()
    if request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True})
    flash("Copia registrada en historial comercial.", "success")
    return _commercial_redirect(opportunity_id)


@bp.route("/targets")
@login_required
def targets():
    db = get_db()
    rows = _target_signal_rows(db, limit=200)
    return render_template("targets.html", targets=rows)


@bp.route("/targets/new", methods=("GET", "POST"))
@login_required
def target_new():
    if request.method == "POST":
        target_data = _target_form_data()
        if not target_data["name"]:
            flash("La empresa objetivo necesita nombre.", "error")
            return render_template("target_form.html", target=target_data, providers=ATS_PROVIDERS, priorities=TARGET_PRIORITIES, mode="new")
        db = get_db()
        db.execute(
            """
            INSERT INTO target_companies (
                name, domain, country, ats_provider, ats_slug, careers_url,
                priority, notes, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_data["name"],
                target_data["domain"],
                target_data["country"],
                target_data["ats_provider"],
                target_data["ats_slug"],
                target_data["careers_url"],
                target_data["priority"],
                target_data["notes"],
                target_data["enabled"],
            ),
        )
        db.commit()
        flash("Empresa objetivo agregada.", "success")
        return redirect(url_for("main.targets"))

    return render_template(
        "target_form.html",
        target={
            "name": "",
            "domain": "",
            "country": "",
            "ats_provider": "greenhouse",
            "ats_slug": "",
            "careers_url": "",
            "priority": "medium",
            "notes": "",
            "enabled": 1,
        },
        providers=ATS_PROVIDERS,
        priorities=TARGET_PRIORITIES,
        mode="new",
    )


@bp.route("/targets/<int:target_id>", methods=("GET", "POST"))
@login_required
def target_detail(target_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM target_companies WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        abort(404)

    if request.method == "POST":
        target_data = _target_form_data()
        if not target_data["name"]:
            flash("La empresa objetivo necesita nombre.", "error")
        else:
            db.execute(
                """
                UPDATE target_companies
                SET name = ?,
                    domain = ?,
                    country = ?,
                    ats_provider = ?,
                    ats_slug = ?,
                    careers_url = ?,
                    priority = ?,
                    notes = ?,
                    enabled = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    target_data["name"],
                    target_data["domain"],
                    target_data["country"],
                    target_data["ats_provider"],
                    target_data["ats_slug"],
                    target_data["careers_url"],
                    target_data["priority"],
                    target_data["notes"],
                    target_data["enabled"],
                    target_id,
                ),
            )
            db.commit()
            flash("Empresa objetivo actualizada.", "success")
            return redirect(url_for("main.target_detail", target_id=target_id))

    target = _serialize_target(row, opportunities_generated=_target_opportunity_count(db, row))
    opportunities = [_serialize_opportunity(item) for item in _target_opportunity_rows(db, row, limit=20)]
    _attach_outreach_summary(db, opportunities)
    return render_template(
        "target_detail.html",
        target=target,
        opportunities=opportunities,
        providers=ATS_PROVIDERS,
        priorities=TARGET_PRIORITIES,
    )


@bp.route("/targets/<int:target_id>/scan", methods=("POST",))
@login_required
def target_scan(target_id: int):
    db = get_db()
    target = db.execute("SELECT * FROM target_companies WHERE id = ?", (target_id,)).fetchone()
    if target is None:
        abort(404)
    provider = str(target["ats_provider"] or "").strip().lower()
    connector = connector_for_provider(provider)
    if connector is None or provider == "unknown":
        flash("Configura un provider ATS valido antes de escanear.", "error")
        return redirect(url_for("main.target_detail", target_id=target_id))
    if not target["ats_slug"]:
        flash("Configura el slug/subdominio ATS antes de escanear.", "error")
        return redirect(url_for("main.target_detail", target_id=target_id))

    result = connector.fetch(_target_scan_config(target))
    db.execute(
        "UPDATE target_companies SET last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (target_id,),
    )
    if result.status in ERROR_STATUSES:
        db.commit()
        flash(f"Scan controlado sin importar: {result.error_message or result.status}", "error")
        return redirect(url_for("main.target_detail", target_id=target_id))

    stats = {"created": 0, "updated": 0}
    if result.opportunities:
        db.execute(
            "UPDATE target_companies SET last_signal_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (target_id,),
        )
        stats = import_opportunities(
            db,
            source_key=TARGET_SOURCE_KEYS[provider],
            source_label=TARGET_SOURCE_LABELS[provider],
            items=result.opportunities,
            profile=_get_profile(db, g.user["id"]),
            trigger="target_scan",
        )
    else:
        db.commit()

    flash(
        f"Scan completado: {result.raw_count} puestos, {stats.get('created', 0)} nuevas, {stats.get('updated', 0)} actualizadas.",
        "success",
    )
    return redirect(url_for("main.target_detail", target_id=target_id))


@bp.route("/targets/<int:target_id>/toggle", methods=("POST",))
@login_required
def target_toggle(target_id: int):
    db = get_db()
    target = db.execute("SELECT enabled FROM target_companies WHERE id = ?", (target_id,)).fetchone()
    if target is None:
        abort(404)
    db.execute(
        "UPDATE target_companies SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (0 if target["enabled"] else 1, target_id),
    )
    db.commit()
    flash("Estado de empresa objetivo actualizado.", "success")
    return redirect(url_for("main.targets"))


@bp.route("/accounts")
@login_required
def accounts():
    db = get_db()
    _refresh_buyer_accounts_light(db)
    rows = db.execute(
        """
        SELECT *
        FROM buyer_accounts
        WHERE opportunity_count > 0
        ORDER BY
            CASE account_tier
                WHEN 'hot' THEN 1
                WHEN 'warm' THEN 2
                WHEN 'nurture' THEN 3
                WHEN 'cold' THEN 4
                ELSE 5
            END,
            account_score DESC,
            opportunity_count DESC,
            name ASC
        LIMIT 80
        """
    ).fetchall()
    duplicate_count = db.execute(
        "SELECT COUNT(*) AS total FROM buyer_account_duplicate_candidates WHERE status = 'open'"
    ).fetchone()["total"]
    return render_template(
        "accounts.html",
        accounts=[_serialize_account(row) for row in rows],
        duplicate_count=duplicate_count,
    )


@bp.route("/accounts/duplicates")
@login_required
def account_duplicates():
    db = get_db()
    _refresh_buyer_accounts_light(db)
    candidates = list_duplicate_candidates(db)
    return render_template("account_duplicates.html", candidates=candidates)


@bp.route("/accounts/duplicates/<int:candidate_id>/ignore", methods=("POST",))
@login_required
def account_duplicate_ignore(candidate_id: int):
    db = get_db()
    try:
        ignore_duplicate_candidate(db, candidate_id=candidate_id)
    except LookupError:
        abort(404)
    flash("Duplicado ignorado.", "success")
    return redirect(url_for("main.account_duplicates"))


@bp.route("/accounts/<int:account_id>")
@login_required
def account_detail(account_id: int):
    db = get_db()
    try:
        account = _serialize_account(recalculate_buyer_account(db, account_id))
        detect_possible_duplicates(db)
        db.commit()
    except LookupError:
        abort(404)

    opportunity_rows = db.execute(
        """
        SELECT *
        FROM opportunities
        WHERE buyer_account_id = ?
        ORDER BY score_total DESC, COALESCE(posted_at, created_at) DESC, id DESC
        """,
        (account_id,),
    ).fetchall()
    opportunities = [_serialize_opportunity(row) for row in opportunity_rows]
    _attach_outreach_summary(db, opportunities)
    ats_signals = [
        item
        for item in opportunities
        if item.get("source_key") in {"greenhouse_jobs", "lever_postings", "ashby_jobs", "workable_jobs"}
    ]
    commercial_activities = db.execute(
        """
        SELECT a.*, o.title AS opportunity_title, u.email AS actor_email
        FROM commercial_activities a
        JOIN opportunities o ON o.id = a.opportunity_id
        LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE o.buyer_account_id = ?
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT 40
        """,
        (account_id,),
    ).fetchall()
    account_activities = db.execute(
        """
        SELECT aa.*, u.email AS actor_email
        FROM account_activities aa
        LEFT JOIN users u ON u.id = aa.actor_user_id
        WHERE aa.buyer_account_id = ?
        ORDER BY aa.created_at DESC, aa.id DESC
        LIMIT 20
        """,
        (account_id,),
    ).fetchall()
    duplicate_candidates = [
        candidate
        for candidate in list_duplicate_candidates(db)
        if candidate["account_a_id"] == account_id or candidate["account_b_id"] == account_id
    ]
    return render_template(
        "account_detail.html",
        account=account,
        opportunities=opportunities,
        ats_signals=ats_signals,
        commercial_activities=commercial_activities,
        account_activities=account_activities,
        duplicate_candidates=duplicate_candidates,
    )


@bp.route("/accounts/<int:account_id>/merge", methods=("POST",))
@login_required
def account_merge(account_id: int):
    source_account_id = _safe_int(request.form.get("source_account_id"))
    if not source_account_id:
        flash("Selecciona una cuenta origen para fusionar.", "error")
        return redirect(url_for("main.account_detail", account_id=account_id))
    try:
        merge_buyer_accounts(
            get_db(),
            target_account_id=account_id,
            source_account_id=source_account_id,
            actor_user_id=g.user["id"],
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except LookupError:
        abort(404)
    else:
        flash("Cuenta fusionada y metricas recalculadas.", "success")
    return redirect(url_for("main.account_detail", account_id=account_id))


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
    items = _apply_quality_visibility(_sort_items([_serialize_opportunity(row) for row in rows]), filters)
    items = _apply_quick_filter(items, filters)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "titulo",
            "empresa",
            "comprador",
            "tipo_fuente",
            "fuente",
            "canal_aplicacion",
            "score",
            "tier",
            "score_dinero",
            "score_fit",
            "score_urgencia",
            "score_contacto",
            "prioridad_real",
            "estado",
            "deadline",
            "sector",
            "fit_ia",
            "accion_ia",
            "paso_siguiente",
            "presupuesto",
            "riesgo",
            "qualityStage",
            "commercialIntentLabel",
            "commercialIntentScore",
            "riskLevel",
            "riskScore",
            "riskReasons",
            "buyerConfidence",
            "budgetConfidence",
            "sourceTrustScore",
            "rejectionReasons",
            "duplicateClusterId",
            "duplicateCount",
            "isContactable",
            "url",
        ]
    )

    for item in items:
        quality = item["quality"]
        writer.writerow(
            [
                item["id"],
                item["title"],
                item["company"],
                item.get("buyer_name", ""),
                item.get("source_type", ""),
                item["source_label"],
                item["application_host"],
                item["score_total"] or item["score"],
                item["priority_tier"],
                item["score_breakdown"]["money"],
                item["score_breakdown"]["fit"],
                item["score_breakdown"]["urgency"],
                item["score_breakdown"]["contactability"],
                item["priority_label"],
                item["state"],
                item.get("deadline_at") or "",
                item["sector"],
                item["analysis"].get("fit_label", ""),
                item["analysis"].get("recommended_action", ""),
                item.get("next_best_action") or item["action_summary"],
                item["budget_display"],
                item["risk_level"],
                quality["quality_stage"],
                quality["commercial_intent_label"],
                quality["commercial_intent_score"],
                quality["risk_level"],
                quality["risk_score"],
                "; ".join(quality.get("risk_reasons") or []),
                quality["buyer_confidence"],
                quality["budget_confidence"],
                quality["source_trust_score"],
                "; ".join(quality.get("rejection_reasons") or []),
                quality.get("duplicate_cluster_id", ""),
                quality.get("duplicate_count", 0),
                "yes" if quality.get("is_contactable") else "no",
                item["url"],
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=loto-signal-opportunities.csv"},
    )


@bp.route("/exports/quality-audit.csv")
@login_required
def export_quality_audit_csv():
    db = get_db()
    rows = _fetch_opportunities(db, _blank_filters(), limit=None)
    items = _sort_items([_serialize_opportunity(row) for row in rows])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "title",
            "source",
            "url",
            "qualityStage",
            "grade",
            "finalScore",
            "commercialIntentLabel",
            "commercialIntentScore",
            "riskLevel",
            "riskScore",
            "riskReasons",
            "buyerName",
            "buyerConfidence",
            "budgetRawEvidence",
            "budgetValid",
            "budgetConfidence",
            "sourceTrustScore",
            "authorTrustScore",
            "domainTrustScore",
            "duplicateClusterId",
            "duplicateCount",
            "isContactable",
            "explanationHeadline",
            "nextBestAction",
            "rejectionReasons",
            "createdAt",
        ]
    )
    for item in items:
        quality = item["quality"]
        budget = quality.get("budget") or {}
        explanation = item.get("explanation") or {}
        writer.writerow(
            [
                item["id"],
                item["title"],
                item.get("source_label") or item.get("source_key"),
                item["url"],
                quality.get("quality_stage"),
                quality.get("grade") or item.get("priority_tier"),
                quality.get("quality_score"),
                quality.get("commercial_intent_label"),
                quality.get("commercial_intent_score"),
                quality.get("risk_level"),
                quality.get("risk_score"),
                "; ".join(quality.get("risk_reasons") or []),
                item.get("buyer_name") or item.get("company"),
                quality.get("buyer_confidence"),
                budget.get("raw_evidence", ""),
                "yes" if budget.get("is_valid_commercial_budget") else "no",
                quality.get("budget_confidence"),
                quality.get("source_trust_score"),
                quality.get("author_trust_score"),
                quality.get("domain_trust_score"),
                quality.get("duplicate_cluster_id", ""),
                quality.get("duplicate_count", 0),
                "yes" if quality.get("is_contactable") else "no",
                explanation.get("headline", ""),
                explanation.get("next_best_action", item.get("next_best_action", "")),
                "; ".join(quality.get("rejection_reasons") or []),
                item.get("created_at", ""),
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=loto-signal-quality-audit.csv"},
    )


def _run_commercial_action(opportunity_id: int, action: str, **kwargs) -> dict:
    db = get_db()
    try:
        updated = apply_commercial_action(
            db,
            opportunity_id=opportunity_id,
            actor_user_id=g.user["id"] if g.user else None,
            action=action,
            **kwargs,
        )
    except ValueError as exc:
        flash(str(exc), "error")
        return {}
    except LookupError:
        abort(404)

    flash("Movimiento comercial guardado.", "success")
    return updated


def _commercial_redirect(opportunity_id: int):
    next_url = request.form.get("next", "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("main.opportunity_detail", opportunity_id=opportunity_id))


def _optional_int(value) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _target_form_data() -> dict:
    provider = request.form.get("ats_provider", "unknown").strip().lower()
    priority = request.form.get("priority", "medium").strip().lower()
    return {
        "name": request.form.get("name", "").strip(),
        "domain": _normalize_target_domain(request.form.get("domain", "")),
        "country": request.form.get("country", "").strip(),
        "ats_provider": provider if provider in ATS_PROVIDERS else "unknown",
        "ats_slug": request.form.get("ats_slug", "").strip(),
        "careers_url": request.form.get("careers_url", "").strip(),
        "priority": priority if priority in TARGET_PRIORITIES else "medium",
        "notes": request.form.get("notes", "").strip(),
        "enabled": 1 if request.form.get("enabled") else 0,
    }


def _target_scan_config(target) -> dict:
    provider = str(target["ats_provider"] or "").strip().lower()
    return {
        "target_company": dict(target),
        "timeout_seconds": current_app.config.get("CONNECTOR_TIMEOUT_SECONDS", current_app.config.get("REQUEST_TIMEOUT_SECONDS", 15)),
        "max_results": current_app.config.get("CONNECTOR_MAX_RESULTS_PER_SOURCE", 50),
        "max_jobs_per_company": current_app.config.get("CONNECTOR_MAX_RESULTS_PER_SOURCE", 50),
        "user_agent": current_app.config.get("INGEST_USER_AGENT"),
        "api_token": current_app.config.get("WORKABLE_API_TOKEN") if provider == "workable" else None,
        "enabled": True,
    }


def _target_signal_rows(db, *, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT tc.*,
               COUNT(o.id) AS opportunities_generated
        FROM target_companies tc
        LEFT JOIN opportunities o
          ON o.source_key IN ('greenhouse_jobs', 'lever_postings', 'ashby_jobs', 'workable_jobs')
         AND (
              (tc.domain <> '' AND LOWER(o.buyer_domain) = LOWER(tc.domain))
              OR (tc.domain = '' AND LOWER(o.buyer_name) = LOWER(tc.name))
         )
        GROUP BY tc.id
        ORDER BY
            CASE tc.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            COALESCE(tc.last_signal_at, '') DESC,
            tc.name ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_serialize_target(row, opportunities_generated=row["opportunities_generated"]) for row in rows]


def _serialize_target(row, *, opportunities_generated: int = 0) -> dict:
    target = dict(row)
    target["enabled_label"] = "Activa" if target.get("enabled") else "Pausada"
    target["opportunities_generated"] = int(opportunities_generated or 0)
    target["last_checked_display"] = _datetime_display(target.get("last_checked_at")) if target.get("last_checked_at") else "Sin scan"
    target["last_signal_display"] = _datetime_display(target.get("last_signal_at")) if target.get("last_signal_at") else "Sin senal"
    target["provider_label"] = str(target.get("ats_provider") or "unknown").title()
    target["priority_badge_class"] = {
        "high": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
        "medium": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
        "low": "border border-slate-700 bg-slate-900 text-slate-300",
    }.get(str(target.get("priority") or "").lower(), "border border-slate-700 bg-slate-900 text-slate-300")
    return target


def _target_opportunity_rows(db, target, *, limit: int):
    domain = str(target["domain"] or "").strip()
    name = str(target["name"] or "").strip()
    if domain:
        return db.execute(
            """
            SELECT *
            FROM opportunities
            WHERE source_key IN ('greenhouse_jobs', 'lever_postings', 'ashby_jobs', 'workable_jobs')
              AND LOWER(buyer_domain) = LOWER(?)
            ORDER BY COALESCE(posted_at, created_at) DESC, score_total DESC
            LIMIT ?
            """,
            (domain, limit),
        ).fetchall()
    return db.execute(
        """
        SELECT *
        FROM opportunities
        WHERE source_key IN ('greenhouse_jobs', 'lever_postings', 'ashby_jobs', 'workable_jobs')
          AND LOWER(buyer_name) = LOWER(?)
        ORDER BY COALESCE(posted_at, created_at) DESC, score_total DESC
        LIMIT ?
        """,
        (name, limit),
    ).fetchall()


def _target_opportunity_count(db, target) -> int:
    return len(_target_opportunity_rows(db, target, limit=500))


def _normalize_target_domain(value: str) -> str:
    parsed = urlparse(str(value or "").strip().lower())
    host = parsed.netloc or parsed.path
    return host.replace("www.", "").split("/", 1)[0].strip()


def _attach_outreach_summary(db, items: list[dict]) -> None:
    if not items:
        return
    item_ids = [int(item["id"]) for item in items if item.get("id")]
    if not item_ids:
        return
    placeholders = ", ".join("?" for _ in item_ids)
    rows = db.execute(
        f"""
        SELECT *
        FROM outreach_drafts
        WHERE is_active = 1 AND opportunity_id IN ({placeholders})
        ORDER BY opportunity_id ASC, id ASC
        """,
        tuple(item_ids),
    ).fetchall()
    by_opportunity: dict[int, dict[str, dict]] = {}
    for row in rows:
        draft = serialize_outreach_draft(row)
        by_type = by_opportunity.setdefault(int(draft["opportunity_id"]), {})
        by_type.setdefault(draft["draft_type"], draft)

    for item in items:
        drafts = by_opportunity.get(int(item["id"]), {})
        followup_type = recommended_followup_type(item.get("followup_count"))
        primary = drafts.get("initial")
        followup = drafts.get(followup_type)
        first_draft = next(iter(drafts.values()), None)
        item["outreach_drafts"] = drafts
        item["outreach_initial"] = primary
        item["outreach_followup"] = followup
        item["outreach_recommended_channel"] = (primary or first_draft or {}).get("channel", "sin draft")
        item["has_outreach_drafts"] = bool(drafts)


def _drafts_by_type(drafts: list[dict]) -> dict[str, dict]:
    by_type: dict[str, dict] = {}
    for draft in drafts:
        by_type.setdefault(draft["draft_type"], draft)
    return by_type


def _refresh_buyer_accounts_light(db) -> None:
    assigned = assign_missing_buyer_accounts(db, limit=200)
    account_rows = db.execute(
        """
        SELECT DISTINCT buyer_account_id AS id
        FROM opportunities
        WHERE buyer_account_id IS NOT NULL
        ORDER BY buyer_account_id ASC
        LIMIT 200
        """
    ).fetchall()
    for row in account_rows:
        recalculate_buyer_account(db, int(row["id"]))
    detect_possible_duplicates(db)
    if assigned:
        db.commit()


def _hot_accounts(db, *, limit: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT *
        FROM buyer_accounts
        WHERE opportunity_count > 0
          AND account_tier IN ('hot', 'warm')
          AND LOWER(name) NOT IN ('conspiracy', 'nosleep', 'unknown reddit user', 'reddit')
          AND COALESCE(normalized_domain, '') NOT IN ('reddit.com', 'old.reddit.com', 'www.reddit.com')
        ORDER BY
            CASE account_tier WHEN 'hot' THEN 1 ELSE 2 END,
            account_score DESC,
            a1_count DESC,
            a2_count DESC,
            opportunity_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_serialize_account(row) for row in rows]


def _serialize_account(row) -> dict:
    account = dict(row)
    account["primary_pain_signals"] = _json_list(account.get("primary_pain_signals_json"))
    account["required_skills"] = _json_list(account.get("required_skills_json"))
    account["contact_signals"] = _json_list(account.get("contact_signals_json"))
    account["evidence_summary"] = _json_list(account.get("evidence_summary_json"))
    account["estimated_value_display"] = _format_money_value(account.get("total_estimated_value"), "USD")
    account["proposal_value_display"] = _format_money_value(account.get("total_proposal_value"), "USD")
    account["won_value_display"] = _format_money_value(account.get("total_won_value"), "USD")
    account["last_contacted_display"] = _datetime_display(account.get("last_contacted_at")) if account.get("last_contacted_at") else "Sin contacto"
    account["next_followup_display"] = _date_display(account.get("next_followup_at"))
    account["tier_badge_class"] = _account_tier_class(account.get("account_tier"))
    account["next_best_action"] = _account_next_action(account)
    return account


def _account_tier_class(tier) -> str:
    return {
        "hot": "border border-emerald-500/30 bg-emerald-500/15 text-emerald-200",
        "warm": "border border-blue-500/30 bg-blue-500/15 text-blue-200",
        "nurture": "border border-amber-500/30 bg-amber-500/15 text-amber-200",
        "cold": "border border-slate-700 bg-slate-900 text-slate-300",
        "noisy": "border border-rose-500/30 bg-rose-500/15 text-rose-200",
    }.get(str(tier or "").lower(), "border border-slate-700 bg-slate-900 text-slate-300")


def _account_next_action(account: dict) -> str:
    tier = str(account.get("account_tier") or "").lower()
    status = str(account.get("commercial_status") or "").lower()
    if tier == "hot" and status in {"new", "reviewed"}:
        return "Contactar con enfoque consultivo y señales acumuladas."
    if status == "proposal":
        return "Seguir propuesta abierta."
    if account.get("next_followup_at"):
        return "Dar seguimiento a cuenta."
    if tier == "noisy":
        return "Revisar ruido antes de perseguir."
    if tier == "warm":
        return "Validar comprador y preparar siguiente contacto."
    return "Mantener en observacion."


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
        "quick": request.args.get("quick", "").strip(),
        "range": request.args.get("range", "30d" if not has_query else "all").strip() or "all",
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
        "quick": "",
        "range": "all",
        "min_budget": "",
    }


def _fetch_opportunities(db, filters: dict, *, limit: int | None):
    query = "SELECT * FROM opportunities WHERE 1 = 1"
    params: list = []

    if filters["q"]:
        like_value = f"%{filters['q']}%"
        query += " AND (title LIKE ? OR company LIKE ? OR buyer_name LIKE ? OR ai_summary LIKE ? OR raw_text LIKE ?)"
        params.extend([like_value, like_value, like_value, like_value, like_value])
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
    if filters.get("range") and filters["range"] != "all":
        days = {"7d": 7, "30d": 30, "90d": 90}.get(filters["range"], 30)
        cutoff = (datetime.now(UTC) - timedelta(days=days)).replace(microsecond=0).isoformat()
        query += " AND COALESCE(posted_at, created_at) >= ?"
        params.append(cutoff)

    query += " ORDER BY score DESC, COALESCE(posted_at, created_at) DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    return db.execute(query, tuple(params)).fetchall()


def _serialize_opportunity(row) -> dict:
    item = dict(row)
    item["stack"] = _json_list(item.get("stack"))
    item["required_skills"] = _json_list(item.get("required_skills"))
    item["pain_signals"] = _json_list(item.get("pain_signals"))
    item["contact_signals"] = _json_list(item.get("contact_signals"))
    item["evidence_snippets"] = _json_list(item.get("evidence_snippets"))
    item["risk_reasons"] = _json_list(item.get("risk_reasons"))
    item["score_reasons"] = _json_list(item.get("score_reasons"))
    item["analysis"] = _json_dict(item.get("analysis_json"))
    item["quality"] = _quality_payload(item)
    item["explanation"] = explain_opportunity(item).to_dict()
    item["budget_display"] = _format_budget(item)
    item["estimated_value_display"] = _format_estimated_value(item)
    item["state_badge_class"] = STATE_BADGE_STYLES.get(item["state"], "border border-slate-600 bg-slate-800 text-slate-300")
    item["risk_badge_class"] = RISK_BADGE_STYLES.get(item["risk_level"], "border border-slate-600 bg-slate-800 text-slate-300")
    item["action_label"] = _action_label(item["analysis"].get("recommended_action"))
    item["action_summary"] = _action_summary(item["analysis"].get("recommended_action"))
    item["fit_label_display"] = _fit_label_display(item["analysis"].get("fit_label"))
    item["application_host"] = _application_host(item["url"])
    item["posted_display"] = _posted_display(item.get("posted_at"), item.get("created_at"))
    item["deadline_display"] = _date_display(item.get("deadline_at"))
    item["next_followup_display"] = _date_display(item.get("next_followup_at"))
    item["last_contacted_display"] = _datetime_display(item.get("last_contacted_at")) if item.get("last_contacted_at") else "Sin contacto"
    item["snoozed_until_display"] = _date_display(item.get("snoozed_until"))
    item["proposal_value_display"] = _format_money_value(item.get("proposal_value"), item.get("currency"))
    item["won_value_display"] = _format_money_value(item.get("won_value"), item.get("currency"))
    item["commercial_status"] = str(item.get("commercial_status") or "new").strip().lower() or "new"
    item["commercial_status_label"] = STATUS_LABELS.get(item["commercial_status"], item["commercial_status"])
    item["is_snoozed_active"] = _is_snoozed_active(item)
    item["is_followup_due"] = _is_followup_due(item)
    item["freshness_label"] = _freshness_label(item.get("posted_at"), item.get("created_at"))
    item["score_breakdown"] = {
        "money": int(item.get("score_money") or 0),
        "fit": int(item.get("score_fit") or 0),
        "urgency": int(item.get("score_urgency") or 0),
        "contactability": int(item.get("score_contactability") or 0),
        "confidence": int(item.get("score_confidence") or 0),
    }
    item.update(_priority_profile(item))
    item["intelligence"] = _opportunity_intelligence(item)
    item["canonical_state"] = LEGACY_TO_CANONICAL.get(str(item.get("state") or "").upper(), "new")
    item["available_transitions"] = available_transitions(item["canonical_state"], item)
    return item


def _opportunity_intelligence(item: dict) -> dict:
    quality = item.get("quality") or _quality_payload(item)
    score = score_v2({**item, "quality": quality})
    risk = assess_risk(item)
    payload = {
        **item,
        "quality": quality,
        "grade": score.grade,
        "score_v2": score.numeric_score,
        "scam_risk": risk.scam_risk,
    }
    nba = recommend_next_best_action(payload)
    outreach = compose_outreach(payload)
    evidence = collect_evidence(payload)
    return {
        "score": score.to_dict(),
        "next_best_action": nba.to_dict(),
        "evidence": evidence,
        "risk": risk.to_dict(),
        "outreach_preview": outreach.to_dict(),
        "copilot_modes": {
            "evaluar": copilot_mode(payload, mode="evaluar").to_dict(),
            "investigar": copilot_mode(payload, mode="investigar").to_dict(),
            "redactar": copilot_mode(payload, mode="redactar").to_dict(),
        },
    }


def _quality_payload(item: dict) -> dict:
    quality = item.get("analysis", {}).get("quality") if isinstance(item.get("analysis"), dict) else None
    if isinstance(quality, dict):
        return quality
    return assess_opportunity_quality(item).to_dict()


def _apply_quality_visibility(items: list[dict], filters: dict) -> list[dict]:
    quick = str(filters.get("quick") or "").strip()
    if quick in {"rejected_noise", "duplicates", "reddit_hidden", "suspect", "budget_dubious", "no_buyer"}:
        return items
    return [
        item
        for item in items
        if item["quality"]["quality_stage"] not in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}
    ]


def _apply_quick_filter(items: list[dict], filters: dict) -> list[dict]:
    quick = str(filters.get("quick") or "").strip()
    if not quick:
        return items
    now = datetime.now(UTC)
    if quick == "high_intent":
        return [item for item in items if item["intelligence"]["score"]["grade"] in {"A1", "A2"}]
    if quick == "budget_visible":
        return [item for item in items if _budget_anchor(item) > 0]
    if quick == "uncontacted":
        return [item for item in items if item.get("commercial_status") in {"new", "reviewed", ""}]
    if quick == "deadline_close":
        return [
            item
            for item in items
            if (deadline := _parse_datetime(item.get("deadline_at"))) and 0 <= (deadline - now).days <= 14
        ]
    if quick == "low_risk":
        return [item for item in items if item.get("risk_level") == "low" and not item["intelligence"]["risk"]["warnings"]]
    if quick == "strong_evidence":
        return [item for item in items if len(item["intelligence"]["evidence"]) >= 3]
    if quick == "possible_duplicates":
        return [item for item in items if item.get("buyer_account_id") and item.get("buyer_name")]
    if quick == "contactable":
        return [item for item in items if item["quality"]["is_contactable"]]
    if quick == "review_required":
        return [item for item in items if item["quality"]["quality_stage"] == "REVIEW_REQUIRED"]
    if quick == "suspect":
        return [item for item in items if item["quality"]["quality_stage"] == "SUSPECT"]
    if quick == "rejected_noise":
        return [item for item in items if item["quality"]["quality_stage"] == "REJECTED_NOISE"]
    if quick == "duplicates":
        return [item for item in items if item["quality"]["quality_stage"] == "DUPLICATE" or item["quality"].get("duplicate_count")]
    if quick == "reddit_trusted":
        return [
            item
            for item in items
            if item.get("source_key") == "reddit"
            and item["quality"]["quality_stage"] in {"READY_TO_CONTACT", "REVIEW_REQUIRED", "WATCHLIST"}
            and item["quality"]["source_trust_score"] >= 45
        ]
    if quick == "reddit_hidden":
        return [
            item
            for item in items
            if item.get("source_key") == "reddit"
            and item["quality"]["quality_stage"] in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}
        ]
    if quick == "budget_dubious":
        return [item for item in items if not item["quality"]["budget"]["is_valid_commercial_budget"]]
    if quick == "no_buyer":
        return [item for item in items if item["quality"]["buyer_confidence"] < 50]
    return items


def _format_budget(item: dict) -> str:
    quality = item.get("quality") or {}
    budget_quality = quality.get("budget") or {}
    if budget_quality and not budget_quality.get("is_valid_commercial_budget"):
        return "Presupuesto dudoso" if budget_quality.get("raw_evidence") else "No especificado"
    if item["budget_text"]:
        return item["budget_text"]
    if item["budget_min"] and item["budget_max"]:
        return f"{item['currency']} {item['budget_min']:,} - {item['budget_max']:,}"
    if item["budget_min"]:
        return f"{item['currency']} {item['budget_min']:,}"
    if item["budget_max"]:
        return f"{item['currency']} {item['budget_max']:,}"
    return "No especificado"


def _format_estimated_value(item: dict) -> str:
    quality = item.get("quality") or {}
    budget_quality = quality.get("budget") or {}
    if budget_quality and not budget_quality.get("is_valid_commercial_budget"):
        return "Sin valor claro"
    value = item.get("estimated_value") or item.get("budget_max") or item.get("budget_min")
    if not value:
        return "Sin valor claro"
    try:
        return f"{item.get('currency') or 'USD'} {int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _format_money_value(value, currency="USD") -> str:
    if value in (None, ""):
        return "Sin valor"
    try:
        return f"{currency or 'USD'} {int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _is_snoozed_active(item: dict) -> bool:
    if str(item.get("commercial_status") or "").lower() != "snoozed":
        return False
    parsed = _parse_datetime(item.get("snoozed_until") or item.get("next_followup_at"))
    return bool(parsed and parsed > datetime.now(UTC))


def _is_followup_due(item: dict) -> bool:
    if _is_commercially_closed(item) or _is_snoozed_active(item):
        return False
    parsed = _parse_datetime(item.get("next_followup_at"))
    return bool(parsed and parsed <= datetime.now(UTC))


def _is_commercially_closed(item: dict) -> bool:
    return str(item.get("commercial_status") or "").strip().lower() in {"won", "lost", "ignored"}


def _json_list(value) -> list:
    parsed = _json_value(value, default=[])
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, str) and parsed.strip():
        return [parsed.strip()]
    return []


def _json_dict(value) -> dict:
    parsed = _json_value(value, default={})
    return parsed if isinstance(parsed, dict) else {}


def _json_value(value, *, default):
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truthy_arg(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _policy_config(policy_row) -> dict:
    if policy_row is None:
        return {}
    try:
        parsed = json.loads(policy_row["config_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _split_rule_list(value) -> list[str]:
    if not value:
        return []
    parts = []
    for chunk in str(value).replace(",", "\n").splitlines():
        cleaned = chunk.strip().lower().replace("r/", "")
        if cleaned:
            parts.append(cleaned)
    return list(dict.fromkeys(parts))


def _golden_dataset_status() -> dict:
    fixture_path = Path(current_app.root_path).parent / "tests" / "fixtures" / "opportunity_quality_golden.json"
    if not fixture_path.exists():
        return {"exists": False, "total": 0, "path": str(fixture_path)}
    try:
        cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"exists": True, "total": 0, "path": str(fixture_path), "error": "invalid_json"}
    return {"exists": True, "total": len(cases) if isinstance(cases, list) else 0, "path": str(fixture_path)}


def _authorize_internal_request():
    cron_secret = str(current_app.config.get("CRON_SECRET", "") or "").strip()
    if not cron_secret:
        return jsonify({"ok": False, "error": "CRON_SECRET is not configured."}), 503

    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    expected = f"Bearer {cron_secret}"
    if not secrets.compare_digest(auth_header, expected):
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
    base_url = str(
        current_app.config.get("LOTO_SIGNAL_BASE_URL")
        or current_app.config.get("SCRAPPERLANAS_BASE_URL", "")
        or ""
    ).strip().rstrip("/")
    import_path = str(current_app.config.get("N8N_IMPORT_WEBHOOK_PATH", "") or "").strip().strip("/")
    scheduler_interval = max(1, int(current_app.config.get("N8N_SCHEDULER_INTERVAL_MINUTES", 15) or 15))
    workflows = (
        {
            "name": "Loto Signal Scheduler",
            "filename": "scrapperlanas_scheduler.json",
            "purpose": "Dispara la ingesta due-only sin depender del boton del dashboard.",
        },
        {
            "name": "Loto Signal Import Webhook",
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
        "github.com": "GitHub issue",
        "sam.gov": "SAM.gov",
        "api.sam.gov": "SAM.gov API",
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


def _date_display(value) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Sin deadline"
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
    commercial_tier = str(item.get("score_tier") or "").strip().upper()
    commercial_score = int(item.get("score_total") or 0)
    if commercial_tier in PRIORITY_LABELS and commercial_score > 0:
        reasons = item.get("score_reasons") or []
        reason = reasons[0] if reasons else "Score comercial calculado con dinero, fit, urgencia, contacto y confianza."
        state = str(item.get("state") or "").strip()
        return {
            "priority_score": max(0, min(commercial_score, 100)),
            "priority_tier": commercial_tier,
            "priority_label": PRIORITY_LABELS[commercial_tier],
            "priority_badge_class": PRIORITY_STYLES[commercial_tier],
            "priority_reason": reason,
            "is_focus_candidate": (
                commercial_tier in {"A1", "A2"}
                and state not in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
                and not _is_commercially_closed(item)
                and not item.get("is_snoozed_active")
                and item.get("risk_level") != "high"
                and item.get("quality", {}).get("is_contactable", False)
            ),
        }

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
    if _is_commercially_closed(item) or item.get("is_snoozed_active"):
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
            and not _is_commercially_closed(item)
            and not item.get("is_snoozed_active")
            and not management_role
            and not noncore_role
            and item.get("quality", {}).get("is_contactable", False)
        ),
    }


def _focus_items(items: list[dict]) -> list[dict]:
    candidates = [
        item
        for item in items
        if item.get("is_focus_candidate") and item.get("state") not in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
        and item.get("quality", {}).get("is_contactable")
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            -int(item.get("score") or 0),
            item.get("title", ""),
        )
    )
    return candidates[:8]


def _tier_items(items: list[dict], *, tier: str, limit: int) -> list[dict]:
    blocked_states = {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
    candidates = [
        item
        for item in items
        if item.get("priority_tier") == tier
        and item.get("state") not in blocked_states
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
        and item.get("commercial_status") in {"new", "reviewed"}
        and item.get("quality", {}).get("is_contactable")
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("priority_score") or 0),
            item.get("deadline_at") or "9999",
            item.get("title", ""),
        )
    )
    return candidates[:limit]


def _daily_plan(items: list[dict]) -> dict:
    active_items = [
        item
        for item in items
        if item.get("state") not in {"APLICADO", "GANADO", "PERDIDO", "DESCARTADO"}
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
    ]
    return {
        "apply_now": sum(1 for item in active_items if item.get("analysis", {}).get("recommended_action") == "apply_now"),
        "review_today": sum(1 for item in active_items if item.get("analysis", {}).get("recommended_action") == "review_today"),
        "focus_now": sum(1 for item in active_items if item.get("priority_tier") in {"A1", "A2"} and item.get("quality", {}).get("is_contactable")),
        "fresh": sum(1 for item in active_items if item.get("freshness_label") in {"muy fresca", "reciente"}),
    }


def _sales_cockpit(items: list[dict]) -> dict:
    active_items = [
        item
        for item in items
        if item.get("state") not in {"GANADO", "PERDIDO", "DESCARTADO"}
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
    ]
    focus_items = [
        item
        for item in active_items
        if item.get("priority_tier") in {"A1", "A2"}
        and item.get("risk_level") != "high"
        and item.get("quality", {}).get("is_contactable")
    ]
    proposal_value = sum(_budget_anchor(item) for item in focus_items)
    follow_up_items = [
        item
        for item in active_items
        if item.get("state") in {"RESPONDIDO", "APLICADO", "FOLLOW_UP"}
    ]
    source_quality: dict[str, dict] = {}
    for item in active_items:
        label = item.get("source_label") or "Sin fuente"
        bucket = source_quality.setdefault(
            label,
            {"source_label": label, "total": 0, "focus": 0, "potential": 0},
        )
        bucket["total"] += 1
        if item.get("priority_tier") in {"A1", "A2"}:
            if not item.get("quality", {}).get("is_contactable"):
                continue
            bucket["focus"] += 1
            bucket["potential"] += _budget_anchor(item)

    ranked_sources = sorted(
        source_quality.values(),
        key=lambda row: (-row["focus"], -row["potential"], row["source_label"]),
    )
    return {
        "focus_count": len(focus_items),
        "proposal_value": proposal_value,
        "follow_up_count": len(follow_up_items),
        "reply_ready_count": sum(
            1
            for item in focus_items
            if item.get("suggested_reply") and item.get("analysis", {}).get("recommended_action") != "skip"
        ),
        "top_sources": ranked_sources[:4],
    }


def _quality_metrics(items: list[dict]) -> dict:
    buckets = {
        "total_ingested": len(items),
        "rejected_noise": 0,
        "blocked_subreddit": 0,
        "suspect": 0,
        "duplicates": 0,
        "contactable": 0,
        "review_required": 0,
        "watchlist": 0,
        "low_value": 0,
        "budget_dubious": 0,
        "no_buyer": 0,
        "top_rejection_reasons": [],
    }
    reason_counts: dict[str, int] = {}
    for item in items:
        quality = item.get("quality") or {}
        stage = quality.get("quality_stage")
        if stage == "REJECTED_NOISE":
            buckets["rejected_noise"] += 1
        if stage == "SUSPECT":
            buckets["suspect"] += 1
        if stage == "DUPLICATE" or quality.get("duplicate_count"):
            buckets["duplicates"] += 1
        if stage == "READY_TO_CONTACT" or quality.get("is_contactable"):
            buckets["contactable"] += 1
        if stage == "REVIEW_REQUIRED":
            buckets["review_required"] += 1
        if stage == "WATCHLIST":
            buckets["watchlist"] += 1
        if stage == "LOW_VALUE":
            buckets["low_value"] += 1
        if not (quality.get("budget") or {}).get("is_valid_commercial_budget"):
            buckets["budget_dubious"] += 1
        if int(quality.get("buyer_confidence") or 0) < 50:
            buckets["no_buyer"] += 1
        for reason in quality.get("rejection_reasons") or []:
            if reason == "blocked_subreddit":
                buckets["blocked_subreddit"] += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    buckets["top_rejection_reasons"] = sorted(
        ({"reason": reason, "total": total} for reason, total in reason_counts.items()),
        key=lambda row: (-row["total"], row["reason"]),
    )[:6]
    return buckets


def _sales_actions(items: list[dict]) -> dict:
    a1_new = [
        item
        for item in items
        if item.get("priority_tier") == "A1"
        and item.get("commercial_status") in {"new", "reviewed"}
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
        and item.get("quality", {}).get("is_contactable")
    ]
    a2_new = [
        item
        for item in items
        if item.get("priority_tier") == "A2"
        and item.get("commercial_status") in {"new", "reviewed"}
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
        and item.get("quality", {}).get("is_contactable")
    ]
    followups_due = [
        item
        for item in items
        if item.get("is_followup_due") and item.get("commercial_status") != "proposal"
    ]
    proposals_open = [
        item
        for item in items
        if item.get("commercial_status") == "proposal"
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
    ]
    proposals_due = [item for item in proposals_open if item.get("is_followup_due")]
    won_recent = [
        item
        for item in items
        if item.get("commercial_status") == "won"
    ]
    lost_recent = [
        item
        for item in items
        if item.get("commercial_status") == "lost"
    ]

    return {
        "a1_new": _sort_commercial_items(a1_new)[:8],
        "a2_new": _sort_commercial_items(a2_new)[:8],
        "followups_due": _sort_commercial_items(followups_due)[:8],
        "proposals_open": _sort_commercial_items(proposals_open)[:8],
        "proposals_due_count": len(proposals_due),
        "won_recent": _sort_recent_commercial_items(won_recent)[:5],
        "lost_recent": _sort_recent_commercial_items(lost_recent)[:5],
        "today_count": len(a1_new) + len(followups_due) + len(proposals_due),
    }


def _copilot_summary(items: list[dict], *, source_performance: list[dict]) -> dict:
    active_items = [
        item
        for item in items
        if item.get("state") not in {"GANADO", "PERDIDO", "DESCARTADO"}
        and not _is_commercially_closed(item)
        and not item.get("is_snoozed_active")
        and item.get("quality", {}).get("quality_stage") not in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}
    ]
    focus = [
        item
        for item in active_items
        if item.get("quality", {}).get("is_contactable")
        and item.get("intelligence", {}).get("score", {}).get("grade") in {"A1", "A2"}
    ]
    focus = _sort_items(focus)
    top_item = focus[0] if focus else (active_items[0] if active_items else None)
    due_deadlines = [
        item
        for item in active_items
        if (deadline := _parse_datetime(item.get("deadline_at")))
        and 0 <= (deadline - datetime.now(UTC)).days <= 14
    ]
    risk_items = [
        item
        for item in active_items
        if item.get("intelligence", {}).get("risk", {}).get("warnings")
    ]
    top_source = source_performance[0] if source_performance else None
    recommendation = top_item.get("intelligence", {}).get("next_best_action") if top_item else None
    outreach = top_item.get("intelligence", {}).get("outreach_preview") if top_item else None
    summary_parts = []
    if focus:
        summary_parts.append(f"{len(focus)} oportunidades A1/A2 piden atencion.")
    if due_deadlines:
        summary_parts.append(f"{len(due_deadlines)} deadlines estan cerca.")
    if top_source:
        summary_parts.append(f"{top_source['source_label']} lidera por contactables reales.")
    if not summary_parts:
        summary_parts.append("No hay urgencias claras; conviene ejecutar ingesta o ampliar filtros.")

    return {
        "summary": " ".join(summary_parts),
        "top_opportunity": top_item,
        "recommendation": recommendation,
        "outreach": outreach,
        "risk_items": risk_items[:4],
        "deadline_items": due_deadlines[:4],
        "top_source": top_source,
        "merge_suggestion_count": sum(1 for item in active_items if item.get("buyer_account_id") and item.get("buyer_name")),
    }


def _source_performance(items: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for item in items:
        label = item.get("source_label") or item.get("source_key") or "Sin fuente"
        status = str(item.get("commercial_status") or "new").lower()
        bucket = buckets.setdefault(
            label,
            {
                "source_label": label,
                "total": 0,
                "a1_count": 0,
                "a2_count": 0,
                "contacted_count": 0,
                "replied_count": 0,
                "proposal_count": 0,
                "won_count": 0,
                "lost_count": 0,
                "ignored_count": 0,
                "ignored_bad_fit_count": 0,
                "contactable_count": 0,
                "watchlist_count": 0,
                "rejected_noise_count": 0,
                "suspect_count": 0,
                "duplicate_count": 0,
                "trust_total": 0,
                "score_total": 0,
                "total_won_value": 0,
            },
        )
        bucket["total"] += 1
        bucket["score_total"] += int(item.get("score_total") or item.get("priority_score") or 0)
        quality = item.get("quality") or {}
        if quality.get("is_contactable"):
            bucket["contactable_count"] += 1
        if quality.get("quality_stage") == "WATCHLIST":
            bucket["watchlist_count"] += 1
        if quality.get("quality_stage") == "REJECTED_NOISE":
            bucket["rejected_noise_count"] += 1
        if quality.get("quality_stage") == "SUSPECT":
            bucket["suspect_count"] += 1
        if quality.get("quality_stage") == "DUPLICATE" or quality.get("duplicate_count"):
            bucket["duplicate_count"] += 1
        bucket["trust_total"] += int(quality.get("source_trust_score") or 0)
        grade_v2 = item.get("intelligence", {}).get("score", {}).get("grade") or item.get("priority_tier")
        if grade_v2 == "A1":
            bucket["a1_count"] += 1
        if grade_v2 == "A2":
            bucket["a2_count"] += 1
        if status in {"contacted", "followup_due", "replied", "discovery", "proposal", "won", "lost"} or item.get("last_contacted_at"):
            bucket["contacted_count"] += 1
        if status in {"replied", "discovery", "proposal", "won", "lost"}:
            bucket["replied_count"] += 1
        if status in {"proposal", "won"}:
            bucket["proposal_count"] += 1
        if status == "won":
            bucket["won_count"] += 1
            bucket["total_won_value"] += _numeric_item_value(item.get("won_value")) or _numeric_item_value(item.get("proposal_value")) or _budget_anchor(item)
        if status == "lost":
            bucket["lost_count"] += 1
        if status == "ignored":
            bucket["ignored_count"] += 1
            if item.get("ignored_reason") == "bad_fit":
                bucket["ignored_bad_fit_count"] += 1

    rows = []
    for bucket in buckets.values():
        total = max(1, bucket["total"])
        contacted = max(1, bucket["contacted_count"])
        reply_rate = bucket["replied_count"] / contacted
        proposal_rate = bucket["proposal_count"] / contacted
        win_rate = bucket["won_count"] / contacted
        ignore_rate = bucket["ignored_count"] / total
        a_focus_rate = (bucket["a1_count"] + bucket["a2_count"]) / total
        contactable_rate = bucket["contactable_count"] / total
        trash_rate = (bucket["rejected_noise_count"] + bucket["suspect_count"] + bucket["ignored_count"] + bucket["duplicate_count"]) / total
        trust_score = round(bucket["trust_total"] / total)
        health_score = max(0, min(100, round(trust_score + (contactable_rate * 35) - (trash_rate * 30))))
        classification = "Prometedora"
        if bucket["won_count"] and win_rate >= 0.15 and trash_rate < 0.4:
            classification = "Elite"
        elif trash_rate >= 0.5:
            classification = "Ruidosa"
        elif bucket["total"] >= 5 and bucket["contactable_count"] == 0:
            classification = "Muerta"

        rows.append(
            {
                **bucket,
                "avg_score": round(bucket["score_total"] / total, 1),
                "reply_rate": round(reply_rate * 100),
                "proposal_rate": round(proposal_rate * 100),
                "win_rate": round(win_rate * 100),
                "ignore_rate": round(ignore_rate * 100),
                "contactable_rate": round(contactable_rate * 100),
                "trash_rate": round(trash_rate * 100),
                "trust_score": trust_score,
                "health_score": health_score,
                "false_positive_rate": round((bucket["ignored_bad_fit_count"] / total) * 100),
                "a_focus_rate": round(a_focus_rate * 100),
                "classification": classification,
            }
        )

    rows.sort(
        key=lambda row: (
            row["classification"] not in {"Elite", "Prometedora"},
            -row["contactable_count"],
            row["trash_rate"],
            -row["won_count"],
            -row["total_won_value"],
            row["source_label"],
        )
    )
    return rows[:8]


def _sort_commercial_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            item.get("next_followup_at") or item.get("deadline_at") or "9999",
            -int(item.get("priority_score") or 0),
            item.get("title", ""),
        ),
    )


def _sort_recent_commercial_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            item.get("last_status_at") or item.get("updated_at") or item.get("created_at") or "",
            item.get("id") or 0,
        ),
        reverse=True,
    )


def _numeric_item_value(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _budget_anchor(item: dict) -> int:
    budget_quality = (item.get("quality") or {}).get("budget") or {}
    if budget_quality and not budget_quality.get("is_valid_commercial_budget"):
        return 0
    value = item.get("estimated_value") or item.get("budget_max") or item.get("budget_min") or 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
