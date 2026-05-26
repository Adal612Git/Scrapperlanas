from __future__ import annotations

from pathlib import Path

from scrapperlanas import create_app
from scrapperlanas.db import get_db
from scrapperlanas.services.ingestion import NormalizedOpportunity, extract_budget
from scrapperlanas.services.pipeline import import_opportunities
from scrapperlanas.services.quality import (
    assess_opportunity_quality,
    build_duplicate_context,
    parse_commercial_budget,
)


def _reddit(title: str, subreddit: str, body: str = "", url_slug: str = "post", **overrides):
    data = {
        "external_id": f"{subreddit}-{url_slug}",
        "source_key": "reddit",
        "source_label": "Reddit Public JSON",
        "source_type": "community",
        "title": title,
        "company": subreddit,
        "buyer_name": subreddit,
        "buyer_domain": "reddit.com",
        "url": f"https://reddit.com/r/{subreddit}/comments/{url_slug}",
        "raw_text": f"{title}\n{body}",
        "risk_level": "medium",
    }
    data.update(overrides)
    return NormalizedOpportunity(**data)


def test_quality_rejects_blocked_nosleep_fiction():
    quality = assess_opportunity_quality(
        _reddit(
            "The Warehouse I Work At Has a Basement That Doesn't Exist...",
            "nosleep",
            "Story fiction with no buyer.",
        )
    )

    assert quality.quality_stage == "REJECTED_NOISE"
    assert quality.commercial_intent_label == "FICTION"
    assert "blocked_subreddit" in quality.rejection_reasons
    assert quality.final_score_cap <= 10


def test_quality_rejects_conspiracy_posts():
    quality = assess_opportunity_quality(
        _reddit(
            "Alisa Valdes-Rodriguez says Epstein used directed-energy weapon",
            "conspiracy",
            "conspiracy discussion",
        )
    )

    assert quality.quality_stage == "REJECTED_NOISE"
    assert quality.commercial_intent_label == "CONSPIRACY"
    assert "blocked_subreddit" in quality.rejection_reasons


def test_quality_classifies_seo_content_as_not_contactable():
    quality = assess_opportunity_quality(
        _reddit(
            "How to Choose an Enterprise AI Consultant for Magento",
            "SaaS",
            "Guide and case study for Hyva agencies.",
            buyer_name="u/glossfirre",
        )
    )

    assert quality.commercial_intent_label == "SEO_CONTENT"
    assert quality.quality_stage == "LOW_VALUE"
    assert quality.is_contactable is False


def test_quality_rejects_rate_my_proposal_discussion():
    quality = assess_opportunity_quality(
        _reddit(
            "Could you please rate my proposal?",
            "Upwork",
            "I want feedback, what do you think?",
            buyer_name="Upwork",
        )
    )

    assert quality.commercial_intent_label == "DISCUSSION"
    assert quality.is_contactable is False
    assert quality.grade in {"C", "D", "REJECTED"}


def test_budget_parser_rejects_counts_durations_and_commission():
    assert parse_commercial_budget("[US] 30+ Developer jobs that opened this week").is_valid_commercial_budget is False
    assert parse_commercial_budget("Saves 3-6 months of dev work").is_valid_commercial_budget is False
    assert parse_commercial_budget("Networking online, serious side income, 10-15% rate").is_valid_commercial_budget is False
    assert extract_budget("[US] 30+ Developer jobs")[:2] == (None, None)
    assert extract_budget("3-6 months of dev work")[:2] == (None, None)


def test_budget_parser_accepts_hourly_and_mxn_project():
    hourly = parse_commercial_budget("[Hiring] automation specialist $50/hour")
    mxn = parse_commercial_budget("Small ISP needs WhatsApp bot. Budget MXN 25,000 project.")

    assert hourly.is_valid_commercial_budget is True
    assert hourly.amount_min == 50
    assert hourly.unit == "hour"
    assert mxn.is_valid_commercial_budget is True
    assert mxn.amount_min == 25_000
    assert mxn.currency == "MXN"
    assert mxn.unit == "project"


