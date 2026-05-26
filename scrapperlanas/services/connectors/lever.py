from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, coerce_target_company, group_hiring_jobs_to_opportunity, html_to_text


class LeverConnector(BaseConnector):
    source_name = "lever_postings"
    source_type = "hiring_signal"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        target = coerce_target_company(config)
        slug = str(target.get("ats_slug") or config.get("ats_slug") or "").strip()
        if not slug:
            return ConnectorResult(self.source_name, "no_matches", error_message="Missing Lever site slug.")

        max_jobs = _safe_int(config.get("max_jobs_per_company") or config.get("max_results"), default=50)
        url = f"https://api.lever.co/v0/postings/{slug}"
        status, payload, error = self._get_json(
            url,
            config=config,
            params={"mode": "json", "limit": max_jobs},
        )
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        jobs_payload = payload if isinstance(payload, list) else []
        jobs = [self.normalize(job) for job in jobs_payload if isinstance(job, dict)]
        jobs = [job for job in jobs if job]
        opportunity = group_hiring_jobs_to_opportunity(
            source_name=self.source_name,
            provider="lever",
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
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        lists = item.get("lists") if isinstance(item.get("lists"), list) else []
        list_text = " ".join(html_to_text(str(row.get("content") or "")) for row in lists if isinstance(row, dict))
        salary_range = item.get("salaryRange") if isinstance(item.get("salaryRange"), dict) else {}
        return {
            "id": str(item.get("id") or "").strip(),
            "title": str(item.get("text") or "").strip(),
            "url": str(item.get("hostedUrl") or item.get("applyUrl") or "").strip(),
            "apply_url": str(item.get("applyUrl") or item.get("hostedUrl") or "").strip(),
            "published_at": str(item.get("createdAt") or "").strip(),
            "department": str(categories.get("department") or categories.get("team") or "").strip(),
            "team": str(categories.get("team") or "").strip(),
            "location": str(categories.get("location") or item.get("workplaceType") or "").strip(),
            "description": " ".join(
                part
                for part in (
                    html_to_text(str(item.get("descriptionPlain") or item.get("description") or "")),
                    list_text,
                    html_to_text(str(item.get("additionalPlain") or item.get("additional") or "")),
                )
                if part
            ),
            "compensation_signal": _salary_signal(salary_range, item),
        }


def _salary_signal(salary_range: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    description = str(item.get("salaryDescriptionPlain") or item.get("salaryDescription") or "").strip()
    if salary_range:
        currency = str(salary_range.get("currency") or "").upper()
        min_value = salary_range.get("min")
        max_value = salary_range.get("max")
        if min_value and max_value:
            return f"{currency} {int(min_value):,}-{int(max_value):,}"
    return html_to_text(description)


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
