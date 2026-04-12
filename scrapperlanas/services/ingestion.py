from __future__ import annotations

import json
import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from flask import current_app


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
    budget_min: int | None = None
    budget_max: int | None = None
    budget_text: str = ""
    currency: str = "USD"
    sector: str = ""
    stack: list[str] = field(default_factory=list)
    posted_at: str | None = None
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
            budget_min=budget_min,
            budget_max=budget_max,
            budget_text=budget_text,
            currency=currency,
            sector=sector,
            stack=stack,
            posted_at=raw.get("posted_at"),
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
        blocked_responses = 0
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
                except requests.HTTPError as exc:
                    status_code = getattr(exc.response, "status_code", None)
                    if status_code in {401, 403, 429}:
                        blocked_responses += 1
                    continue
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
            if blocked_responses:
                raise RuntimeError(
                    "Reddit endpoints are blocking this runtime (HTTP 403/429)."
                )
            raise RuntimeError("Reddit endpoints blocked or unreachable from this runtime.")

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        if not filtered:
            raise RuntimeError("Reddit responded, but no posts matched the current filters.")
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
        if not filtered:
            raise RuntimeError("Workana responded, but no projects matched the current filters.")
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
        if not filtered:
            raise RuntimeError("We Work Remotely RSS returned no matches for the current filters.")
        return filtered


class FreelancerComConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        search_urls = config.get("search_urls") or [
            "https://www.freelancer.com/job-search/python/",
            "https://www.freelancer.com/job-search/web-scraping/",
            "https://www.freelancer.com/job-search/n8n/",
            "https://www.freelancer.com/job-search/api/",
        ]
        candidate_limit = max(1, _safe_int(config.get("candidate_limit"), default=25))
        normalized: list[NormalizedOpportunity] = []
        seen_urls: set[str] = set()
        successful_pages = 0

        for search_url in search_urls:
            try:
                response = requests.get(search_url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
            except Exception:
                continue

            successful_pages += 1
            soup = BeautifulSoup(response.text, "html.parser")
            for href, title in _extract_freelancer_job_links(search_url, soup):
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                raw_row = {
                    "external_id": _freelancer_external_id(href),
                    "title": title,
                    "company": "Freelancer.com",
                    "url": href,
                    "body": _freelancer_card_text(soup, href, title),
                    "posted_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                }
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )
                if len(seen_urls) >= candidate_limit:
                    break
            if len(seen_urls) >= candidate_limit:
                break

        if successful_pages == 0:
            raise RuntimeError("Freelancer.com search pages unreachable.")

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        if not filtered:
            raise RuntimeError("Freelancer.com responded, but no projects matched the current filters.")
        return filtered


class PeoplePerHourConnector(BaseConnector):
    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        config = self._policy_config(policy_row)
        search_urls = config.get("search_urls") or [
            "https://www.peopleperhour.com/freelance-jobs?page=1",
            "https://www.peopleperhour.com/freelance-jobs/technology-programming/website-development",
            "https://www.peopleperhour.com/freelance-jobs/technology-programming/python",
        ]
        candidate_limit = max(1, _safe_int(config.get("candidate_limit"), default=20))
        normalized: list[NormalizedOpportunity] = []
        seen_urls: set[str] = set()
        successful_pages = 0

        for search_url in search_urls:
            try:
                response = requests.get(search_url, headers=self._headers(), timeout=self._timeout())
                response.raise_for_status()
            except Exception:
                continue

            successful_pages += 1
            soup = BeautifulSoup(response.text, "html.parser")
            for href, title, card_text in _extract_peopleperhour_job_cards(search_url, soup):
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                raw_row = {
                    "external_id": _peopleperhour_external_id(href),
                    "title": title,
                    "company": "PeoplePerHour",
                    "url": href,
                    "body": card_text,
                    "posted_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                }
                normalized.append(
                    self._normalize(
                        policy_row=policy_row,
                        source_label=policy_row["display_name"],
                        raw=raw_row,
                    )
                )
                if len(seen_urls) >= candidate_limit:
                    break
            if len(seen_urls) >= candidate_limit:
                break

        if successful_pages == 0:
            raise RuntimeError("PeoplePerHour search pages unreachable.")

        normalized = self._dedupe_by_url(normalized)
        filtered = self._apply_policy_filters(policy_row, normalized)
        if not filtered:
            raise RuntimeError("PeoplePerHour responded, but no projects matched the current filters.")
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
        if not filtered:
            raise RuntimeError("Hacker News jobs API returned no matches for the current filters.")
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


class FutureMarketplaceConnector(BaseConnector):
    def __init__(self, source_key: str) -> None:
        self.source_key = source_key

    def fetch(self, policy_row) -> list[NormalizedOpportunity]:
        raise RuntimeError(f"Connector not implemented yet for {self.source_key}.")


def connector_registry() -> dict[str, BaseConnector]:
    return {
        "workana_projects": WorkanaConnector(),
        "weworkremotely": WeWorkRemotelyConnector(),
        "email_alerts": EmailAlertsConnector(),
        "freelancer_com": FreelancerComConnector(),
        "peopleperhour": PeoplePerHourConnector(),
        "upwork": FutureMarketplaceConnector("upwork"),
    }


def html_to_text(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return " ".join(soup.stripped_strings)


def _extract_freelancer_job_links(search_url: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(search_url, anchor.get("href", "").strip())
        if not href.startswith("https://www.freelancer.com/"):
            continue
        if "/projects/" not in href and "/jobs/" not in href and "/job-search/" not in href:
            continue
        if href in seen:
            continue
        text = " ".join(anchor.stripped_strings).strip()
        if not text or len(text) < 8:
            continue
        if _looks_like_navigation_link(text):
            continue
        seen.add(href)
        links.append((href, text))
    return links


def _extract_peopleperhour_job_cards(search_url: str, soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    cards: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(search_url, anchor.get("href", "").strip())
        if not href.startswith("https://www.peopleperhour.com/"):
            continue
        if "/freelance-jobs/" not in href:
            continue
        if not re.search(r"-\d+(?:[/?#]|$)", href):
            continue
        if href in seen:
            continue
        title = " ".join(anchor.stripped_strings).strip()
        if not title or len(title) < 8:
            continue
        card_text = _pph_card_text(anchor)
        if _looks_like_navigation_link(title):
            continue
        seen.add(href)
        cards.append((href, title, card_text))
    return cards


def _pph_card_text(anchor) -> str:
    pieces = []
    if anchor is not None:
        pieces.append(anchor.get_text(" ", strip=True))
        parent = anchor.parent
        if parent is not None:
            pieces.append(parent.get_text(" ", strip=True))
            grand = parent.parent
            if grand is not None:
                pieces.append(grand.get_text(" ", strip=True))
    return " ".join(part for part in pieces if part).strip()


def _peopleperhour_external_id(url: str) -> str:
    match = re.search(r"/freelance-jobs/(?:[^/]+-)?(\d+)", url)
    if match:
        return f"pph-{match.group(1)}"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"pph-{digest}"


def _freelancer_external_id(url: str) -> str:
    match = re.search(r"/projects/(?:[^/]+-)?(\d+)", url)
    if match:
        return f"freelancer-{match.group(1)}"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"freelancer-{digest}"


def _freelancer_card_text(soup: BeautifulSoup, href: str, title: str) -> str:
    anchor = soup.find("a", href=re.compile(re.escape(href)))
    if anchor:
        parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
        if parent_text and len(parent_text) > len(title):
            return parent_text
    return title


def _looks_like_navigation_link(text: str) -> bool:
    lowered = text.lower()
    return lowered in {
        "next",
        "previous",
        "first",
        "last",
        "bid now",
        "apply now",
        "search",
        "browse jobs",
        "freelancer",
    }


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
    range_match = re.search(
        r"(?i)(?:usd|\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|to|hasta|y)\s*(?:usd|\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?(?:\s*usd)?",
        raw_text,
    )
    if range_match:
        first = _money_to_int(range_match.group(1), range_match.group(2))
        second = _money_to_int(range_match.group(3), range_match.group(4))
        budget_text = range_match.group(0).replace("  ", " ").strip()
        return first, second, budget_text, "USD"

    single_matches = re.findall(r"(?i)(?:usd|\$)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", raw_text)
    suffix_matches = re.findall(r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*usd\b", raw_text)
    amount_matches = [*single_matches, *suffix_matches]
    if amount_matches:
        values = [_money_to_int(amount, suffix) for amount, suffix in amount_matches]
        if values:
            first_value = values[0]
            budget_text = f"USD {first_value:,}"
            return first_value, max(values), budget_text, "USD"

    return None, None, "", "USD"


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
