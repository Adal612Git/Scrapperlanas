from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse


REDDIT_ALLOWLIST = {
    "forhire",
    "freelance_forhire",
    "remotework",
    "remotejobs",
    "remotepython",
    "hireaprogrammer",
    "jobs4bitcoins",
    "slavelabour",
    "webdev",
    "smallbusiness",
    "entrepreneur",
    "saas",
    "startups",
    "n8n",
    "automation",
    "nocode",
}

REDDIT_GREYLIST = {
    "python",
    "django",
    "flask",
    "reactjs",
    "node",
    "programming",
    "learnprogramming",
    "datascience",
    "machinelearning",
    "artificial",
    "localllama",
    "webscraping",
}

REDDIT_BLOCKLIST = {
    "nosleep",
    "conspiracy",
    "askreddit",
    "amitheasshole",
    "relationship_advice",
    "worldnews",
    "news",
    "politics",
    "memes",
    "funny",
    "gaming",
    "teenagers",
    "nostupidquestions",
    "showerthoughts",
    "writingprompts",
    "creepypasta",
    "unresolvedmysteries",
    "ufos",
    "aliens",
}

DIRECT_HIRING_TERMS = (
    "hiring",
    "looking for someone",
    "looking for a contractor",
    "need a developer",
    "need an agency",
    "needs a developer",
    "needs an agency",
    "necesito un",
    "necesitamos",
    "buscamos freelance",
    "buscamos freelancer",
    "buscamos desarrollador",
    "se busca freelancer",
    "contratar",
    "proyecto remoto para",
    "seeking contractor",
    "freelancer needed",
    "looking to hire",
    "help us automate",
    "build this",
)

PROCUREMENT_TERMS = (
    "request for proposal",
    "rfp",
    "proposal deadline",
    "submit bid",
    "contract opportunity",
    "procurement",
    "tender",
    "licitacion",
    "contrato publico",
    "fecha limite",
    "statement of work",
    "sow",
)

PAY_TERMS = (
    "budget is",
    "we pay",
    "hourly rate",
    "fixed budget",
    "presupuesto",
    "pago",
    "pagamos",
    "cotizacion",
    "paid",
    "contract",
    "freelance",
    "usd",
    "mxn",
    "eur",
    "gbp",
)

NEGATIVE_INTENT_PATTERNS = {
    "SEO_CONTENT": (
        "how to choose",
        "how to evaluate",
        "guide",
        "tutorial",
        "case study",
        "enterprise ai consultant",
        "magento",
        "hyva",
    ),
    "DISCUSSION": (
        "review my proposal",
        "rate my proposal",
        "what do you think",
        "discussion",
        "could you please rate my proposal",
        "roast my landing page",
    ),
    "FICTION": (
        "story",
        "fiction",
        "creepypasta",
        "basement that doesn't exist",
        "warehouse i work at",
    ),
    "CONSPIRACY": (
        "conspiracy",
        "targeted energy weapon",
        "directed-energy weapon",
        "epstein",
        "ufo",
        "aliens",
    ),
    "NEWS": (
        "breaking news",
        "worldnews",
        "politics",
        "news:",
    ),
    "SELF_PROMO": (
        "show hn",
        "launching my app",
        "saves 3-6 months",
        "generate native salesforce packages",
    ),
    "MARKET_RESEARCH": (
        "tool recommendations",
        "recommend a tool",
        "what tool should i use",
        "which crm",
    ),
}

JOB_AGGREGATOR_PATTERNS = (
    "jobs that opened",
    "developer jobs that opened",
    "30+ developer jobs",
    "remote jobs",
    "job board",
    "weekly jobs",
)

RISK_FLAGS = {
    "commission_only": ("commission only", "10-15% rate", "10-15% commission"),
    "serious_side_income": ("serious side income", "weekly bonus", "no experience required"),
    "dm_only": ("dm me for details", "dm me", "telegram only", "whatsapp only"),
    "unsafe_payment": ("crypto payment", "crypto only", "gift card", "wire transfer", "payment after"),
    "no_contract": ("no contract", "free test", "trial task unpaid", "exposure"),
    "identity_abuse": ("send your id", "bank account", "investment", "forex", "casino"),
    "bad_work": ("adult", "dating", "essay writing", "homework", "account creation", "fake reviews", "review posting"),
}

LOW_VALUE_TASK_PATTERNS = (
    "lovable app testing",
    "manual vector tracing",
    "logos to svg",
    "logo to svg",
    "quick testing",
    "simple testing",
)

