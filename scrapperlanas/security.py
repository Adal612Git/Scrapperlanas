from __future__ import annotations

import secrets
from typing import Final

from flask import abort, current_app, request, session


UNSAFE_METHODS: Final[set[str]] = {"POST", "PUT", "PATCH", "DELETE"}


def init_app(app) -> None:
    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": generate_csrf_token}

    @app.before_request
    def protect_forms() -> None:
        if not current_app.config.get("CSRF_ENABLED", True):
            return
        if request.method not in UNSAFE_METHODS:
            return
        if _is_authorized_internal_automation_request():
            return

        session_token = session.get("_csrf_token", "")
        request_token = (
            request.form.get("csrf_token", "")
            or request.headers.get("X-CSRFToken", "")
            or request.headers.get("X-CSRF-Token", "")
        )

        if not session_token or not request_token:
            abort(400, description="CSRF token missing.")
        if not secrets.compare_digest(session_token, request_token):
            abort(400, description="CSRF token invalid.")

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("Content-Security-Policy", _build_csp())
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(), payment=(), usb=()")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")

        if response.mimetype in {"text/html", "text/csv"}:
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("Pragma", "no-cache")

        hsts_header = _build_hsts_header()
        if hsts_header:
            response.headers.setdefault("Strict-Transport-Security", hsts_header)

        return response


def generate_csrf_token() -> str:
    token = session.get("_csrf_token")
    if token:
        return token

    token = secrets.token_urlsafe(32)
    session["_csrf_token"] = token
    return token


def _is_authorized_internal_automation_request() -> bool:
    if not request.path.startswith("/internal/"):
        return False

    cron_secret = str(current_app.config.get("CRON_SECRET", "") or "").strip()
    if not cron_secret:
        return False

    auth_header = str(request.headers.get("Authorization", "") or "").strip()
    return secrets.compare_digest(auth_header, f"Bearer {cron_secret}")


def _build_csp() -> str:
    directives = {
        "default-src": "'self'",
        "style-src": "'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
        "script-src": "'self' https://cdn.tailwindcss.com",
        "img-src": "'self' data:",
        "font-src": "'self' https://cdnjs.cloudflare.com data:",
        "connect-src": "'self'",
        "form-action": "'self'",
        "base-uri": "'self'",
        "frame-ancestors": "'none'",
        "object-src": "'none'",
    }
    return "; ".join(f"{name} {value}" for name, value in directives.items())


def _build_hsts_header() -> str:
    if not current_app.config.get("HSTS_ENABLED", False):
        return ""
    if not request.is_secure and current_app.config.get("ENVIRONMENT") != "production":
        return ""

    max_age = max(0, int(current_app.config.get("HSTS_MAX_AGE_SECONDS", 31_536_000) or 0))
    parts = [f"max-age={max_age}"]
    if current_app.config.get("HSTS_INCLUDE_SUBDOMAINS", True):
        parts.append("includeSubDomains")
    if current_app.config.get("HSTS_PRELOAD", False):
        parts.append("preload")
    return "; ".join(parts)
