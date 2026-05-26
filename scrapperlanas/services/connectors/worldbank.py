from __future__ import annotations

from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, compact_text, extract_skills, html_to_text
from .procurement import first_value, procurement_fit, text_without_pdf_urls


class WorldBankProcurementConnector(BaseConnector):
    source_name = "worldbank_procurement"
    source_type = "procurement"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        endpoint = str(config.get("endpoint") or "https://search.worldbank.org/api/procnotices").strip()
        query_terms = [str(term).strip() for term in config.get("query_terms", []) if str(term).strip()]
        if not query_terms:
            query_terms = ["software", "data", "automation", "dashboard", "cloud"]
        rows = _bounded_int(config.get("rows") or config.get("max_results"), default=25, minimum=1, maximum=100)
        max_items = _bounded_int(config.get("max_items") or config.get("max_results"), default=50, minimum=1, maximum=200)
        opportunities = []
        raw_count = 0
        seen: set[str] = set()

        for term in query_terms:
            status, payload, error = self._get_json(
                endpoint,
                config=config,
                params={
                    "format": "json",
                    "rows": rows,
                    "os": 0,
                    "qterm": term,
                },
            )
            if status != "ok":
                return ConnectorResult(self.source_name, status, raw_count=raw_count, error_message=error)

            notices = payload.get("procnotices", []) if isinstance(payload, dict) else []
            raw_count += len(notices)
            for notice in notices:
                if not isinstance(notice, dict):
                    continue
                notice_id = str(notice.get("id") or "").strip()
                if not notice_id or notice_id in seen:
                    continue
                normalized = self.normalize(
                    {
                        **notice,
                        "_source_label": str(config.get("source_label") or "World Bank Procurement Notices"),
                        "_include_terms": config.get("include_terms"),
                        "_exclude_terms": config.get("exclude_terms"),
                        "_notice_types": config.get("notice_types"),
                    }
                )
                if not normalized:
                    continue
                seen.add(notice_id)
                opportunities.append(normalized)
                if len(opportunities) >= max_items:
                    break
            if len(opportunities) >= max_items:
                break

        return ConnectorResult(
            self.source_name,
            "ok" if opportunities else "no_matches",
            opportunities=opportunities,
            raw_count=raw_count,
            normalized_count=len(opportunities),
        )

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        notice_type = str(item.get("notice_type") or "").strip()
        allowed_notice_types = [str(value).lower() for value in (item.get("_notice_types") or []) if str(value).strip()]
        if allowed_notice_types and notice_type.lower() not in allowed_notice_types:
            return {}
        if notice_type.lower() == "contract award":
            return {}

        notice_id = str(item.get("id") or "").strip()
        title = str(item.get("bid_description") or item.get("project_name") or "World Bank procurement notice").strip()
        project = str(item.get("project_name") or "").strip()
        country = str(item.get("project_ctry_name") or "").strip()
        notice_text = html_to_text(text_without_pdf_urls(str(item.get("notice_text") or "")))
        raw_text = compact_text(
            "\n".join(
                part
                for part in (
                    title,
                    project,
                    notice_type,
                    str(item.get("procurement_method_name") or ""),
                    str(item.get("procurement_group") or ""),
                    notice_text,
                )
                if part
            ),
            limit=5200,
        )
        if not notice_id or not procurement_fit(
            raw_text,
            include_terms=item.get("_include_terms"),
            exclude_terms=item.get("_exclude_terms"),
        ):
            return {}

        deadline = str(first_value(item, "submission_date", "deadline_date") or "").strip() or None
        posted_at = str(item.get("noticedate") or "").strip() or None
        url = f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{notice_id}"
        skills = extract_skills(raw_text, limit=8)
        evidence = [
            snippet
            for snippet in (
                title[:260],
                project and f"Proyecto: {project}",
                notice_type and f"Tipo de aviso: {notice_type}",
                deadline and f"Fecha limite: {deadline}",
            )
            if snippet
        ]
        contact_signals = _worldbank_contact_signals(notice_text)
        buyer_name = "World Bank financed project"
        if project:
            buyer_name = project

        return {
            "external_id": f"worldbank_procurement:{notice_id}",
            "source_name": self.source_name,
            "source_type": self.source_type,
            "title": title,
            "company": buyer_name,
            "buyer_name": buyer_name,
            "buyer_domain": "worldbank.org",
            "country": country,
            "url": url,
            "apply_url": url,
            "raw_text": raw_text,
            "body": raw_text,
            "posted_at": posted_at,
            "deadline_at": deadline,
            "estimated_value": None,
            "currency": "USD",
            "required_skills": skills,
            "pain_signals": [
                "Procurement estructurado de proyecto financiado por World Bank.",
                "Encaja con software, datos, automatizacion, reporting o integraciones.",
            ],
            "contact_signals": contact_signals,
            "evidence_snippets": evidence[:5],
            "next_best_action": "Revisar aviso del World Bank y validar elegibilidad antes de responder",
            "source_label": str(item.get("_source_label") or "World Bank Procurement Notices"),
        }


def _worldbank_contact_signals(text: str) -> list[str]:
    signals = []
    for token in text.split():
        clean = token.strip(".,;:()[]<>")
        if "@" in clean and "." in clean:
            signals.append(f"email:{clean}")
    return signals[:4]


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))