COMMERCIAL_SOURCE_TYPES = {"direct_rfp", "procurement", "github_issue", "hiring_signal"}
CONTACTABLE_INTENTS = {"DIRECT_HIRING", "PROCUREMENT", "RFP", "SERVICE_REQUEST"}
REVIEWABLE_INTENTS = CONTACTABLE_INTENTS | {"BUYER_PAIN"}
NOISE_INTENTS = {"FICTION", "CONSPIRACY", "NEWS", "DISCUSSION"}


@dataclass(frozen=True)
class BudgetQuality:
    amount_min: int | None = None
    amount_max: int | None = None
    currency: str = "USD"
    unit: str = "unknown"
    confidence: int = 0
    raw_evidence: str = ""
    is_estimated: bool = False
    is_valid_commercial_budget: bool = False
    rejection_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BuyerIdentity:
    name: str = ""
    organization: str = ""
    domain: str = ""
    contact_method: str = ""
    confidence: int = 0
    type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityAssessment:
    commercial_intent_score: int
    commercial_intent_label: str
    risk_score: int
    risk_level: str
    risk_reasons: list[str] = field(default_factory=list)
    source_trust_score: int = 0
    author_trust_score: int = 0
    domain_trust_score: int = 0
    trust_reasons: list[str] = field(default_factory=list)
    buyer_confidence: int = 0
    budget_confidence: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    quality_stage: str = "REVIEW_REQUIRED"
    quality_score: int = 0
    final_score_cap: int = 100
    grade: str = "C"
    duplicate_cluster_id: str = ""
    duplicate_count: int = 0
    is_contactable: bool = False
    budget: BudgetQuality = field(default_factory=BudgetQuality)
    buyer_identity: BuyerIdentity = field(default_factory=BuyerIdentity)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_commercial_budget(raw_text: str) -> BudgetQuality:
    text = _clean_text(raw_text)
    lowered = text.lower()
    if not text:
        return BudgetQuality(rejection_reason="empty_text")

    percent = re.search(r"\b\d{1,3}\s*[-to]+\s*\d{1,3}\s*%|\b\d{1,3}\s*%", lowered)
    if percent and not re.search(r"(?i)(?:usd|\$)\s*\d", text):
        return BudgetQuality(raw_evidence=percent.group(0), rejection_reason="percentage_or_commission")

    for pattern in (
        r"\b\d+\+?\s+(?:developer\s+)?jobs?\b",
        r"\b\d+\s*[-to]+\s*\d+\s+months?\b",
        r"\b\d+\s*[-to]+\s*\d+\s*%\b",
        r"\b20\s+minutes?\b",
        r"\b3\s+hour\s+events?\b",
        r"\b20\d{2}\b",
        r"\b\d+\s+plans?\b",
        r"\b\d+\s+days?\b",
    ):
        match = re.search(pattern, lowered)
        if match and not re.search(r"(?i)(?:usd|\$)\s*\d", text):
            return BudgetQuality(raw_evidence=match.group(0), rejection_reason="numeric_context_not_budget")

    currency = r"(?:usd|mxn|eur|euro|gbp|cad|aud|dollars?|pesos?|\$)"
    range_patterns = (
        rf"(?i){currency}\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|to|hasta|a)\s*(?:{currency})?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?",
        rf"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|to|hasta|a)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*{currency}\b",
        rf"(?i)(?:entre|between)\s+(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:y|and)\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*{currency}",
    )
    for pattern in range_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        evidence = match.group(0).strip()
        if _money_context_rejected(lowered, match.start(), match.end()):
            return BudgetQuality(raw_evidence=evidence, rejection_reason="numeric_context_not_budget")
        amount_min = _money_to_int(match.group(1), match.group(2))
        amount_max = _money_to_int(match.group(3), match.group(4))
        unit = _budget_unit(lowered, match.start(), match.end())
        confidence = _budget_confidence(lowered, match.start(), match.end(), unit=unit, has_currency=True)
        return BudgetQuality(
            amount_min=min(amount_min, amount_max),
            amount_max=max(amount_min, amount_max),
            currency=_currency_from_evidence(evidence),
            unit=unit,
            confidence=confidence,
            raw_evidence=evidence,
            is_valid_commercial_budget=confidence >= 60,
        )

    single_patterns = (
        rf"(?i){currency}\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?",
        rf"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*{currency}\b",
    )
    matches = []
    for pattern in single_patterns:
        matches.extend(re.finditer(pattern, text))
    if not matches:
        return BudgetQuality(rejection_reason="no_currency_budget_signal")

    for match in matches:
        evidence = match.group(0).strip()
        if _money_context_rejected(lowered, match.start(), match.end()):
            continue
        amount = _money_to_int(match.group(1), match.group(2))
        unit = _budget_unit(lowered, match.start(), match.end())
        confidence = _budget_confidence(lowered, match.start(), match.end(), unit=unit, has_currency=True)
        return BudgetQuality(
            amount_min=amount,
            amount_max=amount,
            currency=_currency_from_evidence(evidence),
            unit=unit,
            confidence=confidence,
            raw_evidence=evidence,
            is_valid_commercial_budget=confidence >= 60,
        )

    return BudgetQuality(rejection_reason="numeric_context_not_budget")


