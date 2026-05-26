from __future__ import annotations

from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config, DEFAULT_DEV_SECRET
from .automation import init_app as init_automation_app
from .db import bootstrap_database, init_app as init_db_app
from .security import init_app as init_security_app


def create_app(test_config: dict | None = None) -> Flask:
    config = Config().as_dict()
    if test_config:
        config.update(test_config)
        if (
            test_config.get("OLLAMA_ENABLED")
            and "AI_PROVIDER" not in test_config
            and "AI_PROVIDER_EXPLICIT" not in test_config
        ):
            config["AI_PROVIDER"] = "ollama"
            config["AI_PROVIDER_EXPLICIT"] = False
            if "AI_EMBED_PROVIDER" not in test_config:
                config["AI_EMBED_PROVIDER"] = "ollama"

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
        init_automation_app(app)

    return app


def _validate_runtime_config(app: Flask) -> None:
    if app.testing:
        return

    if app.config.get("ENVIRONMENT") != "production":
        return

    secret_key = str(app.config.get("SECRET_KEY", "")).strip()
    if _is_weak_secret(secret_key):
        raise RuntimeError("Set a strong SECRET_KEY before running Loto Signal in production.")

    cron_secret = str(app.config.get("CRON_SECRET", "") or "").strip()
    if _is_weak_secret(cron_secret):
        raise RuntimeError("Set a strong CRON_SECRET before running Loto Signal in production.")

    if app.config.get("DEBUG"):
        raise RuntimeError("Disable DEBUG before running Loto Signal in production.")

    if not app.config.get("SESSION_COOKIE_SECURE") and not app.config.get("ALLOW_INSECURE_PRODUCTION_COOKIES"):
        raise RuntimeError("SESSION_COOKIE_SECURE must be enabled in production.")

    if not app.config.get("DATABASE_URL") and not app.config.get("ALLOW_SQLITE_IN_PRODUCTION"):
        raise RuntimeError("DATABASE_URL must be configured in production.")


def _is_weak_secret(value: str) -> bool:
    normalized = value.strip().lower()
    if len(value.strip()) < 32:
        return True
    return normalized in {
        DEFAULT_DEV_SECRET,
        "change-me",
        "change-me-before-production",
        "change-me-cron-secret",
        "change-me-n8n",
        "change-me-n8n-encryption-key",
    }
