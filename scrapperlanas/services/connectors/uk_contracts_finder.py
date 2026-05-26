from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult
from .procurement import normalize_ocds_release, to_iso_date_days_ago


class UKContractsFinderConnector(BaseConnector):
    source_name = "uk_contracts_finder"
    source_type = "procurement"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        endpoint = str(config.get("endpoint") or "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search").strip()
        limit = _bounded_int(config.get("limit") or config.get("max_results"), default=50, minimum=1, maximum=100)
        max_items = _bounded_int(config.get("max_items") or config.get("max_results"), default=50, minimum=1, maximum=100)
        days_back = _bounded_int(config.get("published_from_days"), default=21, minimum=0, maximum=365)
        params: dict[str, Any] = {
            "limit": limit,
            "publishedFrom": to_iso_date_days_ago(days_back),
        }
        stages = config.get("stages")
        if isinstance(stages, list) and stages:
            params["stages"] = ",".join(str(stage) for stage in stages if str(stage).strip())
        elif config.get("stage"):
            params["stages"] = str(config.get("stage")).strip()

        status, payload, error = self._get_json(endpoint, config=config, params=params)
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        releases = payload.get("releases", []) if isinstance(payload, dict) else []
        opportunities = []
        for release in releases:
            if not isinstance(release, dict):
                continue
            if _is_award_only(release) and not bool(config.get("include_awards", False)):
                continue
            normalized = self.normalize(
                {
                    **release,
                    "_source_label": str(config.get("source_label") or "UK Contracts Finder"),
                    "_include_terms": config.get("include_terms"),
                    "_exclude_terms": config.get("exclude_terms"),
                    "_cpv_prefixes": config.get("cpv_prefixes"),
                }
            )
            if normalized:
                opportunities.append(normalized)
            if len(opportunities) >= max_items:
                break

        return ConnectorResult(
            self.source_name,
            "ok" if opportunities else "no_matches",
            opportunities=opportunities,
            raw_count=len(releases),
            normalized_count=len(opportunities),
        )

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return normalize_ocds_release(
            item,
            source_name=self.source_name,
            source_label=str(item.get("_source_label") or "UK Contracts Finder"),
            default_country="United Kingdom",
            notice_url_template="https://www.contractsfinder.service.gov.uk/Notice/{id}",
            include_terms=item.get("_include_terms"),
            exclude_terms=item.get("_exclude_terms"),
            cpv_prefixes=item.get("_cpv_prefixes"),
        )


def _is_award_only(release: Mapping[str, Any]) -> bool:
    tags = [str(tag).lower() for tag in (release.get("tag") or [])]
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    return "award" in tags and str(tender.get("status") or "").lower() == "complete"


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))