def build_duplicate_context(opportunities: list[Any]) -> dict[int, dict[str, Any]]:
    groups: dict[str, list[Any]] = {}
    for opportunity in opportunities:
        key = duplicate_key(opportunity)
        if not key:
            continue
        groups.setdefault(key, []).append(opportunity)

    context: dict[int, dict[str, Any]] = {}
    for key, rows in groups.items():
        if len(rows) <= 1:
            continue
        cluster_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        for index, opportunity in enumerate(rows):
            context[id(opportunity)] = {
                "duplicate_cluster_id": cluster_id,
                "duplicate_count": len(rows),
                "is_duplicate": index > 0,
            }
    return context


def duplicate_key(opportunity: Any) -> str:
    canonical_url = _canonical_url(_value(opportunity, "url"))
    external = _clean_text(_value(opportunity, "external_id"))
    source = _clean_text(_value(opportunity, "source_key")).lower()
    title = _normalize_for_hash(_value(opportunity, "title"))
    body = _normalize_for_hash(_value(opportunity, "raw_text"))
    buyer = _normalize_for_hash(_value(opportunity, "buyer_name") or _value(opportunity, "company"))
    if source in {"reddit", "email_alerts"} and title and body:
        return "body:" + hashlib.sha1(f"{buyer}|{title}|{body[:500]}".encode("utf-8")).hexdigest()
    if source and external:
        return f"external:{source}:{external}"
    if canonical_url:
        return f"url:{canonical_url}"
    if title and body:
        return "body:" + hashlib.sha1(f"{buyer}|{title}|{body[:500]}".encode("utf-8")).hexdigest()
    return ""


def assess_opportunity_quality(
    opportunity: Any,
    *,
    policy_config: Mapping[str, Any] | None = None,
    duplicate_hint: Mapping[str, Any] | None = None,
) -> QualityAssessment:
    policy_config = policy_config or {}
    duplicate_hint = duplicate_hint or {}
    text = _combined_text(opportunity)
    lowered = text.lower()
    source_key = _clean_text(_value(opportunity, "source_key")).lower()
    source_type = _clean_text(_value(opportunity, "source_type")).lower()
    subreddit = _reddit_subreddit(opportunity)
    reddit_policy = _reddit_policy(policy_config)

    budget = parse_commercial_budget(text)
    intent_label, intent_score, intent_reasons = _commercial_intent(lowered, source_type=source_type)
    risk_score, risk_reasons = _risk_score(lowered, opportunity)
    buyer_identity = _buyer_identity(opportunity, source_type=source_type, subreddit=subreddit)
    source_trust, source_reasons = _source_trust(
        opportunity,
        source_key=source_key,
        source_type=source_type,
        subreddit=subreddit,
        reddit_policy=reddit_policy,
    )
    domain_trust = _domain_trust(opportunity, buyer_identity=buyer_identity)
    author_trust = _author_trust(opportunity, intent_label=intent_label, subreddit=subreddit)
    rejection_reasons: list[str] = []
    cap = 100

    if duplicate_hint.get("is_duplicate"):
        rejection_reasons.append("duplicate_exact")
        cap = min(cap, 10)
    if source_key == "reddit" and subreddit in reddit_policy["blocklist"]:
        rejection_reasons.append("blocked_subreddit")
        cap = min(cap, 10)
        if subreddit == "nosleep":
            intent_label = "FICTION"
            intent_score = 0
        elif subreddit == "conspiracy":
            intent_label = "CONSPIRACY"
            intent_score = 0
    if intent_label in NOISE_INTENTS:
        cap = min(cap, 20)
        rejection_reasons.append(f"intent_{intent_label.lower()}")
    if intent_label == "SEO_CONTENT":
        cap = min(cap, 35)
        rejection_reasons.append("seo_content")
    if intent_label == "MARKET_RESEARCH":
        cap = min(cap, 45)
        rejection_reasons.append("market_research")
    if buyer_identity.confidence < 30:
        cap = min(cap, 45)
        rejection_reasons.append("buyer_identity_weak")
    if not budget.is_valid_commercial_budget:
        cap = min(cap, 61)
        if budget.rejection_reason:
            rejection_reasons.append(f"budget_{budget.rejection_reason}")
    if risk_score >= 85:
        cap = min(cap, 10)
        rejection_reasons.append("critical_risk")
    elif risk_score >= 65:
        cap = min(cap, 44)
        rejection_reasons.append("high_risk")
    if _is_low_value_task(lowered, budget=budget):
        cap = min(cap, 35)
        rejection_reasons.append("low_value_budget")

    risk_level = _risk_level(risk_score)
    evidence_score = _evidence_score(opportunity, budget=budget, buyer_identity=buyer_identity, intent_score=intent_score)
    raw_quality_score = round(
        (0.26 * intent_score)
        + (0.18 * source_trust)
        + (0.18 * buyer_identity.confidence)
        + (0.14 * budget.confidence)
        + (0.14 * evidence_score)
        + (0.10 * max(domain_trust, author_trust))
        - min(45, risk_score * 0.55)
    )
    quality_score = max(0, min(cap, raw_quality_score))
    stage = _quality_stage(
        intent_label=intent_label,
        risk_level=risk_level,
        buyer_confidence=buyer_identity.confidence,
        budget=budget,
        quality_score=quality_score,
        source_key=source_key,
        subreddit=subreddit,
        reddit_policy=reddit_policy,
        duplicate=bool(duplicate_hint.get("is_duplicate")),
        rejection_reasons=rejection_reasons,
    )
    grade = _quality_grade(
        quality_score=quality_score,
        stage=stage,
        intent_label=intent_label,
        buyer_confidence=buyer_identity.confidence,
        evidence_score=evidence_score,
        risk_level=risk_level,
        budget=budget,
    )
    is_contactable = stage == "READY_TO_CONTACT"

    return QualityAssessment(
        commercial_intent_score=intent_score,
        commercial_intent_label=intent_label,
        risk_score=risk_score,
        risk_level=risk_level,
        risk_reasons=_unique([*risk_reasons, *intent_reasons])[:8],
        source_trust_score=source_trust,
        author_trust_score=author_trust,
        domain_trust_score=domain_trust,
        trust_reasons=_unique([*source_reasons])[:8],
        buyer_confidence=buyer_identity.confidence,
        budget_confidence=budget.confidence,
        rejection_reasons=_unique(rejection_reasons),
        quality_stage=stage,
        quality_score=quality_score,
        final_score_cap=cap,
        grade=grade,
        duplicate_cluster_id=str(duplicate_hint.get("duplicate_cluster_id") or ""),
        duplicate_count=int(duplicate_hint.get("duplicate_count") or 0),
        is_contactable=is_contactable,
        budget=budget,
        buyer_identity=buyer_identity,
    )


