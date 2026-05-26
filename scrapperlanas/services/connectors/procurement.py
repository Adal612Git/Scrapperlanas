from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Mapping

from .base import compact_text, extract_skills, html_to_text


PROCUREMENT_TERMS = (
    "software",
    "application",
    "web app",
    "platform",
    "automation",
    "dashboard",
    "data",
    "analytics",
    "reporting",
    "business intelligence",
    "api",
    "integration",
    "cloud",
    "cybersecurity",
    "cyber security",
    "digital",
    "database",
    "ai",
    "artificial intelligence",
    "machine learning",
    "internal tool",
    "workflow",
)

PROCUREMENT_EXCLUDE_TERMS = (
    "janitorial",
    "cleaning",
    "catering",
    "vehicle",
    "furniture",
    "construction",
    "road works",
    "facilities management",
    "medical supplies",
    "food service",
)

TECH_CPV_PREFIXES = ("48", "72")


def procurement_fit(
    text: str,
    *,
    cpv_codes: list[str] | None = None,
    include_terms: list[str] | tuple[str, ...] | None = None,
    exclude_terms: list[str] | tuple[str, ...] | None = None,
    cpv_prefixes: list[str] | tuple[str, ...] | None = None,
) -> bool:
    lowered = (text or "").lower()
    excludes = tuple(str(term).lower() for term in (exclude_terms or PROCUREMENT_EXCLUDE_TERMS))
    if any(term and term in lowered for term in excludes):
        return False

    prefixes = tuple(str(prefix).strip() for prefix in (cpv_prefixes or TECH_CPV_PREFIXES) if str(prefix).strip())
    if any(str(code or "").strip().startswith(prefixes) for code in (cpv_codes or []) if str(code or "").strip()):
        return True

    includes = tuple(str(term).lower() for term in (include_terms or PROCUREMENT_TERMS))
    return any(term and term in lowered for term in includes)


def normalize_ocds_release(
    release: Mapping[str, Any],
    *,
    source_name: str,
    source_label: str,
    default_country: str = "",
    notice_url_template: str = "",
    include_terms: list[str] | tuple[str, ...] | None = None,
    exclude_terms: list[str] | tuple[str, ...] | None = None,
    cpv_prefixes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    tender = release.get("tender") if isinstance(release.get("tender"), dict) else {}
    buyer = release.get("buyer") if isinstance(release.get("buyer"), dict) else {}
    parties = release.get("parties") if isinstance(release.get("parties"), list) else []
    notice_id = str(release.get("id") or release.get("ocid") or tender.get("id") or "").strip()
    title = str(tender.get("title") or release.get("title") or "").strip()
    description = html_to_text(str(tender.get("description") or ""))
    buyer_name = str(buyer.get("name") or _buyer_from_parties(parties) or "").strip()
    country = _country_from_release(tender, parties) or default_country
    value = tender.get("value") if isinstance(tender.get("value"), dict) else {}
    amount = _safe_int_or_none(value.get("amount") or value.get("amountGross"))
    currency = str(value.get("currency") or "GBP").strip() or "GBP"
    deadline = _first_nested_date(tender, ("tenderPeriod", "endDate"), ("enquiryPeriod", "endDate"))
    published_at = _to_iso_datetime(release.get("date"))
    cpv_codes = _cpv_codes_from_tender(tender)
    cpv_text = " ".join(_cpv_descriptions_from_tender(tender))
    contact_signals = _contact_signals(parties)
    document_url = _first_document_url(tender)
    url = _notice_url(source_name, notice_id, notice_url_template, document_url)
    raw_text = compact_text(
        "\n".join(
            part
            for part in (
                title,
                buyer_name,
                description,
                cpv_text,
                str(tender.get("procurementMethodDetails") or ""),
                str(tender.get("status") or ""),
            )
            if part
        ),
        limit=5200,
    )
    if not notice_id or not title or not procurement_fit(
        raw_text,
        cpv_codes=cpv_codes,
        include_terms=include_terms,
        exclude_terms=exclude_terms,
        cpv_prefixes=cpv_prefixes,
    ):
        return {}

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
    pain = [
        "Procurement estructurado con proceso formal.",
        "Encaja con software, datos, automatizacion, cloud o herramientas internas.",
    ]
    if deadline:
        pain.append("Tiene fecha limite publicada.")

    return {
        "external_id": f"{source_name}:{notice_id}",
        "source_name": source_name,
        "source_type": "procurement",
        "title": title,
        "company": buyer_name,
        "buyer_name": buyer_name,
        "buyer_domain": "",
        "country": country,
        "url": url,
        "apply_url": document_url or url,
        "raw_text": raw_text,
        "body": raw_text,
        "posted_at": published_at,
        "deadline_at": deadline,
        "estimated_value": amount,
        "currency": currency,
        "required_skills": skills,
        "pain_signals": pain,
        "contact_signals": contact_signals,
        "evidence_snippets": evidence[:5],
        "next_best_action": "Revisar pliego estructurado y preparar respuesta formal",
        "source_label": source_label,
    }


def to_iso_date_days_ago(days_back: int) -> str:
    return (datetime.now(UTC) - timedelta(days=max(0, int(days_back)))).strftime("%Y-%m-%dT00:00:00Z")


def _buyer_from_parties(parties: list[Any]) -> str:
    for party in parties:
        if not isinstance(party, dict):
            continue
        roles = party.get("roles") if isinstance(party.get("roles"), list) else []
        if "buyer" in roles and party.get("name"):
            return str(party.get("name") or "").strip()
    return ""


def _country_from_release(tender: Mapping[str, Any], parties: list[Any]) -> str:
    for item in tender.get("items") or []:
        if not isinstance(item, dict):
            continue
        for address in item.get("deliveryAddresses") or []:
            if isinstance(address, dict):
                value = str(address.get("countryName") or address.get("country") or "").strip()
                if value:
                    return value
    for party in parties:
        if not isinstance(party, dict):
            continue
        address = party.get("address") if isinstance(party.get("address"), dict) else {}
        value = str(address.get("countryName") or address.get("countryName") or "").strip()
        if value:
            return value
    return ""


def _cpv_codes_from_tender(tender: Mapping[str, Any]) -> list[str]:
    codes = []
    for classification in _classifications(tender):
        code = str(classification.get("id") or "").strip()
        scheme = str(classification.get("scheme") or "").lower()
        if code and (not scheme or "cpv" in scheme):
            codes.append(code)
    return list(dict.fromkeys(codes))


def _cpv_descriptions_from_tender(tender: Mapping[str, Any]) -> list[str]:
    descriptions = []
    for classification in _classifications(tender):
        description = str(classification.get("description") or "").strip()
        if description:
            descriptions.append(description)
    return list(dict.fromkeys(descriptions))


def _classifications(tender: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = []
    for key in ("classification", "additionalClassifications"):
        value = tender.get(key)
        if isinstance(value, dict):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, dict))
    for item in tender.get("items") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("classification")
        if isinstance(value, dict):
            values.append(value)
        for classification in item.get("additionalClassifications") or []:
            if isinstance(classification, dict):
                values.append(classification)
    return values


