from __future__ import annotations

from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config, DEFAULT_DEV_SECRET
from .automation import init_app as init_automation_app
from .db import bootstrap_database, get_db, init_app as init_db_app
from .security import init_app as init_security_app


def create_app(test_config: dict | None = None) -> Flask:
    config = Config().as_dict()
    if test_config:
        config.update(test_config)

    instance_path = config.get("INSTANCE_PATH")
    app = Flask(__name__, instance_relative_config=True, instance_path=instance_path)

    app.config.from_mapping(config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    if app.config.get("TRUST_PROXY"):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

    _validate_runtime_config(app)

    init_db_app(app)
    init_security_app(app)

    from .auth import bp as auth_bp
    from .views import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        bootstrap_database()
        _bootstrap_demo_data(app)
        init_automation_app(app)

    return app


def _validate_runtime_config(app: Flask) -> None:
    if app.testing:
        return

    if app.config.get("ENVIRONMENT") != "production":
        return

    secret_key = str(app.config.get("SECRET_KEY", "")).strip()
    if not secret_key or secret_key in {DEFAULT_DEV_SECRET, "change-me", "change-me-before-production"}:
        raise RuntimeError("Set a strong SECRET_KEY before running Scrapperlanas in production.")

    if app.config.get("DEBUG"):
        raise RuntimeError("Disable DEBUG before running Scrapperlanas in production.")


def _bootstrap_demo_data(app: Flask) -> None:
    if not app.config.get("DEMO_DATA"):
        return

    db = get_db()
    total = db.execute("SELECT COUNT(*) AS total FROM opportunities").fetchone()["total"]
    if total:
        return

    from .services.pipeline import run_ingestion_for_policies

    app.logger.info("Bootstrapping demo opportunities from sample_feed because DEMO_DATA is enabled.")
    run_ingestion_for_policies(
        db,
        profile=None,
        source_keys=("sample_feed",),
        due_only=False,
        trigger="demo_bootstrap",
    )
