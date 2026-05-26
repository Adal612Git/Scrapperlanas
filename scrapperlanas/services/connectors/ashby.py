from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, coerce_target_company, group_hiring_jobs_to_opportunity, html_to_text


class AshbyConnector(BaseConnector):
    source_name = "ashby_jobs"
    source_type = "hiring_signal"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        target = coerce_target_company(config)
        slug = str(target.get("ats_slug") or config.get("ats_slug") or "").strip()
        if not slug:
            return ConnectorResult(self.source_name, "no_matches", error_message="Missing Ashby job board name.")

        max_jobs = _safe_int(config.get("max_jobs_per_company") or config.get("max_results"), default=50)
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        status, payload, error = self._get_json(
            url,
            config=config,
            params={"includeCompensation": "true"},
        )
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        jobs_payload = payload.get("jobs", []) if isinstance(payload, dict) else []
        jobs = [
            self.normalize(job)
            for job in jobs_payload
            if isinstance(job, dict) and job.get("isListed", True) is not False
        ]
        jobs = [job for job in jobs if job]
        opportunity = group_hiring_jobs_to_opportunity(
            source_name=self.source_name,
            provider="ashby",
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
        compensation = item.get("compensation") if isinstance(item.get("compensation"), dict) else {}
        return {
            "id": str(item.get("id") or item.get("jobUrl") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("jobUrl") or item.get("applyUrl") or "").strip(),
            "apply_url": str(item.get("applyUrl") or item.get("jobUrl") or "").strip(),
            "published_at": str(item.get("publishedAt") or "").strip(),
            "department": str(item.get("department") or "").strip(),
            "team": str(item.get("team") or "").strip(),
            "location": str(item.get("location") or item.get("workplaceType") or "").strip(),
            "description": str(item.get("descriptionPlain") or html_to_text(str(item.get("descriptionHtml") or ""))).strip(),
            "compensation_signal": _compensation_signal(compensation),
        }


def _compensation_signal(compensation: Mapping[str, Any]) -> str:
    if not compensation:
        return ""
    return str(
        compensation.get("compensationTierSummary")
        or compensation.get("scrapeableCompensationSalarySummary")
        or ""
    ).strip()


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