def _contact_signals(parties: list[Any]) -> list[str]:
    signals = []
    for party in parties:
        if not isinstance(party, dict):
            continue
        contact = party.get("contactPoint") if isinstance(party.get("contactPoint"), dict) else {}
        email = str(contact.get("email") or "").strip()
        url = str(contact.get("url") or party.get("uri") or "").strip()
        if email:
            signals.append(f"email:{email}")
        if url:
            signals.append(f"portal:{url}")
    return signals[:6]


def _first_document_url(tender: Mapping[str, Any]) -> str:
    for document in tender.get("documents") or []:
        if isinstance(document, dict):
            url = str(document.get("url") or "").strip()
            if url and not url.lower().endswith(".pdf"):
                return url
    return ""


def _notice_url(source_name: str, notice_id: str, template: str, fallback: str) -> str:
    if fallback:
        return fallback
    if template and "{id}" in template:
        return template.format(id=notice_id)
    if source_name == "uk_find_tender":
        return f"https://www.find-tender.service.gov.uk/Notice/{notice_id}"
    if source_name == "uk_contracts_finder":
        return f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
    return f"https://loto-signal.local/procurement/{source_name}/{notice_id}"


def _first_nested_date(parent: Mapping[str, Any], *paths: tuple[str, str]) -> str | None:
    for key, child_key in paths:
        child = parent.get(key) if isinstance(parent.get(key), dict) else {}
        parsed = _to_iso_datetime(child.get(child_key))
        if parsed:
            return parsed
    return None


def _to_iso_datetime(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC).replace(microsecond=0).isoformat()
    text = str(value).strip()
    if not text:
        return None
    for parser in (_from_iso, _from_email_date):
        parsed = parser(text)
        if parsed:
            return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
    return text


def _from_iso(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _from_email_date(text: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _safe_int_or_none(value) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def first_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list) and value:
            return value[0]
        if value not in (None, "", []):
            return value
    return None


def text_without_pdf_urls(value: str) -> str:
    return re.sub(r"https?://\S+\.pdf\b", "", str(value or ""), flags=re.IGNORECASE)
