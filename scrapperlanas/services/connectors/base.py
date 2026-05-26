from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any, Mapping

import requests
from bs4 import BeautifulSoup


RESULT_OK = "ok"
RESULT_NO_MATCHES = "no_matches"
RESULT_AUTH_ERROR = "auth_error"
RESULT_RATE_LIMITED = "rate_limited"
RESULT_PARSE_ERROR = "parse_error"
RESULT_NETWORK_ERROR = "network_error"

ERROR_STATUSES = {
    RESULT_AUTH_ERROR,
    RESULT_RATE_LIMITED,
    RESULT_PARSE_ERROR,
    RESULT_NETWORK_ERROR,
}

TECHNICAL_TERMS = (
    "python",
    "django",
    "flask",
    "fastapi",
    "backend",
    "back-end",
    "full stack",
    "full-stack",
    "api",
    "integration",
    "automation",
    "data",
    "analytics",
    "etl",
    "pipeline",
    "dashboard",
    "scraping",
    "scraper",
    "ai",
    "ml",
    "llm",
    "cloud",
    "devops",
    "platform",
    "postgres",
    "postgresql",
    "sql",
    "react",
    "typescript",
)

SKILL_LABELS = {
    "api": "API",
    "ai": "AI",
    "ml": "ML",
    "llm": "LLM",
    "etl": "ETL",
    "sql": "SQL",
    "back-end": "Backend",
    "backend": "Backend",
    "full stack": "Full Stack",
    "full-stack": "Full Stack",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
}


@dataclass(slots=True)
class ConnectorResult:
    source_name: str
    status: str
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    raw_count: int = 0
    normalized_count: int = 0
    error_message: str | None = None


