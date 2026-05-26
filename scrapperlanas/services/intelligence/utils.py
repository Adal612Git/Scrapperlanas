from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import urlparse


TECHNICAL_TERMS = {
    "api",
    "automation",
    "automatizacion",
    "backend",
    "cloud",
    "dashboard",
    "data",
    "docker",
    "etl",
    "flask",
    "ia",
    "integration",
    "integracion",
    "llm",
    "n8n",
    "pipeline",
    "postgres",
    "postgresql",
    "python",
    "scraping",
    "sql",
}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def lower_text(value: Any) -> str:
    return clean_text(value).lower()


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, tuple | set):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,;\n]", text) if part.strip()]


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def budget_amount(opportunity: Mapping[str, Any]) -> int:
    for key in ("estimated_value", "budget_amount", "budget_max", "budget_min", "proposal_value", "won_value"):
        amount = int_value(opportunity.get(key), default=0)
        if amount > 0:
            return amount
    return 0


def combined_text(opportunity: Mapping[str, Any]) -> str:
    parts = [
        opportunity.get("title"),
        opportunity.get("description"),
        opportunity.get("raw_text"),
        opportunity.get("ai_summary"),
        opportunity.get("company"),
        opportunity.get("buyer_name"),
        " ".join(coerce_list(opportunity.get("skills") or opportunity.get("required_skills") or opportunity.get("stack"))),
        " ".join(coerce_list(opportunity.get("pain_signals"))),
        " ".join(coerce_list(opportunity.get("evidence") or opportunity.get("evidence_snippets"))),
    ]
    return lower_text(" ".join(clean_text(part) for part in parts if clean_text(part)))


def normalize_domain(value: Any) -> str:
    text = lower_text(value)
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    domain = parsed.netloc or parsed.path
    domain = domain.removeprefix("www.")
    return domain.split("/", 1)[0].strip()


def unique(values: list[str], *, limit: int | None = None) -> list[str]:
    seen = set()
    output = []
    for value in values:
        cleaned = clean_text(value)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
        if limit is not None and len(output) >= limit:
            break
    return output


def technical_matches(opportunity: Mapping[str, Any]) -> list[str]:
    text = combined_text(opportunity)
    skills = {lower_text(skill) for skill in coerce_list(opportunity.get("skills") or opportunity.get("required_skills") or opportunity.get("stack"))}
    matches = sorted(term for term in TECHNICAL_TERMS if term in text or term in skills)
    return [match.upper() if match in {"api", "etl", "sql", "llm"} else match.title() for match in matches]