def apply_quality_to_score(score: dict[str, Any], quality: QualityAssessment) -> dict[str, Any]:
    updated = dict(score)
    original_total = int(updated.get("score_total") or 0)
    capped_total = max(0, min(original_total, quality.final_score_cap))
    tier = _legacy_tier_for_quality(quality, capped_total)
    reasons = list(updated.get("score_reasons") or [])
    reasons.extend(_quality_reasons(quality))

    updated["score_total"] = capped_total
    updated["score_tier"] = tier
    updated["score_reasons"] = _unique(reasons)[:8]
    updated["next_best_action"] = _quality_next_action(quality, tier)
    if not quality.budget.is_valid_commercial_budget:
        updated["estimated_value"] = None
    return updated


def should_assign_buyer_account(quality: QualityAssessment) -> bool:
    return quality.quality_stage in {"READY_TO_CONTACT", "REVIEW_REQUIRED", "WATCHLIST"} and quality.buyer_confidence >= 45


def is_quality_noise_mapping(opportunity: Mapping[str, Any]) -> bool:
    quality = _quality_from_analysis(opportunity)
    if quality:
        return str(quality.get("quality_stage") or "") in {"REJECTED_NOISE", "DUPLICATE", "LOW_VALUE"}
    source_key = str(opportunity.get("source_key") or "").lower()
    if source_key == "reddit":
        subreddit = _reddit_subreddit(opportunity)
        if subreddit in REDDIT_BLOCKLIST:
            return True
        label, _score, _reasons = _commercial_intent(_combined_text(opportunity).lower(), source_type=str(opportunity.get("source_type") or ""))
        return label in NOISE_INTENTS or label in {"SEO_CONTENT", "JOB_AGGREGATOR"}
    return False


