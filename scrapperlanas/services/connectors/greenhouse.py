from __future__ import annotations

from typing import Any, Mapping

from .base import (
    BaseConnector,
    ConnectorResult,
    coerce_target_company,
    group_hiring_jobs_to_opportunity,
    html_to_text,
)


class GreenhouseConnector(BaseConnector):
    source_name = "greenhouse_jobs"
    source_type = "hiring_signal"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        target = coerce_target_company(config)
        slug = str(target.get("ats_slug") or config.get("ats_slug") or "").strip()
        if not slug:
            return ConnectorResult(self.source_name, "no_matches", error_message="Missing Greenhouse board slug.")

        max_jobs = _safe_int(config.get("max_jobs_per_company") or config.get("max_results"), default=50)
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        status, payload, error = self._get_json(url, config=config, params={"content": "true"})
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        jobs_payload = payload.get("jobs", []) if isinstance(payload, dict) else []
        jobs = [self.normalize(job) for job in jobs_payload if isinstance(job, dict)]
        jobs = [job for job in jobs if job]
        opportunity = group_hiring_jobs_to_opportunity(
            source_name=self.source_name,
            provider="greenhouse",
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
        departments = [str(row.get("name") or "").strip() for row in (item.get("departments") or []) if isinstance(row, dict)]
        offices = [str(row.get("name") or row.get("location") or "").strip() for row in (item.get("offices") or []) if isinstance(row, dict)]
        location = ""
        if isinstance(item.get("location"), dict):
            location = str(item.get("location", {}).get("name") or "").strip()
        description = html_to_text(str(item.get("content") or ""))
        return {
            "id": str(item.get("id") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("absolute_url") or "").strip(),
            "apply_url": str(item.get("absolute_url") or "").strip(),
            "published_at": str(item.get("updated_at") or "").strip(),
            "department": ", ".join(part for part in departments if part),
            "team": "",
            "location": location or ", ".join(part for part in offices if part),
            "description": description,
            "compensation_signal": _pay_signal(item),
        }


def _pay_signal(item: Mapping[str, Any]) -> str:
    ranges = item.get("pay_input_ranges") or []
    if not isinstance(ranges, list) or not ranges:
        return ""
    first = ranges[0] if isinstance(ranges[0], dict) else {}
    currency = str(first.get("currency_type") or "").upper()
    min_cents = first.get("min_cents")
    max_cents = first.get("max_cents")
    if min_cents and max_cents:
        return f"{currency} {int(min_cents) // 100:,}-{int(max_cents) // 100:,}"
    return str(first.get("title") or first.get("blurb") or "pay transparency").strip()


def _safe_int(value, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
