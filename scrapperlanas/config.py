from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

DEFAULT_DEV_SECRET = "scrapperlanas-dev-secret"


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _as_csv(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


class Config:
    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parent.parent
        is_vercel = _as_bool(os.getenv("VERCEL"), default=False) or bool(os.getenv("VERCEL_URL"))
        instance_dir = Path("/tmp/scrapperlanas-instance") if is_vercel else project_root / "instance"
        environment = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "development")).strip().lower()
        deployment_host = (os.getenv("VERCEL_URL", "").strip() or "scrapperlanas.vercel.app").strip("/")
        default_app_origin = (
            f"https://{deployment_host}"
            if environment == "production" or is_vercel
            else "https://localhost"
        )
        testing = _as_bool(os.getenv("TESTING"), default=False)
        debug_default = False if is_vercel else environment != "production"
        ollama_enabled = _as_bool(os.getenv("OLLAMA_ENABLED"), default=False)
        configured_ai_provider = os.getenv("AI_PROVIDER", "").strip().lower()
        default_ai_provider = configured_ai_provider or ("ollama" if ollama_enabled else "heuristic")

        self.INSTANCE_PATH = str(instance_dir)
        self.ENVIRONMENT = environment
        self.TESTING = testing
        self.DEBUG = _as_bool(os.getenv("FLASK_DEBUG"), default=debug_default)
        self.SECRET_KEY = os.getenv("SECRET_KEY", DEFAULT_DEV_SECRET)
        self.APP_BASE_URL = os.getenv("APP_BASE_URL", default_app_origin).strip().rstrip("/")
        self.DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or None
        self.DATABASE = os.getenv(
            "DATABASE_PATH",
            str(instance_dir / "scrapperlanas.sqlite3"),
        )
        self.HOST = os.getenv("HOST", "127.0.0.1")
        self.PORT = _as_int(os.getenv("PORT"), default=5000)
        self.AI_PROVIDER_EXPLICIT = bool(configured_ai_provider)
        self.AI_PROVIDER = default_ai_provider
        self.AI_API_KEY = os.getenv("AI_API_KEY", "").strip() or None
        self.AI_MODEL = os.getenv("AI_MODEL", "").strip() or None
        self.AI_EMBED_PROVIDER = os.getenv("AI_EMBED_PROVIDER", "").strip().lower() or None
        self.AI_EMBED_MODEL = os.getenv("AI_EMBED_MODEL", "").strip() or None
        self.AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip() or None
        self.AI_TEMPERATURE = float(
            os.getenv("AI_TEMPERATURE", os.getenv("OLLAMA_TEMPERATURE", "0.15"))
        )
        self.AI_MAX_OUTPUT_TOKENS = _as_int(os.getenv("AI_MAX_OUTPUT_TOKENS"), default=900)
        self.AI_MAX_ENRICHMENTS_PER_RUN = _as_int(
            os.getenv("AI_MAX_ENRICHMENTS_PER_RUN"),
            default=24,
        )
        self.AI_MIN_SCORE_FOR_REMOTE_ENRICHMENT = _as_int(
            os.getenv("AI_MIN_SCORE_FOR_REMOTE_ENRICHMENT"),
            default=38,
        )
        self.OLLAMA_ENABLED = ollama_enabled
        self.OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "").strip() or None
        self.OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "").strip() or None
        self.OLLAMA_REQUEST_TIMEOUT_SECONDS = max(
            1,
            _as_int(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS"), default=4),
        )
        self.OLLAMA_MODEL_PREFERENCES = _as_csv(
            os.getenv("OLLAMA_MODEL_PREFERENCES"),
            default=("mistral:latest", "qwen2.5-coder:7b", "llama3.1:8b"),
        )
        self.OLLAMA_TEMPERATURE = self.AI_TEMPERATURE
        self.OLLAMA_NUM_CTX = _as_int(os.getenv("OLLAMA_NUM_CTX"), default=8192)
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip() or None
        self.GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
        self.GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001").strip()
        self.GEMINI_BASE_URL = os.getenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        ).strip()
        self.DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip() or None
        self.DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.DEEPSEEK_BASE_URL = os.getenv(
            "DEEPSEEK_BASE_URL",
            "https://api.deepseek.com",
        ).strip()
        self.REQUEST_TIMEOUT_SECONDS = _as_int(os.getenv("REQUEST_TIMEOUT_SECONDS"), default=12)
        self.CONNECTOR_TIMEOUT_SECONDS = max(
            1,
            _as_int(os.getenv("CONNECTOR_TIMEOUT_SECONDS"), default=self.REQUEST_TIMEOUT_SECONDS),
        )
        self.CONNECTOR_MAX_RESULTS_PER_SOURCE = max(
            1,
            _as_int(os.getenv("CONNECTOR_MAX_RESULTS_PER_SOURCE"), default=50),
        )
        self.HN_ALGOLIA_ENABLED = _as_bool(os.getenv("HN_ALGOLIA_ENABLED"), default=False)
        self.GREENHOUSE_ENABLED = _as_bool(os.getenv("GREENHOUSE_ENABLED"), default=False)
        self.LEVER_ENABLED = _as_bool(os.getenv("LEVER_ENABLED"), default=False)
        self.ASHBY_ENABLED = _as_bool(os.getenv("ASHBY_ENABLED"), default=False)
        self.WORKABLE_ENABLED = _as_bool(os.getenv("WORKABLE_ENABLED"), default=False)
        self.WORKABLE_API_TOKEN = os.getenv("WORKABLE_API_TOKEN", "").strip() or None
        self.TED_EU_ENABLED = _as_bool(os.getenv("TED_EU_ENABLED"), default=False)
        self.UK_FIND_TENDER_ENABLED = _as_bool(os.getenv("UK_FIND_TENDER_ENABLED"), default=False)
        self.UK_CONTRACTS_FINDER_ENABLED = _as_bool(os.getenv("UK_CONTRACTS_FINDER_ENABLED"), default=False)
        self.WORLD_BANK_PROCUREMENT_ENABLED = _as_bool(
            os.getenv("WORLD_BANK_PROCUREMENT_ENABLED"),
            default=False,
        )
        self.INGEST_USER_AGENT = os.getenv(
            "INGEST_USER_AGENT",
            f"Scrapperlanas/0.1 (+{default_app_origin})",
        )
        self.GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip() or None
        self.GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com").strip().rstrip("/")
        self.SAM_API_KEY = os.getenv("SAM_API_KEY", "").strip() or None
        self.SAM_OPPORTUNITIES_URL = os.getenv(
            "SAM_OPPORTUNITIES_URL",
            "https://api.sam.gov/opportunities/v2/search",
        ).strip()
        self.CRON_SECRET = os.getenv("CRON_SECRET", "").strip() or None
        self.AUTOMATION_MODE = os.getenv("AUTOMATION_MODE", "manual").strip().lower() or "manual"
        self.AUTOMATION_POLL_SECONDS = max(
            30,
            _as_int(os.getenv("AUTOMATION_POLL_SECONDS"), default=300),
        )
        self.SCRAPPERLANAS_BASE_URL = (
            os.getenv("SCRAPPERLANAS_BASE_URL", self.APP_BASE_URL).strip().rstrip("/")
        )
        self.N8N_PUBLIC_URL = os.getenv("N8N_PUBLIC_URL", "http://localhost:5678").strip().rstrip("/")
        self.N8N_IMPORT_WEBHOOK_PATH = (
            os.getenv("N8N_IMPORT_WEBHOOK_PATH", "scrapperlanas/import").strip().strip("/")
        )
        self.N8N_SCHEDULER_INTERVAL_MINUTES = max(
            1,
            _as_int(os.getenv("N8N_SCHEDULER_INTERVAL_MINUTES"), default=15),
        )
        self.N8N_BOOTSTRAP_FORCE = _as_bool(os.getenv("N8N_BOOTSTRAP_FORCE"), default=False)
        self.AUTOMATION_RUN_ON_STARTUP = _as_bool(
            os.getenv("AUTOMATION_RUN_ON_STARTUP"),
            default=True,
        )
        self.CSRF_ENABLED = _as_bool(os.getenv("CSRF_ENABLED"), default=not testing)
        self.SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "scrapperlanas_session")
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
        self.SESSION_COOKIE_SECURE = _as_bool(
            os.getenv("SESSION_COOKIE_SECURE"),
            default=environment == "production" or is_vercel,
        )
        self.SESSION_REFRESH_EACH_REQUEST = False
        self.PERMANENT_SESSION_LIFETIME = timedelta(
            minutes=_as_int(os.getenv("SESSION_LIFETIME_MINUTES"), default=480)
        )
        self.MAX_CONTENT_LENGTH = _as_int(
            os.getenv("MAX_CONTENT_LENGTH_BYTES"),
            default=2 * 1024 * 1024,
        )
        self.PREFERRED_URL_SCHEME = os.getenv(
            "PREFERRED_URL_SCHEME",
            "https" if environment == "production" or is_vercel else "http",
        )
        self.TRUST_PROXY = _as_bool(os.getenv("TRUST_PROXY"), default=is_vercel)
        self.ALLOW_INSECURE_PRODUCTION_COOKIES = _as_bool(
            os.getenv("ALLOW_INSECURE_PRODUCTION_COOKIES"),
            default=False,
        )
        self.ALLOW_SQLITE_IN_PRODUCTION = _as_bool(
            os.getenv("ALLOW_SQLITE_IN_PRODUCTION"),
            default=False,
        )
        self.HSTS_ENABLED = _as_bool(
            os.getenv("HSTS_ENABLED"),
            default=environment == "production" or is_vercel,
        )
        self.HSTS_MAX_AGE_SECONDS = max(
            0,
            _as_int(os.getenv("HSTS_MAX_AGE_SECONDS"), default=31_536_000),
        )
        self.HSTS_INCLUDE_SUBDOMAINS = _as_bool(
            os.getenv("HSTS_INCLUDE_SUBDOMAINS"),
            default=True,
        )
        self.HSTS_PRELOAD = _as_bool(os.getenv("HSTS_PRELOAD"), default=False)
        self.AUTH_RATE_LIMIT_MAX_ATTEMPTS = max(
            0,
            _as_int(os.getenv("AUTH_RATE_LIMIT_MAX_ATTEMPTS"), default=8),
        )
        self.AUTH_RATE_LIMIT_WINDOW_SECONDS = max(
            1,
            _as_int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SECONDS"), default=900),
        )
        self.PREFERRED_KEYWORDS = (
            "python",
            "scraping",
            "automation",
            "n8n",
            "rust",
            "backend",
            "api",
            "dashboard",
            "flask",
            "postgresql",
            "llm",
            "ollama",
            "remote",
            "freelance",
        )

    def as_dict(self) -> dict:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key.isupper()
        }