def _commercial_intent(text: str, *, source_type: str) -> tuple[str, int, list[str]]:
    reasons: list[str] = []
    for label in ("FICTION", "CONSPIRACY", "NEWS", "SEO_CONTENT", "DISCUSSION", "SELF_PROMO", "MARKET_RESEARCH"):
        patterns = NEGATIVE_INTENT_PATTERNS[label]
        if any(pattern in text for pattern in patterns):
            reasons.append(f"Clasificado como {label}.")
            return label, 10 if label in NOISE_INTENTS else 22, reasons
    if any(pattern in text for pattern in JOB_AGGREGATOR_PATTERNS) or re.search(r"\b\d+\+?\s+(?:developer\s+)?jobs?\b", text):
        reasons.append("Parece agregador de vacantes, no comprador directo.")
        return "JOB_AGGREGATOR", 35, reasons
    if source_type == "procurement" or any(term in text for term in PROCUREMENT_TERMS):
        reasons.append("Hay proceso formal de compra o procurement.")
        return "PROCUREMENT", 88, reasons
    if "rfp" in text or "request for proposal" in text:
        return "RFP", 90, ["Menciona RFP."]
    if source_type == "direct_rfp" and any(term in text for term in ("needs ", "need ", "looking for", "seeking ", "busca ", "necesita ")) and any(term in text for term in PAY_TERMS):
        reasons.append("Solicitud directa con necesidad y senal de pago.")
        return "SERVICE_REQUEST", 84, reasons
    if source_type == "github_issue" and any(term in text for term in ("bounty", "paid", "usd", "$", "fixed")):
        reasons.append("Issue tecnico con bounty o senal de pago.")
        return "SERVICE_REQUEST", 72, reasons
    if any(term in text for term in DIRECT_HIRING_TERMS):
        score = 76
        if any(term in text for term in PAY_TERMS):
            score += 10
        reasons.append("Hay intencion directa de contratar.")
        return "DIRECT_HIRING", min(96, score), reasons
    if source_type == "direct_rfp" and any(term in text for term in PAY_TERMS):
        reasons.append("Solicitud directa con senales de pago.")
        return "SERVICE_REQUEST", 74, reasons
    if source_type == "hiring_signal":
        reasons.append("Hiring signal tecnico con comprador identificable.")
        return "SERVICE_REQUEST", 68, reasons
    if any(term in text for term in ("need help", "help us", "pain", "problem", "automate", "integration", "build")):
        reasons.append("Hay dolor operativo, pero falta validar compra.")
        return "BUYER_PAIN", 58, reasons
    if any(term in text for term in PAY_TERMS):
        reasons.append("Hay senales comerciales sueltas.")
        return "SERVICE_REQUEST", 62, reasons
    return "UNKNOWN", 25, ["No hay intencion comercial clara."]


def _risk_score(text: str, opportunity: Any) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for reason, patterns in RISK_FLAGS.items():
        if any(pattern in text for pattern in patterns):
            if reason in {"commission_only", "serious_side_income", "identity_abuse"}:
                score += 42
            elif reason == "bad_work":
                score += 52
            elif reason in {"dm_only", "no_contract"}:
                score += 34
            else:
                score += 18
            reasons.append(reason)
    if not (_value(opportunity, "buyer_name") or _value(opportunity, "company")):
        score += 14
        reasons.append("no_buyer_identity")
    if _clean_text(_value(opportunity, "risk_level")).lower() == "high":
        score += 30
        reasons.append("source_marked_high_risk")
    elif _clean_text(_value(opportunity, "risk_level")).lower() == "medium":
        score += 10
        reasons.append("source_marked_medium_risk")
    return min(100, score), _unique(reasons)


def _buyer_identity(opportunity: Any, *, source_type: str, subreddit: str) -> BuyerIdentity:
    company = _clean_text(_value(opportunity, "company"))
    buyer_name = _clean_text(_value(opportunity, "buyer_name") or company)
    domain = _normalize_domain(_value(opportunity, "buyer_domain"))
    contact = _contact_method(opportunity)
    organization = buyer_name
    confidence = 12
    buyer_type = "unknown"

    if source_type == "procurement":
        confidence = 82
        buyer_type = "government"
    elif source_type == "hiring_signal" and buyer_name:
        confidence = 72
        buyer_type = "company"
    elif source_type == "direct_rfp" and buyer_name:
        confidence = 58
        buyer_type = "company"
    elif source_type == "github_issue" and buyer_name:
        confidence = 58
        buyer_type = "individual"

    if domain and domain not in {"reddit.com", "old.reddit.com", "www.reddit.com"}:
        confidence += 18
        buyer_type = "company"
    if contact:
        confidence += 18
    if subreddit and buyer_name.lower() == subreddit:
        confidence = min(confidence, 28)
        buyer_type = "unknown"
        organization = ""
    if not buyer_name and not domain and not contact:
        confidence = 8
    return BuyerIdentity(
        name=buyer_name if buyer_name and buyer_name.lower() != subreddit else "",
        organization=organization,
        domain=domain,
        contact_method=contact,
        confidence=max(0, min(100, confidence)),
        type=buyer_type,
    )


