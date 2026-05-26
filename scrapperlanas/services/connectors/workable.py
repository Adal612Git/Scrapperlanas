from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, coerce_target_company, group_hiring_jobs_to_opportunity


class WorkableConnector(BaseConnector):
    source_name = "workable_jobs"
    source_type = "hiring_signal"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        enabled = bool(config.get("enabled", True))
        token = str(config.get("api_token") or config.get("workable_api_token") or "").strip()
        if not token:
            if enabled:
                return ConnectorResult(self.source_name, "auth_error", error_message="WORKABLE_API_TOKEN is required.")
            return ConnectorResult(self.source_name, "no_matches", error_message="Workable disabled and token missing.")

        target = coerce_target_company(config)
        slug = str(target.get("ats_slug") or config.get("ats_slug") or "").strip()
        if not slug:
            return ConnectorResult(self.source_name, "no_matches", error_message="Missing Workable subdomain.")

        max_jobs = _safe_int(config.get("max_jobs_per_company") or config.get("max_results"), default=50)
        url = f"https://{slug}.workable.com/spi/v3/jobs"
        status, payload, error = self._get_json(
            url,
            config=config,
            headers={"Authorization": f"Bearer {token}"},
        )
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        jobs_payload = payload.get("jobs", []) if isinstance(payload, dict) else []
        published_jobs = [job for job in jobs_payload if isinstance(job, dict) and job.get("state") == "published"]
        jobs = [self.normalize(job) for job in published_jobs]
        jobs = [job for job in jobs if job]
        opportunity = group_hiring_jobs_to_opportunity(
            source_name=self.source_name,
            provider="workable",
            target_company=target,
            jobs=jobs,
            max_jobs=max_jobs,
        )
        opportunities = [opportunity] if opportunity else []
        return ConnectorResult(
            self.source_name,
            "ok" if opportunities else "no_matches",
            opportunities=opportunities,
            raw_count=len(jobs_payload),
            normalized_count=len(opportunities),
        )

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        salary = item.get("salary") if isinstance(item.get("salary"), dict) else {}
        return {
            "id": str(item.get("id") or item.get("shortcode") or "").strip(),
            "title": str(item.get("title") or item.get("full_title") or "").strip(),
            "url": str(item.get("url") or item.get("shortlink") or "").strip(),
            "apply_url": str(item.get("application_url") or item.get("url") or "").strip(),
            "published_at": str(item.get("created_at") or "").strip(),
            "department": str(item.get("department") or "").strip(),
            "team": "",
            "location": str(location.get("location_str") or location.get("country") or "").strip(),
            "description": " ".join(
                str(part or "").strip()
                for part in (
                    item.get("description"),
                    item.get("requirements"),
                    item.get("benefits"),
                )
                if str(part or "").strip()
            ),
            "compensation_signal": _salary_signal(salary),
        }


def _salary_signal(salary: Mapping[str, Any]) -> str:
    if not salary:
        return ""
    salary_from = salary.get("salary_from")
    salary_to = salary.get("salary_to")
    currency = str(salary.get("salary_currency") or "").upper()
    if salary_from and salary_to:
        return f"{currency} {int(salary_from):,}-{int(salary_to):,}"
    return ""


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
