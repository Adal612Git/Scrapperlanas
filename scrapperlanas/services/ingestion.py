from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from flask import current_app

from ..db import get_db
from .connectors.base import ERROR_STATUSES
from .connectors.registry import connector_for_provider
from .quality import parse_commercial_budget


DEFAULT_REDDIT_URLS = (
    "https://www.reddit.com/r/forhire/new/.json?limit=20",
    "https://www.reddit.com/r/freelance_forhire/new/.json?limit=20",
)

DEFAULT_WORKANA_SEARCH_URLS = (
    "https://www.workana.com/es/jobs?category=it-programming&skills=python",
    "https://www.workana.com/es/jobs?category=it-programming&skills=web-scraping",
    "https://www.workana.com/es/jobs?category=it-programming&skills=automation",
    "https://www.workana.com/es/jobs?category=it-programming&skills=n8n",
)

DEFAULT_REDDIT_SEARCH_TERMS = (
    "python scraping freelance",
    "n8n automation contract",
    "rust backend freelance",
)

DEFAULT_WWR_RSS_URLS = (
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
)

STACK_TERMS = (
    "python",
    "scraping",
    "n8n",
    "rust",
    "flask",
    "django",
    "postgresql",
    "mysql",
    "sql",
    "api",
    "docker",
    "ollama",
    "llm",
    "javascript",
    "typescript",
    "react",
)

SECTOR_TERMS = {
    "FinTech": ("payments", "bank", "billing", "cashflow", "fintech"),
    "E-commerce": ("shopify", "woocommerce", "ecommerce", "catalog"),
    "SaaS": ("saas", "dashboard", "platform", "b2b"),
    "AI/ML": ("ai", "llm", "machine learning", "ollama"),
    "Data": ("scraping", "etl", "analytics", "dataset"),
    "Operations": ("automation", "workflow", "backoffice"),
}

SCAM_PATTERNS = (
    "crypto only",
    "gift card",
    "wire transfer",
    "upfront fee",
    "registration fee",
    "whatsapp",
    "telegram only",
    "no experience required",
)


@dataclass(slots=True)
class NormalizedOpportunity:
    external_id: str
    source_key: str
    source_label: str
    title: str
    company: str
    url: str
    raw_text: str
    source_type: str = ""
    buyer_name: str = ""
    buyer_domain: str = ""
    country: str = ""
    apply_url: str = ""
    budget_min: int | None = None
    budget_max: int | None = None
    estimated_value: int | None = None
    budget_text: str = ""
    currency: str = "USD"
    sector: str = ""
    stack: list[str] = field(default_factory=list)
    required_skills: list[str] = field(default_factory=list)
    pain_signals: list[str] = field(default_factory=list)
    contact_signals: list[str] = field(default_factory=list)
    evidence_snippets: list[str] = field(default_factory=list)
    posted_at: str | None = None
    deadline_at: str | None = None
    risk_level: str = "low"
    risk_reasons: list[str] = field(default_factory=list)
    score: int = 0
    ai_summary: str = ""
    suggested_reply: str = ""
    is_suspicious: bool = False


class BaseConnector:
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": current_app.config["INGEST_USER_AGENT"],
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _timeout(self) -> int:
        return current_app.config["REQUEST_TIMEOUT_SECONDS"]

    def _policy_config(self, policy_row) -> dict:
        raw_config = policy_row["config_json"] or "{}"
        try:
            return json.loads(raw_config)
        except json.JSONDecodeError:
            return {}

    def _normalize(self, *, policy_row, source_label: str, raw: dict) -> NormalizedOpportunity:
        raw_text = "\n".join(
            part.strip()
            for part in (
                raw.get("title", ""),
                raw.get("company", ""),
                raw.get("body", ""),
                raw.get("location", ""),
            )
            if part and str(part).strip()
        )
        budget_min, budget_max, budget_text, currency = extract_budget(raw_text)
        sector = detect_sector(raw_text)
        stack = detect_stack(raw_text)
        source_type = str(raw.get("source_type") or _source_type_for_policy(policy_row["source_key"])).strip()
        buyer_name = str(raw.get("buyer_name") or raw.get("company") or "").strip()
        apply_url = str(raw.get("apply_url") or raw.get("url") or "").strip()
        deadline_at = raw.get("deadline_at") or extract_deadline(raw_text)
        estimated_value = _safe_int_or_none(raw.get("estimated_value")) or budget_max or budget_min
        required_skills = _coerce_list(raw.get("required_skills")) or stack
        contact_signals = _coerce_list(raw.get("contact_signals")) or extract_contact_signals(raw_text)
        pain_signals = _coerce_list(raw.get("pain_signals")) or detect_pain_signals(raw_text)
        evidence_snippets = _coerce_list(raw.get("evidence_snippets")) or build_evidence_snippets(
            raw_text,
            required_skills=required_skills,
            pain_signals=pain_signals,
        )
        risk_level, risk_reasons, suspicious = assess_risk(
            raw_text,
            policy_row["risk_level"],
            budget_min,
            budget_max,
        )

        return NormalizedOpportunity(
            external_id=str(raw.get("external_id", "")),
            source_key=policy_row["source_key"],
            source_label=source_label,
            title=raw.get("title", "").strip() or "Sin titulo",
            company=raw.get("company", "").strip(),
            url=raw.get("url", "").strip(),
            raw_text=raw_text,
            source_type=source_type,
            buyer_name=buyer_name,
            buyer_domain=str(raw.get("buyer_domain") or _domain_from_url(raw.get("url", ""))).strip(),
            country=str(raw.get("country", "") or "").strip(),
            apply_url=apply_url,
            budget_min=budget_min,
            budget_max=budget_max,
            estimated_value=estimated_value,
            budget_text=budget_text,
            currency=currency,
            sector=sector,
            stack=stack,
            required_skills=required_skills,
            pain_signals=pain_signals,
            contact_signals=contact_signals,
            evidence_snippets=evidence_snippets,
            posted_at=raw.get("posted_at"),
            deadline_at=deadline_at,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            is_suspicious=suspicious,
        )

    def _apply_policy_filters(
        self,
        policy_row,
        opportunities: list[NormalizedOpportunity],
    ) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        include_terms = tuple(
            str(term).strip().lower()
            for term in config.get("include_terms", [])
            if str(term).strip()
        )
        exclude_terms = tuple(
            str(term).strip().lower()
            for term in config.get("exclude_terms", [])
            if str(term).strip()
        )
        remote_only = bool(config.get("remote_only"))
        require_budget = bool(config.get("require_budget"))
        max_items = _safe_int(config.get("max_items"), default=0)

        filtered: list[NormalizedOpportunity] = []
        for opportunity in opportunities:
            haystack = " ".join(
                (
                    opportunity.title,
                    opportunity.company,
                    opportunity.raw_text,
                )
            ).lower()

            if include_terms and not any(term in haystack for term in include_terms):
                continue
            if exclude_terms and any(term in haystack for term in exclude_terms):
                continue
            if remote_only and not _looks_remote(haystack):
                continue
            if require_budget and opportunity.budget_min is None and opportunity.budget_max is None:
                continue

            filtered.append(opportunity)

        if max_items > 0:
            return filtered[:max_items]
        return filtered

    def _dedupe_by_url(
        self,
        opportunities: list[NormalizedOpportunity],
    ) -> list[NormalizedOpportunity]:
        unique: dict[str, NormalizedOpportunity] = {}
        for opportunity in opportunities:
            if not opportunity.url or opportunity.url in unique:
                continue
            unique[opportunity.url] = opportunity
        return list(unique.values())


class SampleFeedConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        sample_rows = (
            {
                "external_id": "sample-001",
                "title": "Python scraper para directorios B2B con export CSV",
                "company": "Orbital Prospecting",
                "url": "https://sample.local/opportunities/python-scraper-b2b",
                "body": (
                    "Necesitamos un scraper en Python para extraer leads de directorios publicos, "
                    "normalizar datos y entregar CSV semanal. Presupuesto USD 2,400. "
                    "Stack ideal: Python, requests, BeautifulSoup, PostgreSQL."
                ),
                "posted_at": now,
            },
            {
                "external_id": "sample-002",
                "title": "Automatizacion n8n + dashboard Flask para pipeline comercial",
                "company": "Loto Ops",
                "url": "https://sample.local/opportunities/n8n-dashboard",
                "body": (
                    "Proyecto remoto para integrar n8n, APIs internas y tablero Flask con reportes. "
                    "Presupuesto entre 1800 y 3200 USD. Valoramos Docker, Postgres y experiencia operativa."
                ),
                "posted_at": now,
            },
            {
                "external_id": "sample-003",
                "title": "Integracion Greenhouse y scoring de candidatos",
                "company": "Northline Recruiting",
                "url": "https://sample.local/opportunities/greenhouse-scoring",
                "body": (
                    "Buscamos freelance para conectar Greenhouse, enriquecer vacantes y crear scoring en Rust "
                    "o Python. Budget $4,800. Entrega en tres semanas."
                ),
                "posted_at": now,
            },
            {
                "external_id": "sample-004",
                "title": "Data entry remoto con pago por crypto y WhatsApp",
                "company": "Unknown Agency",
                "url": "https://sample.local/opportunities/crypto-data-entry",
                "body": (
                    "No experience required. Contacto solo por WhatsApp. "
                    "Pago por crypto only y fee de registro previo."
                ),
                "posted_at": now,
            },
        )
        normalized = [
            self._normalize(policy_row=policy_row, source_label=policy_row["display_name"], raw=row)
            for row in sample_rows
        ]
        return self._apply_policy_filters(policy_row, normalized)


class RedditConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        urls = config.get("urls") or list(DEFAULT_REDDIT_URLS)
        search_terms = config.get("search_terms") or list(DEFAULT_REDDIT_SEARCH_TERMS)
        search_urls = [
            f"https://www.reddit.com/search.json?q={quote(term)}&sort=new&limit=10"
            for term in search_terms[:4]
        ]

        normalized: list[NormalizedOpportunity] = []
        successful_responses = 0
        logical_urls = [*urls, *search_urls, *DEFAULT_REDDIT_URLS]
        seen_logical_urls: set[str] = set()

        for url in logical_urls:
            if url in seen_logical_urls:
                continue
            seen_logical_urls.add(url)

            posts = []
            for candidate_url in _reddit_url_variants(url):
                try:
                    response = requests.get(
                        candidate_url,
                        headers=self._headers(),
                        timeout=self._timeout(),
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception:
                    continue

                successful_responses += 1
                posts = payload.get("data", {}).get("children", [])
                if posts:
                    break

            if not posts:
                continue
            for post in posts:
                data = post.get("data", {})
                permalink = data.get("permalink")
                if not permalink:
                    continue

                raw_row = {
                    "external_id": data.get("id", ""),
                    "title": data.get("title", ""),
                    "company": data.get("subreddit_name_prefixed", "").replace("r/", ""),
                    "url": f"https://reddit.com{permalink}",
                    "body": "\n".join(
                        part
                        for part in (
                            data.get("selftext", ""),
                            data.get("link_flair_text", "") or "",
                        )
                        if part
                    ),
                    "posted_at": datetime.fromtimestamp(
                        float(data.get("created_utc", 0) or 0),
                        tz=UTC,
                    ).replace(microsecond=0).isoformat(),
                }
                if not _looks_like_reddit_job_post(raw_row["title"], raw_row["body"]):
                    continue
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )

        if successful_responses == 0:
            raise RuntimeError("Reddit endpoints blocked or unreachable from this runtime.")

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        return filtered


class WorkanaConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        search_urls = config.get("search_urls") or list(DEFAULT_WORKANA_SEARCH_URLS)
        candidate_limit = max(1, _safe_int(config.get("candidate_limit"), default=18))
        normalized: list[NormalizedOpportunity] = []
        seen_job_urls: set[str] = set()
        successful_pages = 0

        for search_url in search_urls:
            try:
                response = requests.get(search_url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
            except Exception:
                continue

            successful_pages += 1
            soup = BeautifulSoup(response.text, "html.parser")
            for job_url in _extract_workana_job_links(search_url, soup):
                if job_url in seen_job_urls:
                    continue
                seen_job_urls.add(job_url)

                raw_row = self._fetch_job_detail(job_url)
                if raw_row is None:
                    continue
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )
                if len(seen_job_urls) >= candidate_limit:
                    break

            if len(seen_job_urls) >= candidate_limit:
                break

        if successful_pages == 0:
            raise RuntimeError("Workana search pages unreachable.")

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        return filtered

    def _fetch_job_detail(self, job_url: str) -> dict | None:
        try:
            response = requests.get(job_url, headers=self._headers(), timeout=self._timeout())
            response.raise_for_status()
        except Exception:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        title = _clean_workana_title(
            _first_non_empty(
                _meta_content(soup, "property", "og:title"),
                _meta_content(soup, "name", "twitter:title"),
                soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "",
                soup.title.get_text(" ", strip=True) if soup.title else "",
            )
        )
        page_text = html_to_text(response.text)
        meta_description = _meta_content(soup, "name", "description")
        raw_text = "\n".join(
            part.strip()
            for part in (
                meta_description,
                page_text[:5000],
            )
            if part and str(part).strip()
        )

        if not title or not raw_text:
            return None

        return {
            "external_id": _workana_external_id(job_url),
            "title": title,
            "company": _extract_workana_client(page_text),
            "url": job_url,
            "body": raw_text,
            "posted_at": _extract_workana_posted_at(page_text),
        }


class GreenhouseConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        boards = config.get("boards") or []
        normalized: list[NormalizedOpportunity] = []

        for board in boards:
            url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
            try:
                response = requests.get(url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue

            for job in payload.get("jobs", []):
                raw_row = {
                    "external_id": f"{board}-{job.get('id', '')}",
                    "title": job.get("title", ""),
                    "company": board,
                    "url": job.get("absolute_url", ""),
                    "body": " ".join(
                        filter(
                            None,
                            (
                                job.get("location", {}).get("name", ""),
                                job.get("updated_at", ""),
                            ),
                        )
                    ),
                    "posted_at": job.get("updated_at", ""),
                }
                if raw_row["url"]:
                    normalized.append(
                        self._normalize(
                            policy_row=policy_row,
                            source_label=policy_row["display_name"],
                            raw=raw_row,
                        )
                    )

        normalized = self._dedupe_by_url(normalized)
        return self._apply_policy_filters(policy_row, normalized)


class LeverConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        companies = config.get("companies") or []
        normalized: list[NormalizedOpportunity] = []

        for company in companies:
            url = f"https://api.lever.co/v0/postings/{company}?mode=json"
            try:
                response = requests.get(url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue

            for job in payload:
                description = html_to_text(job.get("description", ""))
                requirements = html_to_text(job.get("lists", [{}])[0].get("content", "")) if job.get("lists") else ""
                raw_row = {
                    "external_id": f"{company}-{job.get('id', '')}",
                    "title": job.get("text", ""),
                    "company": company,
                    "url": job.get("hostedUrl", ""),
                    "body": " ".join(filter(None, (description, requirements))),
                    "posted_at": job.get("createdAt", ""),
                    "location": job.get("categories", {}).get("location", ""),
                }
                if raw_row["url"]:
                    normalized.append(
                        self._normalize(
                            policy_row=policy_row,
                            source_label=policy_row["display_name"],
                            raw=raw_row,
                        )
                    )

        normalized = self._dedupe_by_url(normalized)
        return self._apply_policy_filters(policy_row, normalized)


class WeWorkRemotelyConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        rss_urls = config.get("rss_urls") or list(DEFAULT_WWR_RSS_URLS)
        normalized: list[NormalizedOpportunity] = []

        for rss_url in rss_urls:
            try:
                response = requests.get(rss_url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
                root = ET.fromstring(response.text)
            except Exception:
                continue

            for item in root.findall("./channel/item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = html_to_text(item.findtext("description", ""))
                company, role = _split_company_role(title)
                raw_row = {
                    "external_id": (item.findtext("guid") or link or title).strip(),
                    "title": role or title,
                    "company": company,
                    "url": link,
                    "body": description,
                    "location": "remote",
                    "posted_at": _to_iso_datetime(item.findtext("pubDate", "")),
                }
                if raw_row["url"]:
                    normalized.append(
                        self._normalize(
                            policy_row=policy_row,
                            source_label=policy_row["display_name"],
                            raw=raw_row,
                        )
                    )

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        return filtered


class HackerNewsJobsConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        endpoint = str(
            config.get("endpoint") or "https://hacker-news.firebaseio.com/v0/jobstories.json"
        ).strip()
        item_url_template = str(
            config.get("item_url_template") or "https://hacker-news.firebaseio.com/v0/item/{id}.json"
        ).strip()
        candidate_limit = max(1, _safe_int(config.get("candidate_limit"), default=60))

        try:
            response = requests.get(endpoint, headers=self._headers(), timeout=self._timeout())
            response.raise_for_status()
            story_ids = response.json()
        except Exception as exc:
            raise RuntimeError("Hacker News jobs API unreachable.") from exc

        normalized: list[NormalizedOpportunity] = []
        for story_id in story_ids[:candidate_limit]:
            try:
                item_response = requests.get(
                    item_url_template.format(id=story_id),
                    headers=self._headers(),
                    timeout=self._timeout(),
                )
                item_response.raise_for_status()
                job = item_response.json()
            except Exception:
                continue

            if not isinstance(job, dict) or job.get("deleted") or job.get("dead"):
                continue

            title = html_to_text(job.get("title", ""))
            body = html_to_text(job.get("text", ""))
            apply_url = (job.get("url") or f"https://news.ycombinator.com/item?id={story_id}").strip()
            company = _extract_company_from_hn_title(title) or str(job.get("by", "")).strip()
            raw_row = {
                "external_id": str(job.get("id", story_id)),
                "title": title,
                "company": company,
                "url": apply_url,
                "body": body,
                "posted_at": _to_iso_datetime(job.get("time")),
            }
            if raw_row["url"]:
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        return filtered


class GitHubIssuesConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        queries = [
            str(query).strip()
            for query in config.get("queries", [])
            if str(query).strip()
        ]
        if not queries:
            return []

        api_url = str(current_app.config.get("GITHUB_API_URL", "https://api.github.com")).rstrip("/")
        per_page = max(1, min(100, _safe_int(config.get("per_page"), default=20)))
        max_items = max(1, _safe_int(config.get("max_items"), default=25))
        sort = str(config.get("sort") or "updated").strip()
        order = str(config.get("order") or "desc").strip()
        commercial_terms = tuple(
            str(term).strip().lower()
            for term in config.get("commercial_signal_terms", [])
            if str(term).strip()
        )
        require_commercial_signal = bool(config.get("require_commercial_signal", True))

        normalized: list[NormalizedOpportunity] = []
        seen_urls: set[str] = set()
        successful_responses = 0

        for query in queries:
            try:
                response = requests.get(
                    f"{api_url}/search/issues",
                    headers=self._github_headers(),
                    params={
                        "q": query[:256],
                        "sort": sort,
                        "order": order,
                        "per_page": per_page,
                    },
                    timeout=self._timeout(),
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue

            successful_responses += 1
            for issue in payload.get("items", []) or []:
                if issue.get("pull_request"):
                    continue

                issue_url = str(issue.get("html_url", "") or "").strip()
                if not issue_url or issue_url in seen_urls:
                    continue

                labels = [
                    str(label.get("name", "")).strip()
                    for label in issue.get("labels", []) or []
                    if isinstance(label, dict) and str(label.get("name", "")).strip()
                ]
                repository = _github_repository_name(issue)
                body = html_to_text(str(issue.get("body", "") or ""))
                raw_text = "\n".join(
                    part
                    for part in (
                        issue.get("title", ""),
                        repository,
                        " ".join(labels),
                        body,
                    )
                    if part
                )
                if require_commercial_signal and not _has_commercial_signal(
                    raw_text,
                    labels=labels,
                    terms=commercial_terms,
                ):
                    continue

                seen_urls.add(issue_url)
                raw_row = {
                    "external_id": str(issue.get("id", "") or issue_url),
                    "source_type": "github_issue",
                    "title": str(issue.get("title", "") or "").strip(),
                    "company": repository,
                    "buyer_name": repository,
                    "buyer_domain": "github.com",
                    "url": issue_url,
                    "apply_url": issue_url,
                    "body": raw_text,
                    "posted_at": _to_iso_datetime(issue.get("updated_at") or issue.get("created_at")),
                    "required_skills": labels,
                }
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )
                if len(normalized) >= max_items:
                    break

            if len(normalized) >= max_items:
                break

        if successful_responses == 0:
            raise RuntimeError("GitHub Search API unreachable or rate limited.")

        filtered = self._apply_policy_filters(policy_row, self._dedupe_by_url(normalized))
        return filtered

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": current_app.config["INGEST_USER_AGENT"],
        }
        token = str(current_app.config.get("GITHUB_TOKEN", "") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


class SamGovConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        api_key = str(config.get("api_key") or current_app.config.get("SAM_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("SAM_API_KEY is required for SAM.gov Contract Opportunities.")

        endpoint = str(
            config.get("endpoint")
            or current_app.config.get("SAM_OPPORTUNITIES_URL")
            or "https://api.sam.gov/opportunities/v2/search"
        ).strip()
        posted_from, posted_to = _sam_date_window(config)
        limit_per_query = max(1, min(1000, _safe_int(config.get("limit_per_query"), default=25)))
        max_items = max(1, _safe_int(config.get("max_items"), default=30))

        normalized: list[NormalizedOpportunity] = []
        seen_urls: set[str] = set()
        successful_responses = 0

        for params in _sam_request_plan(config):
            request_params = {
                "api_key": api_key,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": limit_per_query,
                "offset": 0,
                **params,
            }
            try:
                response = requests.get(
                    endpoint,
                    headers=self._headers(),
                    params=request_params,
                    timeout=max(self._timeout(), 30),
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:
                continue

            successful_responses += 1
            for record in payload.get("opportunitiesData", []) or []:
                if not isinstance(record, dict):
                    continue

                opportunity_url = _sam_record_url(record)
                if not opportunity_url or opportunity_url in seen_urls:
                    continue

                seen_urls.add(opportunity_url)
                body = _sam_record_text(record)
                raw_row = {
                    "external_id": str(record.get("noticeId", "") or opportunity_url),
                    "source_type": "procurement",
                    "title": str(record.get("title", "") or "").strip(),
                    "company": _sam_organization_name(record),
                    "buyer_name": _sam_organization_name(record),
                    "country": "US",
                    "url": opportunity_url,
                    "apply_url": opportunity_url,
                    "body": body,
                    "posted_at": _to_iso_datetime(record.get("postedDate")),
                    "deadline_at": _to_iso_datetime(_sam_deadline_value(record)),
                    "contact_signals": _sam_contact_signals(record),
                }
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )
                if len(normalized) >= max_items:
                    break

            if len(normalized) >= max_items:
                break

        if successful_responses == 0:
            raise RuntimeError("SAM.gov API unreachable or returned no successful responses.")

        filtered = self._apply_policy_filters(policy_row, self._dedupe_by_url(normalized))
        return filtered


class PublicPagesConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        urls = config.get("urls") or []
        normalized: list[NormalizedOpportunity] = []

        for url in urls:
            try:
                response = requests.get(url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
            except Exception:
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            links = soup.select("a[href]")
            for link in links[:120]:
                title = " ".join(link.stripped_strings)
                href = urljoin(url, link.get("href", "").strip())
                if not title or len(title) < 12:
                    continue
                if not href.startswith("http"):
                    continue
                if not _looks_like_job_link(href):
                    continue
                raw_row = {
                    "external_id": href,
                    "title": unescape(title),
                    "company": url,
                    "url": href,
                    "body": unescape(title),
                    "posted_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                }
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )

        normalized = self._dedupe_by_url(normalized)
        return self._apply_policy_filters(policy_row, normalized)


class EmailAlertsConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        return []


class HackerNewsAlgoliaSignalsConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        connector = connector_for_provider("hn_algolia")
        result = connector.fetch(self._runtime_config(config)) if connector else None
        if result is None:
            return []
        if result.status in ERROR_STATUSES:
            raise RuntimeError(result.error_message or result.status)

        normalized = [
            self._normalize(policy_row=policy_row, source_label=policy_row["display_name"], raw=raw)
            for raw in result.opportunities
            if raw.get("url")
        ]
        return self._apply_policy_filters(policy_row, self._dedupe_by_url(normalized))

    def _runtime_config(self, config: dict) -> dict:
        return {
            **config,
            "timeout_seconds": current_app.config.get("CONNECTOR_TIMEOUT_SECONDS", self._timeout()),
            "max_results": current_app.config.get("CONNECTOR_MAX_RESULTS_PER_SOURCE", 50),
            "user_agent": current_app.config.get("INGEST_USER_AGENT"),
        }


class TargetCompanySignalsConnector(BaseConnector):
    provider = ""

    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        provider = str(config.get("provider") or self.provider).strip().lower()
        connector = connector_for_provider(provider)
        if connector is None:
            raise RuntimeError(f"Sin conector ATS para {provider}.")

        targets = get_db().execute(
            """
            SELECT *
            FROM target_companies
            WHERE enabled = 1
              AND ats_provider = ?
              AND ats_slug <> ''
            ORDER BY
                CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                COALESCE(last_checked_at, '') ASC,
                name ASC
            LIMIT ?
            """,
            (
                provider,
                max(1, _safe_int(config.get("target_limit"), default=25)),
            ),
        ).fetchall()
        if not targets:
            return []

        normalized: list[NormalizedOpportunity] = []
        errors: list[str] = []
        db = get_db()
        for target in targets:
            runtime_config = {
                **config,
                "target_company": dict(target),
                "timeout_seconds": current_app.config.get("CONNECTOR_TIMEOUT_SECONDS", self._timeout()),
                "max_results": current_app.config.get("CONNECTOR_MAX_RESULTS_PER_SOURCE", 50),
                "user_agent": current_app.config.get("INGEST_USER_AGENT"),
            }
            if provider == "workable":
                runtime_config["api_token"] = current_app.config.get("WORKABLE_API_TOKEN") or config.get("api_token")
                runtime_config["enabled"] = bool(config.get("enabled", True))

            result = connector.fetch(runtime_config)
            db.execute(
                "UPDATE target_companies SET last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (target["id"],),
            )
            if result.status in ERROR_STATUSES:
                errors.append(f"{target['name']}: {result.error_message or result.status}")
                continue
            if result.normalized_count:
                db.execute(
                    "UPDATE target_companies SET last_signal_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (target["id"],),
                )

            for raw in result.opportunities:
                if not raw.get("url"):
                    continue
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw,
                    )
                )

        filtered = self._apply_policy_filters(policy_row, self._dedupe_by_url(normalized))
        if errors and not filtered:
            raise RuntimeError("; ".join(errors[:3]))
        return filtered


class GreenhouseTargetSignalsConnector(TargetCompanySignalsConnector):
    provider = "greenhouse"


class LeverTargetSignalsConnector(TargetCompanySignalsConnector):
    provider = "lever"


class AshbyTargetSignalsConnector(TargetCompanySignalsConnector):
    provider = "ashby"


class WorkableTargetSignalsConnector(TargetCompanySignalsConnector):
    provider = "workable"


class ProcurementLiteSignalsConnector(BaseConnector):
    provider = ""

    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        provider = self.provider or policy_row["source_key"]
        connector = connector_for_provider(provider)
        if connector is None:
            raise RuntimeError(f"Sin conector procurement para {provider}.")
        runtime_config = {
            **config,
            "timeout_seconds": current_app.config.get("CONNECTOR_TIMEOUT_SECONDS", self._timeout()),
            "max_results": current_app.config.get("CONNECTOR_MAX_RESULTS_PER_SOURCE", 50),
            "user_agent": current_app.config.get("INGEST_USER_AGENT"),
            "source_label": policy_row["display_name"],
        }
        result = connector.fetch(runtime_config)
        if result.status in ERROR_STATUSES:
            raise RuntimeError(result.error_message or result.status)
        normalized = [
            self._normalize(policy_row=policy_row, source_label=policy_row["display_name"], raw=raw)
            for raw in result.opportunities
            if raw.get("url")
        ]
        return self._apply_policy_filters(policy_row, self._dedupe_by_url(normalized))


class TEDEUProcurementConnector(ProcurementLiteSignalsConnector):
    provider = "ted_eu"


class UKFindTenderProcurementConnector(ProcurementLiteSignalsConnector):
    provider = "uk_find_tender"


class UKContractsFinderProcurementConnector(ProcurementLiteSignalsConnector):
    provider = "uk_contracts_finder"


class WorldBankProcurementLiteConnector(ProcurementLiteSignalsConnector):
    provider = "worldbank_procurement"


def connector_registry() -> dict[str, BaseConnector]:
    return {
        "sample_feed": SampleFeedConnector(),
        "reddit": RedditConnector(),
        "workana_projects": WorkanaConnector(),
        "greenhouse": GreenhouseConnector(),
        "lever": LeverConnector(),
        "weworkremotely": WeWorkRemotelyConnector(),
        "hackernews_jobs": HackerNewsJobsConnector(),
        "hn_algolia": HackerNewsAlgoliaSignalsConnector(),
        "greenhouse_jobs": GreenhouseTargetSignalsConnector(),
        "lever_postings": LeverTargetSignalsConnector(),
        "ashby_jobs": AshbyTargetSignalsConnector(),
        "workable_jobs": WorkableTargetSignalsConnector(),
        "ted_eu": TEDEUProcurementConnector(),
        "uk_find_tender": UKFindTenderProcurementConnector(),
        "uk_contracts_finder": UKContractsFinderProcurementConnector(),
        "worldbank_procurement": WorldBankProcurementLiteConnector(),
        "github_issues": GitHubIssuesConnector(),
        "sam_gov": SamGovConnector(),
        "public_pages": PublicPagesConnector(),
        "email_alerts": EmailAlertsConnector(),
    }


def html_to_text(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return " ".join(soup.stripped_strings)


def detect_stack(raw_text: str) -> list[str]:
    lowered = raw_text.lower()
    found = []
    for term in STACK_TERMS:
        if term in lowered:
            label = term.upper() if len(term) <= 3 else term.title()
            found.append(label)
    return found[:8]


def detect_sector(raw_text: str) -> str:
    lowered = raw_text.lower()
    for sector, terms in SECTOR_TERMS.items():
        if any(term in lowered for term in terms):
            return sector
    return "General Tech"


def detect_pain_signals(raw_text: str) -> list[str]:
    lowered = raw_text.lower()
    signals = []
    patterns = (
        ("automation", "Necesita automatizacion operativa"),
        ("scraping", "Necesita extraccion o normalizacion de datos"),
        ("scraper", "Necesita extraccion o normalizacion de datos"),
        ("api", "Necesita integracion API"),
        ("integration", "Necesita integracion de sistemas"),
        ("integracion", "Necesita integracion de sistemas"),
        ("dashboard", "Necesita dashboard o visibilidad operativa"),
        ("data pipeline", "Necesita pipeline de datos"),
        ("etl", "Necesita pipeline ETL"),
        ("urgent", "Menciona urgencia"),
        ("asap", "Menciona urgencia"),
        ("deadline", "Tiene fecha limite explicita"),
        ("response deadline", "Tiene fecha limite explicita"),
        ("migration", "Necesita migracion tecnica"),
        ("bug", "Tiene problema tecnico abierto"),
    )
    for token, label in patterns:
        if token in lowered and label not in signals:
            signals.append(label)
    return signals[:6]


def extract_contact_signals(raw_text: str) -> list[str]:
    signals = []
    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", raw_text or ""):
        signals.append(f"email:{email}")
    phone_match = re.search(r"(?i)(?:phone|tel|telefono|teléfono)[:\s]+([+\d][\d\s().-]{6,})", raw_text or "")
    if phone_match:
        signals.append(f"phone:{phone_match.group(1).strip()}")
    if "contact form" in (raw_text or "").lower():
        signals.append("contact_form")
    return signals[:6]


def build_evidence_snippets(
    raw_text: str,
    *,
    required_skills: list[str],
    pain_signals: list[str],
) -> list[str]:
    text = " ".join((raw_text or "").split())
    if not text:
        return []

    lowered = text.lower()
    needles = [
        *[skill.lower() for skill in required_skills[:5]],
        "budget",
        "usd",
        "$",
        "deadline",
        "urgent",
        "asap",
        "automation",
        "dashboard",
        "api",
    ]
    snippets = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        sentence_lower = sentence.lower()
        if any(needle and needle in sentence_lower for needle in needles):
            snippets.append(sentence[:260].strip())
        if len(snippets) >= 3:
            break
    if not snippets and pain_signals:
        snippets.append(text[:260].strip())
    return snippets


def extract_deadline(raw_text: str) -> str | None:
    text = " ".join((raw_text or "").split())
    match = re.search(
        r"(?i)(?:response deadline|deadline|due|fecha limite|fecha límite)[:\s]+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
        text,
    )
    if not match:
        return None
    return _to_iso_datetime(match.group(1))


def assess_risk(
    raw_text: str,
    policy_risk: str,
    budget_min: int | None,
    budget_max: int | None,
) -> tuple[str, list[str], bool]:
    lowered = raw_text.lower()
    reasons = []
    suspicious = False

    if policy_risk in {"medium", "high"}:
        reasons.append(f"Fuente clasificada como {policy_risk}.")

    if budget_min is None and budget_max is None:
        reasons.append("Sin presupuesto visible.")

    for pattern in SCAM_PATTERNS:
        if pattern in lowered:
            reasons.append(f"Indicador sospechoso: {pattern}.")
            suspicious = True

    if suspicious:
        return "high", reasons, True
    if policy_risk == "high" or len(reasons) >= 3:
        return "high", reasons, False
    if policy_risk == "medium" or reasons:
        return "medium", reasons, False
    return "low", reasons, False


def extract_budget(raw_text: str) -> tuple[int | None, int | None, str, str]:
    budget = parse_commercial_budget(raw_text)
    if not budget.is_valid_commercial_budget:
        return None, None, "", budget.currency
    return budget.amount_min, budget.amount_max, budget.raw_evidence, budget.currency


def _money_to_int(value: str, suffix: str | None) -> int:
    clean = value.replace(",", "").strip()
    number = float(clean)
    if suffix:
        number *= 1000
    return int(number)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_int_or_none(value) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _coerce_list(value) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _domain_from_url(value: str) -> str:
    host = urlparse(str(value or "")).netloc.lower().replace("www.", "").strip()
    return host


def _source_type_for_policy(source_key: str) -> str:
    mapping = {
        "sample_feed": "direct_rfp",
        "reddit": "community",
        "workana_projects": "direct_rfp",
        "greenhouse": "hiring_signal",
        "lever": "hiring_signal",
        "weworkremotely": "hiring_signal",
        "hackernews_jobs": "hiring_signal",
        "hn_algolia": "community_signal",
        "greenhouse_jobs": "hiring_signal",
        "lever_postings": "hiring_signal",
        "ashby_jobs": "hiring_signal",
        "workable_jobs": "hiring_signal",
        "ted_eu": "procurement",
        "uk_find_tender": "procurement",
        "uk_contracts_finder": "procurement",
        "worldbank_procurement": "procurement",
        "github_issues": "github_issue",
        "sam_gov": "procurement",
        "public_pages": "direct_rfp",
        "email_alerts": "direct_rfp",
    }
    return mapping.get(source_key, "direct_rfp")


def _looks_remote(text: str) -> bool:
    keywords = (
        "remote",
        "home based",
        "home-based",
        "homebased",
        "worldwide",
        "anywhere",
        "distributed",
        "work from home",
    )
    return any(keyword in text for keyword in keywords)


def _looks_like_job_link(href: str) -> bool:
    lower = href.lower()
    if any(token in lower for token in ("mailto:", "javascript:", "#")):
        return False
    hints = (
        "/jobs/",
        "/job/",
        "/careers/",
        "/career/",
        "/positions/",
        "/position/",
        "/remote-jobs/",
        "/openings/",
        "/opening/",
    )
    return any(hint in lower for hint in hints)


def _looks_like_reddit_job_post(title: str, body: str) -> bool:
    haystack = f"{title}\n{body}".lower()
    hiring_markers = (
        "[hiring]",
        " hiring ",
        "looking for",
        "contract",
        "freelance",
        "paid",
        "$",
        "budget",
    )
    return any(marker in haystack for marker in hiring_markers)


def _extract_workana_job_links(base_url: str, soup: BeautifulSoup) -> tuple[str, ...]:
    candidates: list[str] = []
    for link in soup.select("a[href]"):
        href = urljoin(base_url, (link.get("href") or "").strip())
        lower = href.lower()
        if not href.startswith("http"):
            continue
        if "/job/" not in lower and "/es/job/" not in lower:
            continue
        normalized = href.split("?", 1)[0].rstrip("/")
        if normalized not in candidates:
            candidates.append(normalized)
    return tuple(candidates)


def _workana_external_id(job_url: str) -> str:
    parsed = urlparse(job_url)
    return (parsed.path.rstrip("/").rsplit("/", 1)[-1] or job_url).strip()


def _clean_workana_title(value: str) -> str:
    cleaned = " ".join((value or "").split())
    suffixes = (
        " - Se Busca Freelancer - Workana",
        " - Trabajo Freelance - Workana",
        " | Workana",
    )
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
    return cleaned


def _extract_workana_client(page_text: str) -> str:
    text = " ".join((page_text or "").split())
    match = re.search(r"([A-ZÁÉÍÓÚÑ0-9][^:]{2,80})\s+\d+\s+Proyectos publicados", text)
    if not match:
        return ""
    candidate = match.group(1).strip(" -|:")
    if len(candidate) > 48 or "Publicado el" in candidate:
        return ""
    return candidate


def _extract_workana_posted_at(page_text: str) -> str | None:
    text = " ".join((page_text or "").split())
    match = re.search(
        r"Publicado el\s+(\d{1,2})\s+([A-Za-zÁÉÍÓÚáéíóúñÑ]+),?\s+(\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month_value = _month_number(match.group(2))
    if month_value is None:
        return None

    try:
        published_at = datetime(
            year=int(match.group(3)),
            month=month_value,
            day=int(match.group(1)),
            tzinfo=UTC,
        )
    except ValueError:
        return None
    return published_at.replace(microsecond=0).isoformat()


def _month_number(value: str) -> int | None:
    months = {
        "january": 1,
        "enero": 1,
        "february": 2,
        "febrero": 2,
        "march": 3,
        "marzo": 3,
        "april": 4,
        "abril": 4,
        "may": 5,
        "mayo": 5,
        "june": 6,
        "junio": 6,
        "july": 7,
        "julio": 7,
        "august": 8,
        "agosto": 8,
        "september": 9,
        "septiembre": 9,
        "october": 10,
        "octubre": 10,
        "november": 11,
        "noviembre": 11,
        "december": 12,
        "diciembre": 12,
    }
    return months.get((value or "").strip().lower())


def _meta_content(soup: BeautifulSoup, attr: str, value: str) -> str:
    tag = soup.find("meta", attrs={attr: value})
    if tag is None:
        return ""
    return str(tag.get("content") or "").strip()


def _first_non_empty(*values: str) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _split_company_role(title: str) -> tuple[str, str]:
    cleaned = " ".join((title or "").split())
    if not cleaned:
        return "", ""

    if ": " in cleaned:
        company, role = cleaned.split(": ", 1)
        if company.strip() and role.strip():
            return company.strip(), role.strip()

    return "", cleaned


def _extract_company_from_hn_title(title: str) -> str:
    cleaned = " ".join((title or "").split())
    lowered = cleaned.lower()

    for marker in (" is hiring", " is looking", " is seeking", " is growing"):
        if marker in lowered:
            return cleaned[: lowered.index(marker)].strip(" -|:")

    company, _role = _split_company_role(cleaned)
    return company


def _github_repository_name(issue: dict) -> str:
    repository = issue.get("repository") or {}
    if isinstance(repository, dict) and repository.get("full_name"):
        return str(repository["full_name"]).strip()

    repository_url = str(issue.get("repository_url", "") or "").strip()
    if repository_url:
        return repository_url.rstrip("/").rsplit("/", 2)[-2] + "/" + repository_url.rstrip("/").rsplit("/", 1)[-1]
    return ""


def _has_commercial_signal(raw_text: str, *, labels: list[str], terms: tuple[str, ...]) -> bool:
    haystack = " ".join([raw_text, " ".join(labels)]).lower()
    if terms and any(term in haystack for term in terms):
        return True
    return bool(re.search(r"(?i)(?:usd|\$)\s*\d", haystack))


def _sam_date_window(config: dict) -> tuple[str, str]:
    days = max(1, min(365, _safe_int(config.get("posted_from_days"), default=21)))
    today = datetime.now(UTC).date()
    start = today - timedelta(days=days)
    return start.strftime("%m/%d/%Y"), today.strftime("%m/%d/%Y")


def _sam_request_plan(config: dict) -> list[dict]:
    query_terms = [
        str(term).strip()
        for term in config.get("query_terms", [])
        if str(term).strip()
    ]
    naics_codes = [
        str(code).strip()
        for code in config.get("naics_codes", [])
        if str(code).strip()
    ]
    notice_types = [
        str(value).strip().lower()
        for value in config.get("notice_types", [])
        if str(value).strip()
    ]
    if not notice_types:
        notice_types = [""]

    plan: list[dict] = []
    for term in query_terms:
        for notice_type in notice_types[:4]:
            params = {"title": term}
            if notice_type:
                params["ptype"] = notice_type
            plan.append(params)

    for naics_code in naics_codes[:4]:
        params = {"ncode": naics_code}
        if notice_types[0]:
            params["ptype"] = notice_types[0]
        plan.append(params)

    unique: list[dict] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for params in plan:
        signature = tuple(sorted((key, str(value)) for key, value in params.items()))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(params)
    return unique[:24] or [{}]


def _sam_record_url(record: dict) -> str:
    for key in ("uiLink", "additionalInfoLink", "description"):
        value = str(record.get(key, "") or "").strip()
        if value and value.lower() != "null" and value.startswith("http"):
            return value

    notice_id = str(record.get("noticeId", "") or "").strip()
    if notice_id:
        return f"https://sam.gov/opp/{notice_id}/view"
    return ""


def _sam_organization_name(record: dict) -> str:
    for key in ("fullParentPathName", "department", "subTier", "office"):
        value = str(record.get(key, "") or "").strip()
        if value and value.lower() != "null":
            return value
    return "SAM.gov"


def _sam_record_text(record: dict) -> str:
    parts = [
        record.get("title", ""),
        _sam_organization_name(record),
        record.get("type", ""),
        record.get("baseType", ""),
        record.get("typeOfSetAsideDescription", ""),
        record.get("naicsCode", ""),
        record.get("classificationCode", ""),
        _sam_deadline_text(record),
        _sam_award_text(record),
        _sam_contacts_text(record),
    ]
    return "\n".join(str(part).strip() for part in parts if str(part or "").strip())


def _sam_deadline_text(record: dict) -> str:
    deadline = _sam_deadline_value(record)
    if not deadline:
        return ""
    return f"Response deadline: {deadline}"


def _sam_deadline_value(record: dict) -> str:
    return str(
        record.get("responseDeadLine")
        or record.get("responseDeadline")
        or record.get("reponseDeadLine")
        or ""
    ).strip()


def _sam_award_text(record: dict) -> str:
    award = record.get("award") or {}
    if not isinstance(award, dict):
        return ""
    amount = str(award.get("amount", "") or "").strip()
    awardee = award.get("awardee") or {}
    awardee_name = ""
    if isinstance(awardee, dict):
        awardee_name = str(awardee.get("name", "") or "").strip()
    fragments = []
    if amount:
        fragments.append(f"Award amount USD {amount}")
    if awardee_name:
        fragments.append(f"Awardee {awardee_name}")
    return ". ".join(fragments)


def _sam_contacts_text(record: dict) -> str:
    contacts = record.get("pointOfContact") or []
    fragments = []
    if isinstance(contacts, dict):
        contacts = [contacts]
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        name = str(contact.get("fullName") or contact.get("fullname") or "").strip()
        email = str(contact.get("email", "") or "").strip()
        phone = str(contact.get("phone", "") or "").strip()
        fragments.append(" ".join(part for part in (name, email, phone) if part))
    return "\n".join(fragment for fragment in fragments if fragment)


def _sam_contact_signals(record: dict) -> list[str]:
    contacts = record.get("pointOfContact") or []
    if isinstance(contacts, dict):
        contacts = [contacts]
    signals = []
    for contact in contacts:
        if not isinstance(contact, dict):
            continue
        email = str(contact.get("email", "") or "").strip()
        phone = str(contact.get("phone", "") or "").strip()
        if email:
            signals.append(f"email:{email}")
        if phone:
            signals.append(f"phone:{phone}")
    return signals[:6]


def _to_iso_datetime(value) -> str | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat()

    if isinstance(value, str) and value.strip():
        raw_value = value.strip()
        try:
            parsed = parsedate_to_datetime(raw_value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
        except (TypeError, ValueError, IndexError, OverflowError):
            try:
                return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).astimezone(UTC).replace(
                    microsecond=0
                ).isoformat()
            except ValueError:
                for date_format in ("%m/%d/%Y", "%d/%m/%Y"):
                    try:
                        return datetime.strptime(raw_value, date_format).replace(tzinfo=UTC).isoformat()
                    except ValueError:
                        continue
                return None

    return None


def _reddit_url_variants(url: str) -> tuple[str, ...]:
    parsed = urlparse(url)
    if not parsed.scheme.startswith("http") or "reddit.com" not in parsed.netloc:
        return (url,)

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "raw_json" for key, _ in query_pairs):
        query_pairs.append(("raw_json", "1"))
    query = urlencode(query_pairs)

    variants: list[str] = []
    for host in (parsed.netloc, "old.reddit.com"):
        candidate = parsed._replace(netloc=host, query=query)
        candidate_url = urlunparse(candidate)
        if candidate_url not in variants:
            variants.append(candidate_url)
    return tuple(variants)