def _source_trust(opportunity: Any, *, source_key: str, source_type: str, subreddit: str, reddit_policy: dict) -> tuple[int, list[str]]:
    if source_type == "procurement":
        return 90, ["Fuente formal de procurement."]
    if source_key in {"workana_projects", "email_alerts"}:
        return 72, ["Fuente transaccional o marketplace."]
    if source_type == "github_issue":
        return 62, ["Issue tecnico con URL accionable."]
    if source_type == "hiring_signal":
        return 68, ["ATS o hiring signal reconocido."]
    if source_key == "reddit":
        if subreddit in reddit_policy["blocklist"]:
            return 0, [f"Subreddit bloqueado: {subreddit}."]
        if subreddit in reddit_policy["allowlist"]:
            return 55, [f"Subreddit permitido con revision: {subreddit}."]
        if subreddit in reddit_policy["greylist"]:
            return 35, [f"Subreddit greylist: {subreddit}."]
        return 24, ["Reddit sin subreddit confiable."]
    return 50, ["Fuente sin historial suficiente."]


def _domain_trust(opportunity: Any, *, buyer_identity: BuyerIdentity) -> int:
    domain = buyer_identity.domain or _normalize_domain(_value(opportunity, "url"))
    if not domain:
        return 10
    if domain in {"reddit.com", "old.reddit.com", "upwork.com"}:
        return 28
    if any(domain.endswith(suffix) for suffix in (".gov", ".edu", ".org")):
        return 82
    if "." in domain:
        return 68
    return 25


def _author_trust(opportunity: Any, *, intent_label: str, subreddit: str) -> int:
    author = _clean_text(_value(opportunity, "author"))
    if intent_label == "SEO_CONTENT":
        return 18
    if subreddit in REDDIT_BLOCKLIST:
        return 0
    if author:
        return 42
    return 30


def _quality_stage(
    *,
    intent_label: str,
    risk_level: str,
    buyer_confidence: int,
    budget: BudgetQuality,
    quality_score: int,
    source_key: str,
    subreddit: str,
    reddit_policy: dict,
    duplicate: bool,
    rejection_reasons: list[str],
) -> str:
    if duplicate:
        return "DUPLICATE"
    if "blocked_subreddit" in rejection_reasons or intent_label in {"FICTION", "CONSPIRACY", "NEWS"}:
        return "REJECTED_NOISE"
    if risk_level == "CRITICAL":
        return "SUSPECT"
    if risk_level == "HIGH":
        return "SUSPECT"
    if intent_label in {"SEO_CONTENT", "DISCUSSION", "SELF_PROMO", "MARKET_RESEARCH"}:
        return "LOW_VALUE"
    if intent_label == "JOB_AGGREGATOR":
        return "WATCHLIST"
    if "low_value_budget" in rejection_reasons:
        return "LOW_VALUE"
    if source_key == "reddit" and subreddit in reddit_policy["greylist"] and buyer_confidence < 50:
        return "REVIEW_REQUIRED"
    if intent_label == "PROCUREMENT" and buyer_confidence >= 85 and quality_score >= 60:
        return "READY_TO_CONTACT"
    if intent_label in CONTACTABLE_INTENTS and buyer_confidence >= 70 and quality_score >= 75 and (budget.is_valid_commercial_budget or buyer_confidence >= 85):
        return "READY_TO_CONTACT"
    if intent_label in REVIEWABLE_INTENTS and buyer_confidence >= 50 and quality_score >= 55:
        return "REVIEW_REQUIRED"
    if intent_label in REVIEWABLE_INTENTS or quality_score >= 45:
        return "WATCHLIST"
    return "LOW_VALUE"


def _quality_grade(
    *,
    quality_score: int,
    stage: str,
    intent_label: str,
    buyer_confidence: int,
    evidence_score: int,
    risk_level: str,
    budget: BudgetQuality,
) -> str:
    if stage in {"REJECTED_NOISE", "DUPLICATE"} or risk_level == "CRITICAL":
        return "REJECTED"
    if risk_level == "HIGH":
        return "C"
    if (
        quality_score >= 90
        and intent_label in CONTACTABLE_INTENTS
        and buyer_confidence >= 70
        and evidence_score >= 70
        and risk_level in {"LOW", "MEDIUM"}
        and budget.is_valid_commercial_budget
    ):
        return "A1"
    if (
        quality_score >= 80
        and intent_label in REVIEWABLE_INTENTS
        and buyer_confidence >= 50
        and evidence_score >= 55
        and risk_level in {"LOW", "MEDIUM"}
    ):
        return "A2"
    if quality_score >= 65:
        return "B"
    if quality_score >= 45:
        return "C"
    return "D"


def _legacy_tier_for_quality(quality: QualityAssessment, total: int) -> str:
    if quality.grade == "REJECTED" or quality.quality_stage in {"REJECTED_NOISE", "DUPLICATE"}:
        return "D"
    if quality.risk_level == "HIGH":
        return "C"
    if total >= 75:
        return "A1"
    if total >= 62:
        return "A2"
    if total >= 45:
        return "B"
    if total >= 28:
        return "C"
    return "D"


