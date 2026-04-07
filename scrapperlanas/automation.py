from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .db import get_db
from .services.pipeline import run_ingestion_for_policies


AUTOMATION_EXTENSION_KEY = "automation_runtime"


def init_app(app) -> None:
    runtime = {
        "started_at": _utcnow_iso(),
        "worker_enabled": False,
        "worker_name": None,
        "worker_alive": False,
        "poll_seconds": int(app.config["AUTOMATION_POLL_SECONDS"]),
        "last_loop_started_at": None,
        "last_loop_finished_at": None,
        "last_success_at": None,
        "last_stats": None,
        "last_error": None,
        "heartbeat_path": str(_heartbeat_path(app)),
        "log_path": str(_log_path(app)),
        "lock": threading.Lock(),
        "stop_event": threading.Event(),
        "_thread": None,
    }
    app.extensions[AUTOMATION_EXTENSION_KEY] = runtime

    _configure_logging(app)
    _write_heartbeat(app, status="booting")

    if _should_start_local_worker(app):
        thread = threading.Thread(
            target=_worker_loop,
            args=(app,),
            name="scrapperlanas-local-automation",
            daemon=True,
        )
        runtime["worker_enabled"] = True
        runtime["worker_name"] = thread.name
        runtime["_thread"] = thread
        thread.start()
        app.logger.info(
            "Local automation worker started mode=%s poll_seconds=%s",
            app.config.get("AUTOMATION_MODE"),
            runtime["poll_seconds"],
        )


def get_runtime_snapshot(app) -> dict:
    runtime = app.extensions.get(AUTOMATION_EXTENSION_KEY, {})
    thread = runtime.get("_thread")
    return {
        "started_at": runtime.get("started_at"),
        "worker_enabled": bool(runtime.get("worker_enabled")),
        "worker_name": runtime.get("worker_name"),
        "worker_alive": bool(thread.is_alive()) if thread else False,
        "poll_seconds": runtime.get("poll_seconds"),
        "last_loop_started_at": runtime.get("last_loop_started_at"),
        "last_loop_finished_at": runtime.get("last_loop_finished_at"),
        "last_success_at": runtime.get("last_success_at"),
        "last_stats": runtime.get("last_stats"),
        "last_error": runtime.get("last_error"),
        "heartbeat_path": runtime.get("heartbeat_path"),
        "log_path": runtime.get("log_path"),
    }


@contextmanager
def automation_lock(app, *, blocking: bool = True):
    runtime = app.extensions.get(AUTOMATION_EXTENSION_KEY, {})
    lock = runtime.get("lock")
    if lock is None:
        yield
        return

    acquired = lock.acquire(blocking=blocking)
    if not acquired:
        raise TimeoutError("Automation is already running.")
    try:
        yield
    finally:
        lock.release()


def _worker_loop(app) -> None:
    runtime = app.extensions[AUTOMATION_EXTENSION_KEY]
    stop_event = runtime["stop_event"]

    if app.config.get("AUTOMATION_RUN_ON_STARTUP", True):
        _run_worker_iteration(app)

    while not stop_event.wait(runtime["poll_seconds"]):
        _run_worker_iteration(app)


def _run_worker_iteration(app) -> None:
    started_at = _utcnow_iso()
    runtime = app.extensions[AUTOMATION_EXTENSION_KEY]
    runtime["last_loop_started_at"] = started_at
    runtime["last_error"] = None
    heartbeat_status = "idle"

    try:
        with automation_lock(app, blocking=False):
            with app.app_context():
                db = get_db()
                stats = run_ingestion_for_policies(
                    db,
                    profile=None,
                    due_only=True,
                    trigger="local_scheduler",
                )
            runtime["last_success_at"] = _utcnow_iso()
            runtime["last_stats"] = _compact_stats(stats)
            app.logger.info(
                "Local automation sweep completed policies_run=%s fetched=%s created=%s updated=%s skipped=%s errors=%s",
                stats.get("policies_run", 0),
                stats.get("fetched", 0),
                stats.get("created", 0),
                stats.get("updated", 0),
                stats.get("skipped", 0),
                len(stats.get("errors", [])),
            )
    except TimeoutError:
        heartbeat_status = "busy"
        runtime["last_stats"] = {
            "policies_run": 0,
            "fetched": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
            "note": "Skipped local sweep because another ingestion job was already running.",
        }
        app.logger.warning("Local automation sweep skipped because another ingestion job is already running.")
    except Exception as exc:
        heartbeat_status = "error"
        runtime["last_error"] = str(exc)
        app.logger.exception("Local automation sweep failed.")
    finally:
        runtime["last_loop_finished_at"] = _utcnow_iso()
        _write_heartbeat(app, status=heartbeat_status)


def _configure_logging(app) -> None:
    log_path = _log_path(app)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if any(
        isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path
        for handler in app.logger.handlers
    ):
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_500_000,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    app.logger.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)


def _should_start_local_worker(app) -> bool:
    if app.testing:
        return False
    if str(app.config.get("AUTOMATION_MODE", "")).strip().lower() != "local":
        return False
    if app.debug and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        return False
    return True


def _write_heartbeat(app, *, status: str) -> None:
    runtime = get_runtime_snapshot(app)
    payload = {
        "status": status,
        **runtime,
        "updated_at": _utcnow_iso(),
    }
    heartbeat_path = Path(runtime["heartbeat_path"])
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _compact_stats(stats: dict) -> dict:
    return {
        "policies_run": int(stats.get("policies_run", 0) or 0),
        "fetched": int(stats.get("fetched", 0) or 0),
        "created": int(stats.get("created", 0) or 0),
        "updated": int(stats.get("updated", 0) or 0),
        "skipped": int(stats.get("skipped", 0) or 0),
        "errors": list(stats.get("errors", [])),
    }


def _heartbeat_path(app) -> Path:
    return _artifact_root(app) / "automation-heartbeat.json"


def _log_path(app) -> Path:
    return _artifact_root(app) / "scrapperlanas.log"


def _artifact_root(app) -> Path:
    configured = str(app.config.get("AUTOMATION_ARTIFACTS_DIR", "") or "").strip()
    if configured:
        return Path(configured)

    database_path = str(app.config.get("DATABASE", "") or "").strip()
    if database_path:
        return Path(database_path).parent / "runtime"
    return Path(app.instance_path) / "runtime"


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