class BaseConnector:
    source_name = ""
    source_type = ""

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        raise NotImplementedError

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def healthcheck(self, config: Mapping[str, Any]) -> dict[str, Any]:
        result = self.fetch({**dict(config), "max_results": 1})
        return {
            "source_name": self.source_name,
            "status": result.status,
            "raw_count": result.raw_count,
            "normalized_count": result.normalized_count,
            "error_message": result.error_message,
        }

    def _headers(self, config: Mapping[str, Any]) -> dict[str, str]:
        user_agent = str(config.get("user_agent") or "Scrapperlanas/0.1").strip()
        return {
            "User-Agent": user_agent,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        }

    def _timeout(self, config: Mapping[str, Any]) -> int:
        try:
            return max(1, int(config.get("timeout_seconds") or 15))
        except (TypeError, ValueError):
            return 15

    def _get_json(
        self,
        url: str,
        *,
        config: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        retries: int = 1,
    ) -> tuple[str, Any | None, str | None]:
        request_headers = {**self._headers(config), **dict(headers or {})}
        attempts = max(1, retries + 1)
        last_error = None
        for _attempt in range(attempts):
            try:
                response = requests.get(
                    url,
                    headers=request_headers,
                    params=dict(params or {}),
                    timeout=self._timeout(config),
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                continue

            if response.status_code in {401, 403}:
                return RESULT_AUTH_ERROR, None, f"HTTP {response.status_code}"
            if response.status_code == 429:
                return RESULT_RATE_LIMITED, None, "HTTP 429"
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                return RESULT_NETWORK_ERROR, None, f"HTTP {response.status_code}"

            try:
                return RESULT_OK, response.json(), None
            except ValueError as exc:
                return RESULT_PARSE_ERROR, None, str(exc)

        return RESULT_NETWORK_ERROR, None, last_error or "request failed"

    def _post_json(
        self,
        url: str,
        *,
        config: Mapping[str, Any],
        json_payload: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        retries: int = 1,
    ) -> tuple[str, Any | None, str | None]:
        request_headers = {
            **self._headers(config),
            "Content-Type": "application/json",
            **dict(headers or {}),
        }
        attempts = max(1, retries + 1)
        last_error = None
        for _attempt in range(attempts):
            try:
                response = requests.post(
                    url,
                    headers=request_headers,
                    json=dict(json_payload or {}),
                    timeout=self._timeout(config),
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                continue

            if response.status_code in {401, 403}:
                return RESULT_AUTH_ERROR, None, f"HTTP {response.status_code}"
            if response.status_code == 429:
                return RESULT_RATE_LIMITED, None, "HTTP 429"
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                return RESULT_NETWORK_ERROR, None, f"HTTP {response.status_code}"

            try:
                return RESULT_OK, response.json(), None
            except ValueError as exc:
                return RESULT_PARSE_ERROR, None, str(exc)

        return RESULT_NETWORK_ERROR, None, last_error or "request failed"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def today_key(now: datetime | None = None) -> str:
    runtime_now = (now or datetime.now(UTC)).astimezone(UTC)
    return runtime_now.strftime("%Y-%m-%d")


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(BeautifulSoup(unescape(str(value)), "html.parser").stripped_strings)


def compact_text(value: str | None, *, limit: int = 4200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def coerce_target_company(config: Mapping[str, Any]) -> dict[str, Any]:
    target = config.get("target_company") or {}
    if hasattr(target, "keys"):
        return dict(target)
    return {}


def extract_skills(text: str, *, limit: int = 10) -> list[str]:
    lowered = (text or "").lower()
    found = []
    for term in TECHNICAL_TERMS:
        if term in lowered:
            label = SKILL_LABELS.get(term, term.title())
            if label not in found:
                found.append(label)
        if len(found) >= limit:
            break
    return found


def is_technical_job(job: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(job.get(key) or "")
        for key in ("title", "department", "team", "description", "location")
    ).lower()
    if any(term in text for term in ("engineering manager", "sales engineer")):
        return False
    return any(term in text for term in TECHNICAL_TERMS)


def group_hiring_jobs_to_opportunity(
    *,
    source_name: str,
    provider: str,
    target_company: Mapping[str, Any],
    jobs: list[dict[str, Any]],
    max_jobs: int = 50,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    technical_jobs = [job for job in jobs if is_technical_job(job)][:max(1, max_jobs)]
    if not technical_jobs:
        return None

    target_id = str(target_company.get("id") or target_company.get("ats_slug") or target_company.get("name") or "target").strip()
    name = str(target_company.get("name") or target_company.get("ats_slug") or "Empresa objetivo").strip()
    domain = normalize_domain(str(target_company.get("domain") or ""))
    country = str(target_company.get("country") or "").strip()
    slug = str(target_company.get("ats_slug") or "").strip()
    careers_url = str(target_company.get("careers_url") or "").strip()
    signal_day = today_key(now)
    skills = extract_skills(" ".join(_job_text(job) for job in technical_jobs), limit=8)
    if not skills:
        skills = ["Engineering"]

    departments = _top_values(job.get("department") or job.get("team") for job in technical_jobs)
    locations = _top_values(job.get("location") for job in technical_jobs)
    titles = [str(job.get("title") or "").strip() for job in technical_jobs if str(job.get("title") or "").strip()]
    compensation_signals = [
        str(job.get("compensation_signal") or "").strip()
        for job in technical_jobs
        if str(job.get("compensation_signal") or "").strip()
    ]
    primary_skills = ", ".join(skills[:4])
    title = f"{name} esta fortaleciendo equipo tecnico: {primary_skills}"
    evidence = [
        f"{len(technical_jobs)} vacantes tecnicas detectadas en {provider}.",
        "Roles: " + "; ".join(titles[:5]),
    ]
    if departments:
        evidence.append("Areas: " + ", ".join(departments[:4]))
    if compensation_signals:
        evidence.append("Compensacion visible en vacantes: " + "; ".join(compensation_signals[:2]))
    pain_signals = [
        f"Hiring signal: {len(technical_jobs)} vacantes tecnicas activas.",
        f"Team growing in {', '.join(departments[:3]) if departments else 'engineering/data'}",
    ]
    for role_title in titles[:3]:
        pain_signals.append(f"Hiring for {role_title}")

    raw_lines = [
        title,
        f"Provider: {provider}",
        f"Company: {name}",
        f"Domain: {domain}",
        *(_job_text(job) for job in technical_jobs),
    ]
    signal_url = f"https://scrapperlanas.local/targets/{target_id}/signals/{provider}/{signal_day}"
    apply_url = careers_url or _first_url(technical_jobs) or signal_url
    contact = [value for value in (domain and f"domain:{domain}", careers_url and f"careers:{careers_url}") if value]

    return {
        "external_id": f"{provider}:{slug or target_id}:{signal_day}",
        "source_name": source_name,
        "source_type": "hiring_signal",
        "title": title,
        "company": name,
        "buyer_name": name,
        "buyer_domain": domain,
        "country": country,
        "url": signal_url,
        "apply_url": apply_url,
        "raw_text": "\n".join(raw_lines),
        "body": "\n".join(raw_lines),
        "posted_at": utc_now_iso(),
        "required_skills": skills,
        "pain_signals": pain_signals[:8],
        "contact_signals": contact,
        "evidence_snippets": evidence[:5],
        "estimated_value": None,
        "currency": "USD",
        "next_best_action": "Revisar si puede ofrecerse apoyo temporal o consultoria puntual",
        "jobs_snapshot": technical_jobs,
    }


def normalize_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.removeprefix("www.")
    return text.split("/", 1)[0].strip()


def _job_text(job: Mapping[str, Any]) -> str:
    parts = [
        str(job.get("title") or "").strip(),
        str(job.get("department") or "").strip(),
        str(job.get("team") or "").strip(),
        str(job.get("location") or "").strip(),
        str(job.get("description") or "").strip(),
    ]
    return " | ".join(part for part in parts if part)


def _top_values(values) -> list[str]:
    seen = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen[:6]


def _first_url(jobs: list[dict[str, Any]]) -> str:
    for job in jobs:
        url = str(job.get("url") or job.get("apply_url") or "").strip()
        if url:
            return url
    return ""
