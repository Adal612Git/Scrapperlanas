from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, compact_text, extract_skills
from .procurement import first_value, procurement_fit


class TEDEUConnector(BaseConnector):
    source_name = "ted_eu"
    source_type = "procurement"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        endpoint = str(config.get("endpoint") or "https://api.ted.europa.eu/v3/notices/search").strip()
        query = str(config.get("expert_query") or _expert_query(config)).strip()
        limit = _bounded_int(config.get("limit") or config.get("max_results"), default=50, minimum=1, maximum=250)
        max_items = _bounded_int(config.get("max_items") or config.get("max_results"), default=50, minimum=1, maximum=250)
        payload = {
            "query": query,
            "fields": config.get("fields") or _default_fields(),
            "page": 1,
            "limit": limit,
            "scope": str(config.get("scope") or "ACTIVE"),
            "checkQuerySyntax": False,
            "paginationMode": "PAGE_NUMBER",
        }
        status, response_payload, error = self._post_json(endpoint, config=config, json_payload=payload)
        if status != "ok":
            return ConnectorResult(self.source_name, status, error_message=error)

        notices = _extract_ted_results(response_payload)
        opportunities = []
        for notice in notices:
            if not isinstance(notice, dict):
                continue
            normalized = self.normalize(
                {
                    **notice,
                    "_source_label": str(config.get("source_label") or "TED EU"),
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
            raw_count=len(notices),
            normalized_count=len(opportunities),
        )

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        notice_id = str(first_value(item, "publication-number", "notice-number", "id", "noticeId") or "").strip()
        title = str(first_value(item, "notice-title", "title", "contract-title", "name") or "").strip()
        description = str(first_value(item, "description", "notice-description", "short-description") or "").strip()
        buyer_name = str(first_value(item, "buyer-name", "buyer", "organisation-name-buyer") or "").strip()
        country = str(first_value(item, "buyer-country", "place-of-performance-country", "country") or "").strip()
        deadline = str(first_value(item, "deadline-receipt-tender", "deadline", "deadline-date") or "").strip() or None
        posted_at = str(first_value(item, "publication-date", "notice-publication-date") or "").strip() or None
        amount = _safe_int_or_none(first_value(item, "estimated-value", "total-value", "value"))
        currency = str(first_value(item, "estimated-value-cur", "currency") or "EUR").strip() or "EUR"
        cpv_codes = _coerce_list(first_value(item, "classification-cpv", "main-classification", "cpv"))
        cpv_text = " ".join(_coerce_list(first_value(item, "classification-cpv-lot", "cpv-description")))
        raw_text = compact_text(
            "\n".join(part for part in (title, buyer_name, description, cpv_text, " ".join(cpv_codes)) if part),
            limit=5200,
        )
        if not notice_id or not title or not procurement_fit(
            raw_text,
            cpv_codes=cpv_codes,
            include_terms=item.get("_include_terms"),
            exclude_terms=item.get("_exclude_terms"),
            cpv_prefixes=item.get("_cpv_prefixes"),
        ):
            return {}

        url = _ted_notice_url(item, notice_id)
        skills = extract_skills(raw_text, limit=8)
        evidence = [
            snippet
            for snippet in (
                description[:280],
                cpv_text[:240],
                amount and f"Valor estimado: {currency} {amount:,}",
                deadline and f"Deadline: {deadline}",
            )
            if snippet
        ]

        return {
            "external_id": f"ted_eu:{notice_id}",
            "source_name": self.source_name,
            "source_type": self.source_type,
            "title": title,
            "company": buyer_name,
            "buyer_name": buyer_name,
            "buyer_domain": "",
            "country": country,
            "url": url,
            "apply_url": url,
            "raw_text": raw_text,
            "body": raw_text,
            "posted_at": posted_at,
            "deadline_at": deadline,
            "estimated_value": amount,
            "currency": currency,
            "required_skills": skills,
            "pain_signals": [
                "Procurement EU publicado en TED.",
                "Encaja con software, datos, automatizacion, reporting, cloud o ciberseguridad.",
            ],
            "contact_signals": [f"portal:{url}"],
            "evidence_snippets": evidence[:5],
            "next_best_action": "Revisar notice TED y preparar respuesta formal",
            "source_label": str(item.get("_source_label") or "TED EU"),
        }


def _expert_query(config: Mapping[str, Any]) -> str:
    raw_terms = [str(term).strip() for term in config.get("query_terms", []) if str(term).strip()]
    if raw_terms:
        return " OR ".join(f'"{term}"' for term in raw_terms[:12])
    cpv_codes = [str(code).strip() for code in config.get("cpv_codes", []) if str(code).strip()]
    if cpv_codes:
        return " OR ".join(cpv_codes[:16])
    return '"software" OR "data" OR "automation" OR "cloud"'


def _default_fields() -> list[str]:
    return [
        "publication-number",
        "notice-title",
        "buyer-name",
        "buyer-country",
        "publication-date",
        "deadline-receipt-tender",
        "estimated-value",
        "estimated-value-cur",
        "classification-cpv",
        "classification-cpv-lot",
        "description",
    ]


def _extract_ted_results(payload: Any) -> list:
    if not isinstance(payload, dict):
        return []
    for key in ("results", "notices", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    embedded = payload.get("_embedded") if isinstance(payload.get("_embedded"), dict) else {}
    for value in embedded.values():
        if isinstance(value, list):
            return value
    return []


def _ted_notice_url(item: Mapping[str, Any], notice_id: str) -> str:
    links = item.get("links")
    if isinstance(links, dict):
        for key in ("html", "self", "notice", "en"):
            value = links.get(key)
            if isinstance(value, dict):
                href = str(value.get("href") or "").strip()
            else:
                href = str(value or "").strip()
            if href and not href.lower().endswith(".pdf"):
                return href
    return f"https://ted.europa.eu/en/notice/-/detail/{notice_id}"


def _coerce_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value not in (None, ""):
        return [str(value).strip()]
    return []


def _safe_int_or_none(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))