def _quality_next_action(quality: QualityAssessment, tier: str) -> str:
    if quality.quality_stage == "READY_TO_CONTACT":
        return "Contactar solo con evidencia verificada y CTA breve."
    if quality.quality_stage == "REVIEW_REQUIRED":
        return "Revisar evidencia, comprador y canal antes de redactar."
    if quality.quality_stage == "WATCHLIST":
        return "Vigilar; no contactar hasta confirmar comprador o canal oficial."
    if quality.quality_stage == "SUSPECT":
        return "No contactar. Validar identidad, pago y fuente antes de avanzar."
    if quality.quality_stage == "DUPLICATE":
        return "Duplicado oculto. Trabajar desde la oportunidad principal."
    if quality.quality_stage == "REJECTED_NOISE":
        return "Ruido rechazado. No contactar."
    if tier in {"C", "D"}:
        return "No contactar todavia; falta evidencia comercial."
    return "Mantener en revision."


def _quality_reasons(quality: QualityAssessment) -> list[str]:
    reasons = [
        f"Calidad: {quality.quality_stage}.",
        f"Intencion: {quality.commercial_intent_label} ({quality.commercial_intent_score}).",
        f"Comprador: {quality.buyer_confidence}/100.",
    ]
    if quality.rejection_reasons:
        reasons.append("Bloqueos: " + ", ".join(quality.rejection_reasons[:3]) + ".")
    if quality.risk_reasons:
        reasons.append("Riesgos: " + ", ".join(quality.risk_reasons[:3]) + ".")
    return reasons


def _evidence_score(opportunity: Any, *, budget: BudgetQuality, buyer_identity: BuyerIdentity, intent_score: int) -> int:
    score = 20
    if _list_value(opportunity, "evidence_snippets"):
        score += min(25, len(_list_value(opportunity, "evidence_snippets")) * 8)
    if _list_value(opportunity, "pain_signals"):
        score += min(18, len(_list_value(opportunity, "pain_signals")) * 6)
    if budget.is_valid_commercial_budget:
        score += 18
    if buyer_identity.confidence >= 50:
        score += 18
    if intent_score >= 70:
        score += 16
    return min(100, score)


def _reddit_policy(policy_config: Mapping[str, Any]) -> dict[str, set[str]]:
    reddit_config = policy_config.get("reddit") if isinstance(policy_config.get("reddit"), Mapping) else policy_config
    return {
        "allowlist": _string_set(reddit_config.get("allowlist")) or REDDIT_ALLOWLIST,
        "greylist": _string_set(reddit_config.get("greylist")) or REDDIT_GREYLIST,
        "blocklist": _string_set(reddit_config.get("blocklist")) or REDDIT_BLOCKLIST,
    }


def _risk_level(score: int) -> str:
    if score >= 85:
        return "CRITICAL"
    if score >= 65:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    return "LOW"


def _budget_unit(text: str, start: int, end: int) -> str:
    window = _window(text, start, end, size=46)
    if any(term in window for term in ("/hour", "/hr", "/h", "per hour", "hourly", " an hour", " p/h", "por hora", "hora")):
        return "hour"
    if any(term in window for term in ("per month", "/month", "monthly", "mensual", "mes")):
        return "month"
    if any(term in window for term in ("per year", "/year", "yearly", "salary", "anual")):
        return "year"
    if any(term in window for term in ("event", "minutes", "minute", "hours", "evento", "event-based")):
        return "event"
    if any(term in window for term in ("fixed", "fixed price", "project", "proyecto", "fijo", "budget", "presupuesto", "milestone", "contract", "award", "amount", "solicitation")):
        return "project"
    return "unknown"


def _budget_confidence(text: str, start: int, end: int, *, unit: str, has_currency: bool) -> int:
    window = _window(text, start, end, size=70)
    score = 45 if has_currency else 20
    if unit != "unknown":
        score += 25
    if any(term in window for term in ("budget", "presupuesto", "pago", "pagamos", "pay", "paid", "rate", "fixed", "project", "proyecto", "hourly", "contract", "compensation", "award", "amount", "solicitation", "fee", "mxn", "usd")):
        score += 22
    if any(term in window for term in ("jobs", "opened", "months", "plans", "days", "percent", "commission")) and unit == "unknown":
        score -= 28
    return max(0, min(100, score))


def _money_context_rejected(text: str, start: int, end: int) -> bool:
    window = _window(text, start, end, size=42)
    if any(term in window for term in ("jobs", "jobs that opened", "developer jobs", "plans", "total")):
        return True
    if "visible" in window and not any(term in window for term in ("budget", "presupuesto", "award", "amount", "pay", "paid", "rate")):
        return True
    if "commission" in window or re.search(r"\d+\s*[-to]+\s*\d+\s*%", window):
        return True
    return False