def test_budget_parser_accepts_real_low_value_event_budget():
    budget = parse_commercial_budget("[Hiring] Event-Based Work. $200 for 3 hour events")

    assert budget.is_valid_commercial_budget is True
    assert budget.amount_min == 200
    assert budget.unit == "event"


def test_job_aggregator_goes_watchlist_without_budget():
    quality = assess_opportunity_quality(
        _reddit(
            "[US] 30+ Developer jobs that opened this week",
            "DeveloperJobs",
            "Python, React, data jobs list.",
        )
    )

    assert quality.commercial_intent_label == "JOB_AGGREGATOR"
    assert quality.quality_stage == "WATCHLIST"
    assert quality.budget.is_valid_commercial_budget is False
    assert quality.is_contactable is False


def test_commission_only_hiring_is_suspect():
    quality = assess_opportunity_quality(
        _reddit(
            "[hiring] networking online, serious side income, 10-15% rate",
            "forhire",
            "No experience required. DM me for details.",
        )
    )

    assert quality.risk_level in {"HIGH", "CRITICAL"}
    assert quality.quality_stage == "SUSPECT"
    assert quality.is_contactable is False


def test_reddit_hiring_without_buyer_does_not_auto_a1_a2():
    quality = assess_opportunity_quality(
        _reddit(
            "[Hiring] automation specialist $50/hour",
            "forhire",
            "Need a developer for n8n automation. DM me.",
        )
    )

    assert quality.commercial_intent_label == "DIRECT_HIRING"
    assert quality.buyer_confidence < 50
    assert quality.grade not in {"A1", "A2"}
    assert quality.quality_stage in {"WATCHLIST", "REVIEW_REQUIRED", "SUSPECT"}


def test_duplicate_context_marks_later_duplicates():
    first = _reddit("Could you please rate my proposal?", "Upwork", "same body", url_slug="a")
    second = _reddit("Could you please rate my proposal?", "Upwork", "same body", url_slug="b")
    context = build_duplicate_context([first, second])
    first_quality = assess_opportunity_quality(first, duplicate_hint=context.get(id(first), {}))
    second_quality = assess_opportunity_quality(second, duplicate_hint=context.get(id(second), {}))

    assert first_quality.duplicate_count == 2
    assert second_quality.quality_stage == "DUPLICATE"


def test_pipeline_keeps_blocked_reddit_out_of_hot_accounts(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "quality.sqlite3"),
            "SECRET_KEY": "quality-test-secret",
            "CRON_SECRET": "quality-cron-secret",
            "CSRF_ENABLED": False,
            "AUTOMATION_MODE": "manual",
            "AI_PROVIDER": "heuristic",
            "AI_EMBED_PROVIDER": "none",
        }
    )

    with app.app_context():
        db = get_db()
        stats = import_opportunities(
            db,
            source_key="reddit",
            source_label="Reddit Public JSON",
            items=[
                {
                    "external_id": "conspiracy-1",
                    "title": "Alisa Valdes-Rodriguez Epstein directed-energy weapon",
                    "company": "conspiracy",
                    "buyer_name": "conspiracy",
                    "buyer_domain": "reddit.com",
                    "url": "https://reddit.com/r/conspiracy/comments/1",
                    "raw_text": "conspiracy post with USD 151839 and API AI automation words",
                    "budget_min": 151839,
                    "currency": "USD",
                }
            ],
        )
        row = db.execute("SELECT state, commercial_status, score_tier, buyer_account_id, analysis_json FROM opportunities").fetchone()
        hot_accounts = db.execute("SELECT COUNT(*) AS total FROM buyer_accounts WHERE account_tier IN ('hot', 'warm')").fetchone()["total"]

    assert stats["created"] == 1
    assert row["state"] == "DESCARTADO"
    assert row["commercial_status"] == "ignored"
    assert row["score_tier"] == "D"
    assert row["buyer_account_id"] is None
    assert hot_accounts == 0
