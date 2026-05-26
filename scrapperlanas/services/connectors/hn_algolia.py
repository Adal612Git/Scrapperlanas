from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from .base import BaseConnector, ConnectorResult, compact_text, html_to_text


class HNAlgoliaConnector(BaseConnector):
    source_name = "hn_algolia"
    source_type = "community_signal"

    def fetch(self, config: Mapping[str, Any]) -> ConnectorResult:
        endpoint = str(config.get("endpoint") or "https://hn.algolia.com/api/v1/search_by_date").strip()
        queries = [str(query).strip() for query in config.get("queries", []) if str(query).strip()]
        if not queries:
            return ConnectorResult(self.source_name, "no_matches")

        hits_per_page = _bounded_int(config.get("hits_per_page"), default=20, minimum=1, maximum=100)
        max_items = _bounded_int(config.get("max_items") or config.get("max_results"), default=50, minimum=1, maximum=200)
        days_back = _bounded_int(config.get("days_back"), default=45, minimum=1, maximum=365)
        created_after = int((datetime.now(UTC) - timedelta(days=days_back)).timestamp())
        tags = str(config.get("tags") or "").strip()
        opportunities: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        raw_count = 0

        for query in queries:
            params: dict[str, Any] = {
                "query": query,
                "hitsPerPage": hits_per_page,
                "numericFilters": f"created_at_i>{created_after}",
            }
            if tags:
                params["tags"] = tags

            status, payload, error = self._get_json(endpoint, config=config, params=params)
            if status != "ok":
                return ConnectorResult(self.source_name, status, raw_count=raw_count, error_message=error)

            hits = payload.get("hits", []) if isinstance(payload, dict) else []
            raw_count += len(hits)
            for hit in hits:
                normalized = self.normalize({**hit, "_scrapperlanas_query": query})
                if not normalized:
                    continue
                url = normalized.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                opportunities.append(normalized)
                if len(opportunities) >= max_items:
                    break
            if len(opportunities) >= max_items:
                break

        status = "ok" if opportunities else "no_matches"
        return ConnectorResult(
            self.source_name,
            status,
            opportunities=opportunities,
            raw_count=raw_count,
            normalized_count=len(opportunities),
        )

    def normalize(self, item: Mapping[str, Any]) -> dict[str, Any]:
        object_id = str(item.get("objectID") or item.get("id") or "").strip()
        story_id = str(item.get("story_id") or object_id).strip()
        author = str(item.get("author") or "").strip()
        title = html_to_text(
            str(item.get("title") or item.get("story_title") or item.get("comment_text") or "").strip()
        )
        text = html_to_text(
            str(item.get("story_text") or item.get("comment_text") or item.get("_highlightResult") or "").strip()
        )
        url = str(item.get("url") or item.get("story_url") or "").strip()
        if not url and (story_id or object_id):
            url = f"https://news.ycombinator.com/item?id={story_id or object_id}"
        query = str(item.get("_scrapperlanas_query") or "").strip()
        raw_text = compact_text("\n".join(part for part in (title, text, query) if part), limit=3600)
        if not raw_text or not url:
            return {}

        commercial_terms = _commercial_terms(raw_text)
        pain = [f"HN query match: {query}"] if query else ["HN community signal"]
        if commercial_terms:
            pain.append("Menciona senales comerciales: " + ", ".join(commercial_terms[:5]))
        if "urgent" in raw_text.lower() or "asap" in raw_text.lower():
            pain.append("Menciona urgencia")

        evidence = [raw_text[:280]]
        if commercial_terms:
            evidence.append("Terminos comerciales detectados: " + ", ".join(commercial_terms[:5]))

        return {
            "external_id": f"hn_algolia:{object_id or url}",
            "source_name": self.source_name,
            "source_type": self.source_type,
            "title": title[:180] or f"Conversacion HN de {author or 'usuario'}",
            "company": author,
            "buyer_name": author,
            "buyer_domain": "",
            "country": "",
            "url": url,
            "apply_url": url,
            "raw_text": raw_text,
            "body": raw_text,
            "posted_at": str(item.get("created_at") or "").strip() or None,
            "required_skills": [],
            "pain_signals": pain[:6],
            "contact_signals": [f"hn_author:{author}"] if author else [],
            "evidence_snippets": evidence[:3],
            "estimated_value": None,
            "currency": "USD",
            "next_best_action": "Revisar manualmente antes de contactar",
        }


def _commercial_terms(text: str) -> list[str]:
    lowered = (text or "").lower()
    terms = []
    for term in ("paid", "budget", "contractor", "consultant", "freelance", "urgent", "asap", "hire", "quote"):
        if term in lowered:
            terms.append(term)
    return terms


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))