def _money_to_int(value: str, suffix: str | None) -> int:
    number = float(str(value).replace(",", "").strip())
    if suffix:
        number *= 1000
    return int(number)


def _currency_from_evidence(evidence: str) -> str:
    lowered = evidence.lower()
    if "mxn" in lowered or "peso" in lowered:
        return "MXN"
    if "eur" in lowered or "euro" in lowered:
        return "EUR"
    if "gbp" in lowered:
        return "GBP"
    if "cad" in lowered:
        return "CAD"
    if "aud" in lowered:
        return "AUD"
    return "USD"


def _is_low_value_task(text: str, *, budget: BudgetQuality) -> bool:
    amount = budget.amount_max or budget.amount_min or 0
    if budget.is_valid_commercial_budget and budget.unit != "hour" and amount and amount < 75:
        return True
    if budget.is_valid_commercial_budget and budget.unit == "event" and amount and amount < 100:
        return True
    return any(pattern in text for pattern in LOW_VALUE_TASK_PATTERNS)


def _reddit_subreddit(opportunity: Any) -> str:
    source_key = _clean_text(_value(opportunity, "source_key")).lower()
    url = _clean_text(_value(opportunity, "url")).lower()
    if source_key != "reddit" and "reddit.com/" not in url:
        return ""
    company = _clean_text(_value(opportunity, "company") or _value(opportunity, "buyer_name")).lower().replace("r/", "")
    if company:
        return re.sub(r"[^a-z0-9_]+", "", company)
    parsed = urlparse(_clean_text(_value(opportunity, "url")))
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts):
        if part.lower() == "r" and index + 1 < len(parts):
            return re.sub(r"[^a-z0-9_]+", "", parts[index + 1].lower())
    return ""


def _contact_method(opportunity: Any) -> str:
    for signal in _list_value(opportunity, "contact_signals"):
        lowered = signal.lower()
        if "@" in lowered or lowered.startswith("email:"):
            return "email"
        if lowered.startswith("phone:"):
            return "phone"
        if lowered.startswith("portal:"):
            return "portal"
        if "github_issue" in lowered:
            return "github_issue"
    if _value(opportunity, "contact_url") or _value(opportunity, "apply_url"):
        return "url"
    return ""


def _quality_from_analysis(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    raw = opportunity.get("analysis")
    if isinstance(raw, Mapping):
        quality = raw.get("quality")
        return dict(quality) if isinstance(quality, Mapping) else {}
    raw_json = opportunity.get("analysis_json")
    if not raw_json:
        return {}
    try:
        import json

        parsed = json.loads(str(raw_json))
    except Exception:
        return {}
    quality = parsed.get("quality")
    return dict(quality) if isinstance(quality, Mapping) else {}


def _combined_text(opportunity: Any) -> str:
    parts = [
        _value(opportunity, "title"),
        _value(opportunity, "company"),
        _value(opportunity, "buyer_name"),
        _value(opportunity, "buyer_domain"),
        _value(opportunity, "raw_text"),
        " ".join(_list_value(opportunity, "stack")),
        " ".join(_list_value(opportunity, "required_skills")),
        " ".join(_list_value(opportunity, "pain_signals")),
        " ".join(_list_value(opportunity, "contact_signals")),
        " ".join(_list_value(opportunity, "evidence_snippets")),
    ]
    return "\n".join(_clean_text(part) for part in parts if _clean_text(part))


def _value(opportunity: Any, key: str) -> Any:
    if isinstance(opportunity, Mapping):
        return opportunity.get(key)
    return getattr(opportunity, key, None)


def _list_value(opportunity: Any, key: str) -> list[str]:
    value = _value(opportunity, key)
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                import json

                parsed = json.loads(text)
            except Exception:
                return [text]
            return _list_value({"value": parsed}, "value")
        return [item.strip() for item in text.split(",") if item.strip()]
    return []


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).strip()


def _normalize_for_hash(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean_text(value).lower()).strip()


def _canonical_url(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path.rstrip("/")
    return f"{host}{path}".lower()


def _normalize_domain(value: Any) -> str:
    text = _clean_text(value).lower()
    if not text:
        return ""
    if "@" in text and "://" not in text:
        text = text.rsplit("@", 1)[-1]
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    host = (parsed.netloc or parsed.path).lower().split("/", 1)[0].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def _window(text: str, start: int, end: int, *, size: int) -> str:
    return text[max(0, start - size) : min(len(text), end + size)]


def _string_set(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        raw_values = value
    return {str(item).strip().lower().replace("r/", "") for item in raw_values if str(item).strip()}


def _unique(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output
