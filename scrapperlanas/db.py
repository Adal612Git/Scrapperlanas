from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

import click
from flask import current_app, g


SQLITE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        keywords TEXT NOT NULL DEFAULT '',
        sectors TEXT NOT NULL DEFAULT '',
        min_budget INTEGER NOT NULL DEFAULT 0,
        preferred_sources TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_key TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        method TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        frequency_minutes INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        config_json TEXT NOT NULL DEFAULT '{}',
        last_run_at TEXT,
        notes TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS target_companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        ats_provider TEXT NOT NULL DEFAULT 'unknown',
        ats_slug TEXT NOT NULL DEFAULT '',
        careers_url TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'medium',
        notes TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_signal_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_target_companies_provider_enabled ON target_companies(ats_provider, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_target_companies_slug ON target_companies(ats_provider, ats_slug)",
    """
    CREATE TABLE IF NOT EXISTS buyer_accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        normalized_domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        website TEXT NOT NULL DEFAULT '',
        source_first_seen TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        opportunity_count INTEGER NOT NULL DEFAULT 0,
        a1_count INTEGER NOT NULL DEFAULT 0,
        a2_count INTEGER NOT NULL DEFAULT 0,
        contacted_count INTEGER NOT NULL DEFAULT 0,
        replied_count INTEGER NOT NULL DEFAULT 0,
        proposal_count INTEGER NOT NULL DEFAULT 0,
        won_count INTEGER NOT NULL DEFAULT 0,
        lost_count INTEGER NOT NULL DEFAULT 0,
        ignored_count INTEGER NOT NULL DEFAULT 0,
        total_estimated_value INTEGER NOT NULL DEFAULT 0,
        total_proposal_value INTEGER NOT NULL DEFAULT 0,
        total_won_value INTEGER NOT NULL DEFAULT 0,
        account_score INTEGER NOT NULL DEFAULT 0,
        account_tier TEXT NOT NULL DEFAULT 'cold',
        primary_pain_signals_json TEXT NOT NULL DEFAULT '[]',
        required_skills_json TEXT NOT NULL DEFAULT '[]',
        contact_signals_json TEXT NOT NULL DEFAULT '[]',
        evidence_summary_json TEXT NOT NULL DEFAULT '[]',
        last_contacted_at TEXT,
        next_followup_at TEXT,
        commercial_status TEXT NOT NULL DEFAULT 'new',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buyer_accounts_tier ON buyer_accounts(account_tier, account_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_buyer_accounts_normalized_name ON buyer_accounts(normalized_name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_accounts_normalized_domain ON buyer_accounts(normalized_domain) WHERE normalized_domain <> ''",
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_account_id INTEGER,
        external_id TEXT,
        source_key TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT '',
        source_label TEXT NOT NULL,
        title TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        buyer_name TEXT NOT NULL DEFAULT '',
        buyer_domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        url TEXT UNIQUE NOT NULL,
        apply_url TEXT NOT NULL DEFAULT '',
        budget_min INTEGER,
        budget_max INTEGER,
        estimated_value INTEGER,
        budget_text TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT 'USD',
        sector TEXT NOT NULL DEFAULT '',
        stack TEXT NOT NULL DEFAULT '[]',
        required_skills TEXT NOT NULL DEFAULT '[]',
        pain_signals TEXT NOT NULL DEFAULT '[]',
        contact_signals TEXT NOT NULL DEFAULT '[]',
        evidence_snippets TEXT NOT NULL DEFAULT '[]',
        posted_at TEXT,
        deadline_at TEXT,
        risk_level TEXT NOT NULL DEFAULT 'low',
        risk_reasons TEXT NOT NULL DEFAULT '[]',
        score INTEGER NOT NULL DEFAULT 0,
        score_total INTEGER NOT NULL DEFAULT 0,
        score_money INTEGER NOT NULL DEFAULT 0,
        score_fit INTEGER NOT NULL DEFAULT 0,
        score_urgency INTEGER NOT NULL DEFAULT 0,
        score_contactability INTEGER NOT NULL DEFAULT 0,
        score_confidence INTEGER NOT NULL DEFAULT 0,
        score_tier TEXT NOT NULL DEFAULT 'C',
        score_reasons TEXT NOT NULL DEFAULT '[]',
        next_best_action TEXT NOT NULL DEFAULT '',
        ai_summary TEXT NOT NULL DEFAULT '',
        suggested_reply TEXT NOT NULL DEFAULT '',
        analysis_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'NUEVO',
        commercial_status TEXT NOT NULL DEFAULT 'new',
        last_status_at TEXT,
        next_followup_at TEXT,
        last_contacted_at TEXT,
        followup_count INTEGER NOT NULL DEFAULT 0,
        contact_channel TEXT NOT NULL DEFAULT '',
        contact_value TEXT NOT NULL DEFAULT '',
        contact_url TEXT NOT NULL DEFAULT '',
        proposal_value INTEGER,
        won_value INTEGER,
        lost_reason TEXT NOT NULL DEFAULT '',
        ignored_reason TEXT NOT NULL DEFAULT '',
        snoozed_until TEXT,
        commercial_notes TEXT NOT NULL DEFAULT '',
        priority_override TEXT NOT NULL DEFAULT '',
        is_suspicious INTEGER NOT NULL DEFAULT 0,
        raw_text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (buyer_account_id) REFERENCES buyer_accounts (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opportunities_state ON opportunities(state)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_source ON opportunities(source_key)",
    """
    CREATE TABLE IF NOT EXISTS quality_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        corrected_stage TEXT NOT NULL DEFAULT '',
        corrected_grade TEXT NOT NULL DEFAULT '',
        corrected_budget_json TEXT NOT NULL DEFAULT '{}',
        corrected_buyer_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quality_feedback_opportunity ON quality_feedback(opportunity_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quality_feedback_decision ON quality_feedback(decision, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS opportunity_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        event_type TEXT NOT NULL,
        previous_state TEXT,
        new_state TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commercial_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        activity_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        channel TEXT NOT NULL DEFAULT '',
        message_snapshot TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_commercial_activities_opportunity ON commercial_activities(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_commercial_activities_created ON commercial_activities(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS account_activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_account_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        activity_type TEXT NOT NULL,
        related_account_id INTEGER,
        notes TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (buyer_account_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE,
        FOREIGN KEY (related_account_id) REFERENCES buyer_accounts (id) ON DELETE SET NULL,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_account_activities_account ON account_activities(buyer_account_id)",
    """
    CREATE TABLE IF NOT EXISTS buyer_account_duplicate_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_a_id INTEGER NOT NULL,
        account_b_id INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        confidence INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(account_a_id, account_b_id),
        FOREIGN KEY (account_a_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE,
        FOREIGN KEY (account_b_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buyer_account_duplicate_status ON buyer_account_duplicate_candidates(status, confidence DESC)",
    """
    CREATE TABLE IF NOT EXISTS outreach_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER NOT NULL,
        draft_type TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'unknown',
        subject TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        evidence_used_json TEXT NOT NULL DEFAULT '[]',
        tone TEXT NOT NULL DEFAULT 'professional_direct',
        language TEXT NOT NULL DEFAULT 'es',
        quality_warnings_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        regenerated_at TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outreach_drafts_opportunity ON outreach_drafts(opportunity_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_drafts_active_unique ON outreach_drafts(opportunity_id, draft_type, channel) WHERE is_active = 1",
    """
    CREATE TABLE IF NOT EXISTS saved_views (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        query_string TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, name),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_kind TEXT NOT NULL,
        source_key TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        fetched INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        details_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_started_at ON automation_runs(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_source_key ON automation_runs(source_key)",
)

POSTGRES_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id INTEGER PRIMARY KEY,
        keywords TEXT NOT NULL DEFAULT '',
        sectors TEXT NOT NULL DEFAULT '',
        min_budget INTEGER NOT NULL DEFAULT 0,
        preferred_sources TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_policies (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        source_key TEXT UNIQUE NOT NULL,
        display_name TEXT NOT NULL,
        method TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        frequency_minutes INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        config_json TEXT NOT NULL DEFAULT '{}',
        last_run_at TEXT,
        notes TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS target_companies (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        ats_provider TEXT NOT NULL DEFAULT 'unknown',
        ats_slug TEXT NOT NULL DEFAULT '',
        careers_url TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'medium',
        notes TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_signal_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_target_companies_provider_enabled ON target_companies(ats_provider, enabled)",
    "CREATE INDEX IF NOT EXISTS idx_target_companies_slug ON target_companies(ats_provider, ats_slug)",
    """
    CREATE TABLE IF NOT EXISTS buyer_accounts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        normalized_domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        website TEXT NOT NULL DEFAULT '',
        source_first_seen TEXT NOT NULL DEFAULT '',
        first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        opportunity_count INTEGER NOT NULL DEFAULT 0,
        a1_count INTEGER NOT NULL DEFAULT 0,
        a2_count INTEGER NOT NULL DEFAULT 0,
        contacted_count INTEGER NOT NULL DEFAULT 0,
        replied_count INTEGER NOT NULL DEFAULT 0,
        proposal_count INTEGER NOT NULL DEFAULT 0,
        won_count INTEGER NOT NULL DEFAULT 0,
        lost_count INTEGER NOT NULL DEFAULT 0,
        ignored_count INTEGER NOT NULL DEFAULT 0,
        total_estimated_value INTEGER NOT NULL DEFAULT 0,
        total_proposal_value INTEGER NOT NULL DEFAULT 0,
        total_won_value INTEGER NOT NULL DEFAULT 0,
        account_score INTEGER NOT NULL DEFAULT 0,
        account_tier TEXT NOT NULL DEFAULT 'cold',
        primary_pain_signals_json TEXT NOT NULL DEFAULT '[]',
        required_skills_json TEXT NOT NULL DEFAULT '[]',
        contact_signals_json TEXT NOT NULL DEFAULT '[]',
        evidence_summary_json TEXT NOT NULL DEFAULT '[]',
        last_contacted_at TEXT,
        next_followup_at TEXT,
        commercial_status TEXT NOT NULL DEFAULT 'new',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buyer_accounts_tier ON buyer_accounts(account_tier, account_score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_buyer_accounts_normalized_name ON buyer_accounts(normalized_name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_buyer_accounts_normalized_domain ON buyer_accounts(normalized_domain) WHERE normalized_domain <> ''",
    """
    CREATE TABLE IF NOT EXISTS opportunities (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        buyer_account_id INTEGER,
        external_id TEXT,
        source_key TEXT NOT NULL,
        source_type TEXT NOT NULL DEFAULT '',
        source_label TEXT NOT NULL,
        title TEXT NOT NULL,
        company TEXT NOT NULL DEFAULT '',
        buyer_name TEXT NOT NULL DEFAULT '',
        buyer_domain TEXT NOT NULL DEFAULT '',
        country TEXT NOT NULL DEFAULT '',
        url TEXT UNIQUE NOT NULL,
        apply_url TEXT NOT NULL DEFAULT '',
        budget_min INTEGER,
        budget_max INTEGER,
        estimated_value INTEGER,
        budget_text TEXT NOT NULL DEFAULT '',
        currency TEXT NOT NULL DEFAULT 'USD',
        sector TEXT NOT NULL DEFAULT '',
        stack TEXT NOT NULL DEFAULT '[]',
        required_skills TEXT NOT NULL DEFAULT '[]',
        pain_signals TEXT NOT NULL DEFAULT '[]',
        contact_signals TEXT NOT NULL DEFAULT '[]',
        evidence_snippets TEXT NOT NULL DEFAULT '[]',
        posted_at TEXT,
        deadline_at TEXT,
        risk_level TEXT NOT NULL DEFAULT 'low',
        risk_reasons TEXT NOT NULL DEFAULT '[]',
        score INTEGER NOT NULL DEFAULT 0,
        score_total INTEGER NOT NULL DEFAULT 0,
        score_money INTEGER NOT NULL DEFAULT 0,
        score_fit INTEGER NOT NULL DEFAULT 0,
        score_urgency INTEGER NOT NULL DEFAULT 0,
        score_contactability INTEGER NOT NULL DEFAULT 0,
        score_confidence INTEGER NOT NULL DEFAULT 0,
        score_tier TEXT NOT NULL DEFAULT 'C',
        score_reasons TEXT NOT NULL DEFAULT '[]',
        next_best_action TEXT NOT NULL DEFAULT '',
        ai_summary TEXT NOT NULL DEFAULT '',
        suggested_reply TEXT NOT NULL DEFAULT '',
        analysis_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'NUEVO',
        commercial_status TEXT NOT NULL DEFAULT 'new',
        last_status_at TEXT,
        next_followup_at TEXT,
        last_contacted_at TEXT,
        followup_count INTEGER NOT NULL DEFAULT 0,
        contact_channel TEXT NOT NULL DEFAULT '',
        contact_value TEXT NOT NULL DEFAULT '',
        contact_url TEXT NOT NULL DEFAULT '',
        proposal_value INTEGER,
        won_value INTEGER,
        lost_reason TEXT NOT NULL DEFAULT '',
        ignored_reason TEXT NOT NULL DEFAULT '',
        snoozed_until TEXT,
        commercial_notes TEXT NOT NULL DEFAULT '',
        priority_override TEXT NOT NULL DEFAULT '',
        is_suspicious INTEGER NOT NULL DEFAULT 0,
        raw_text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        FOREIGN KEY (buyer_account_id) REFERENCES buyer_accounts (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_opportunities_state ON opportunities(state)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(score DESC)",
    "CREATE INDEX IF NOT EXISTS idx_opportunities_source ON opportunities(source_key)",
    """
    CREATE TABLE IF NOT EXISTS quality_feedback (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        corrected_stage TEXT NOT NULL DEFAULT '',
        corrected_grade TEXT NOT NULL DEFAULT '',
        corrected_budget_json TEXT NOT NULL DEFAULT '{}',
        corrected_buyer_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_quality_feedback_opportunity ON quality_feedback(opportunity_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_quality_feedback_decision ON quality_feedback(decision, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS opportunity_events (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        event_type TEXT NOT NULL,
        previous_state TEXT,
        new_state TEXT,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commercial_activities (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        opportunity_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        activity_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        channel TEXT NOT NULL DEFAULT '',
        message_snapshot TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_commercial_activities_opportunity ON commercial_activities(opportunity_id)",
    "CREATE INDEX IF NOT EXISTS idx_commercial_activities_created ON commercial_activities(created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS account_activities (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        buyer_account_id INTEGER NOT NULL,
        actor_user_id INTEGER,
        activity_type TEXT NOT NULL,
        related_account_id INTEGER,
        notes TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        FOREIGN KEY (buyer_account_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE,
        FOREIGN KEY (related_account_id) REFERENCES buyer_accounts (id) ON DELETE SET NULL,
        FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_account_activities_account ON account_activities(buyer_account_id)",
    """
    CREATE TABLE IF NOT EXISTS buyer_account_duplicate_candidates (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        account_a_id INTEGER NOT NULL,
        account_b_id INTEGER NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        confidence INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        UNIQUE(account_a_id, account_b_id),
        FOREIGN KEY (account_a_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE,
        FOREIGN KEY (account_b_id) REFERENCES buyer_accounts (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_buyer_account_duplicate_status ON buyer_account_duplicate_candidates(status, confidence DESC)",
    """
    CREATE TABLE IF NOT EXISTS outreach_drafts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        opportunity_id INTEGER NOT NULL,
        draft_type TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'unknown',
        subject TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '',
        evidence_used_json TEXT NOT NULL DEFAULT '[]',
        tone TEXT NOT NULL DEFAULT 'professional_direct',
        language TEXT NOT NULL DEFAULT 'es',
        quality_warnings_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        regenerated_at TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (opportunity_id) REFERENCES opportunities (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outreach_drafts_opportunity ON outreach_drafts(opportunity_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_drafts_active_unique ON outreach_drafts(opportunity_id, draft_type, channel) WHERE is_active = 1",
    """
    CREATE TABLE IF NOT EXISTS saved_views (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        query_string TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP::text,
        UNIQUE(user_id, name),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_runs (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        trigger_kind TEXT NOT NULL,
        source_key TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        fetched INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL DEFAULT 0,
        updated INTEGER NOT NULL DEFAULT 0,
        skipped INTEGER NOT NULL DEFAULT 0,
        duration_ms INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        details_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_started_at ON automation_runs(started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_automation_runs_source_key ON automation_runs(source_key)",
)

DEFAULT_REDDIT_POLICY_CONFIG = {
    "urls": [
        "https://www.reddit.com/r/forhire/search.json?q=flair%3AHiring&restrict_sr=1&sort=new&limit=25",
        "https://www.reddit.com/r/freelance_forhire/search.json?q=hiring&restrict_sr=1&sort=new&limit=25",
    ],
    "search_terms": [
        "[hiring] python scraping",
        "[hiring] web scraping contract",
        "[hiring] n8n automation",
        "[hiring] api integration",
        "[hiring] backend automation",
        "[hiring] data engineer remote",
    ],
    "include_terms": [
        "python",
        "scraping",
        "automation",
        "n8n",
        "backend",
        "developer",
        "engineer",
        "software",
        "full stack",
        "full-stack",
        "api",
        "data",
        "etl",
    ],
    "exclude_terms": [
        "[for hire]",
        "for hire",
        "unpaid",
        "volunteer",
        "homework",
        "writer",
        "interviewer",
        "school",
        "minecraft",
        "telegram only",
        "whatsapp only",
    ],
    "max_items": 30,
    "reddit": {
        "enabled": True,
        "defaultVisibility": "review_required",
        "allowlist": [
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
            "SaaS",
            "startups",
            "n8n",
            "automation",
            "nocode",
        ],
        "greylist": [
            "Python",
            "django",
            "flask",
            "reactjs",
            "node",
            "programming",
            "learnprogramming",
            "datascience",
            "MachineLearning",
            "artificial",
            "LocalLLaMA",
            "webscraping",
        ],
        "blocklist": [
            "nosleep",
            "conspiracy",
            "AskReddit",
            "AmItheAsshole",
            "relationship_advice",
            "worldnews",
            "news",
            "politics",
            "memes",
            "funny",
            "gaming",
            "teenagers",
            "NoStupidQuestions",
            "Showerthoughts",
            "WritingPrompts",
            "creepypasta",
            "UnresolvedMysteries",
            "UFOs",
            "aliens",
        ],
        "minCommercialIntentScore": 65,
        "hideRejectedByDefault": True,
        "maxResultsPerRun": 50,
    },
    "quality": {
        "minReadyToContactScore": 75,
        "minBuyerConfidence": 50,
        "rejectCriticalRisk": True,
        "hideDuplicates": True,
    },
}

DEFAULT_WORKANA_POLICY_CONFIG = {
    "search_urls": [
        "https://www.workana.com/es/jobs?category=it-programming&skills=python",
        "https://www.workana.com/es/jobs?category=it-programming&skills=web-scraping",
        "https://www.workana.com/es/jobs?category=it-programming&skills=automation",
        "https://www.workana.com/es/jobs?category=it-programming&skills=n8n",
    ],
    "include_terms": [
        "python",
        "scraping",
        "automation",
        "n8n",
        "backend",
        "api",
        "integracion",
        "integration",
        "data",
        "etl",
        "developer",
        "engineer",
        "software",
    ],
    "exclude_terms": [
        "asistente virtual",
        "virtual assistant",
        "redaccion",
        "writer",
        "marketing",
        "ventas",
        "sales",
        "designer",
        "diseno grafico",
        "community manager",
    ],
    "candidate_limit": 18,
    "max_items": 18,
}

DEFAULT_GREENHOUSE_POLICY_CONFIG = {
    "boards": ["canonical", "vercel", "gitlab", "airtable", "netlify"],
    "include_terms": [
        "engineer",
        "developer",
        "backend",
        "back-end",
        "full stack",
        "full-stack",
        "python",
        "rust",
        "golang",
        "api",
        "platform",
        "data",
        "analytics",
        "automation",
        "integrat",
        "ai",
        "ml",
        "consultant",
        "contractor",
        "freelance",
    ],
    "exclude_terms": [
        "manager",
        "director",
        "head of",
        "sales",
        "account executive",
        "business development",
        "recruiter",
        "talent",
        "legal",
        "finance",
        "accountant",
        "marketing",
        "people partner",
        "support",
        "customer success",
    ],
    "remote_only": True,
    "max_items": 45,
}

DEFAULT_LEVER_POLICY_CONFIG = {
    "companies": ["applydigital"],
    "include_terms": [
        "engineer",
        "backend",
        "back-end",
        "developer",
        "consultant",
        "freelance",
        "martech",
        "automation",
        "solutions architect",
        "agentic",
        "qa",
    ],
    "exclude_terms": [
        "sales",
        "designer",
        "project manager",
        "scrum master",
        "marketing intern",
        "intern ",
        "engagement manager",
    ],
    "max_items": 20,
}

DEFAULT_WWR_POLICY_CONFIG = {
    "rss_urls": [
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    ],
    "include_terms": [
        "engineer",
        "developer",
        "backend",
        "back-end",
        "full stack",
        "full-stack",
        "python",
        "automation",
        "integration",
        "data",
        "api",
        "software",
        "ai",
        "agent",
    ],
    "exclude_terms": [
        "frontend",
        "front-end",
        "design lead",
        "designer",
        "firmware",
        "sales",
        "marketing",
        "customer success",
        "support",
        "recruiter",
        "wordpress",
        "shopware",
        "bubble",
    ],
    "max_items": 30,
}

DEFAULT_HN_JOBS_POLICY_CONFIG = {
    "endpoint": "https://hacker-news.firebaseio.com/v0/jobstories.json",
    "item_url_template": "https://hacker-news.firebaseio.com/v0/item/{id}.json",
    "candidate_limit": 60,
    "include_terms": [
        "engineer",
        "developer",
        "backend",
        "full stack",
        "full-stack",
        "python",
        "rust",
        "automation",
        "api",
        "data",
        "rails",
        "software",
        "ai",
        "ml",
    ],
    "exclude_terms": [
        "designer",
        "sales",
        "marketing",
        "recruiter",
        "customer success",
        "support",
        "intern",
    ],
    "max_items": 20,
}

DEFAULT_HN_ALGOLIA_POLICY_CONFIG = {
    "endpoint": "https://hn.algolia.com/api/v1/search_by_date",
    "queries": [
        "need help with",
        "looking for contractor",
        "freelance",
        "consultant",
        "automation",
        "internal tool",
        "dashboard",
        "data pipeline",
        "scraping",
        "AI agent",
        "workflow automation",
        "Zapier alternative",
        "spreadsheet automation",
    ],
    "tags": "(story,comment)",
    "days_back": 45,
    "hits_per_page": 20,
    "max_items": 50,
    "include_terms": [
        "help",
        "contractor",
        "freelance",
        "consultant",
        "automation",
        "dashboard",
        "scraping",
        "data",
        "pipeline",
        "workflow",
        "ai",
        "agent",
    ],
    "exclude_terms": [
        "homework",
        "unpaid",
        "student project",
        "just curious",
    ],
}

DEFAULT_ATS_SIGNAL_POLICY_CONFIG = {
    "max_jobs_per_company": 50,
    "max_items": 50,
    "include_terms": [
        "engineer",
        "developer",
        "backend",
        "back-end",
        "full stack",
        "full-stack",
        "python",
        "data",
        "analytics",
        "platform",
        "automation",
        "integration",
        "api",
        "ai",
        "ml",
        "cloud",
    ],
    "exclude_terms": [
        "sales",
        "account executive",
        "business development",
        "recruiter",
        "talent",
        "legal",
        "finance",
        "marketing",
        "customer success",
    ],
}

DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS = [
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
]

DEFAULT_PROCUREMENT_LITE_EXCLUDE_TERMS = [
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
]

DEFAULT_TED_EU_POLICY_CONFIG = {
    "endpoint": "https://api.ted.europa.eu/v3/notices/search",
    "query_terms": DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS[:12],
    "cpv_codes": ["48000000", "72000000", "72200000", "72300000", "72400000", "72500000", "72600000", "72700000", "72800000", "72900000"],
    "cpv_prefixes": ["48", "72"],
    "include_terms": DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS,
    "exclude_terms": DEFAULT_PROCUREMENT_LITE_EXCLUDE_TERMS,
    "scope": "ACTIVE",
    "limit": 50,
    "max_items": 50,
    "min_score": 18,
    "max_age_days": 60,
}

DEFAULT_UK_FIND_TENDER_POLICY_CONFIG = {
    "endpoint": "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages",
    "updated_from_days": 21,
    "limit": 50,
    "max_items": 50,
    "cpv_prefixes": ["48", "72"],
    "include_terms": DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS,
    "exclude_terms": DEFAULT_PROCUREMENT_LITE_EXCLUDE_TERMS,
    "min_score": 18,
    "max_age_days": 60,
}

DEFAULT_UK_CONTRACTS_FINDER_POLICY_CONFIG = {
    "endpoint": "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
    "published_from_days": 21,
    "stages": ["planning", "tender"],
    "include_awards": False,
    "limit": 50,
    "max_items": 50,
    "cpv_prefixes": ["48", "72"],
    "include_terms": DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS,
    "exclude_terms": DEFAULT_PROCUREMENT_LITE_EXCLUDE_TERMS,
    "min_score": 18,
    "max_age_days": 60,
}

DEFAULT_WORLD_BANK_PROCUREMENT_POLICY_CONFIG = {
    "endpoint": "https://search.worldbank.org/api/procnotices",
    "query_terms": ["software", "data", "automation", "dashboard", "cloud", "cybersecurity", "information system", "reporting"],
    "notice_types": [
        "Request for Expression of Interest",
        "Invitation for Bids",
        "Invitation for Prequalification",
        "General Procurement Notice",
        "Request for Bids",
    ],
    "rows": 25,
    "max_items": 50,
    "include_terms": DEFAULT_PROCUREMENT_LITE_INCLUDE_TERMS,
    "exclude_terms": DEFAULT_PROCUREMENT_LITE_EXCLUDE_TERMS,
    "min_score": 18,
    "max_age_days": 120,
}

DEFAULT_GITHUB_ISSUES_POLICY_CONFIG = {
    "queries": [
        "\"paid help\" python is:issue is:open",
        "\"bounty\" automation is:issue is:open",
        "\"contract\" \"api integration\" is:issue is:open",
        "\"freelance\" scraping is:issue is:open",
        "\"help wanted\" \"data pipeline\" is:issue is:open",
    ],
    "include_terms": [
        "python",
        "scraping",
        "automation",
        "n8n",
        "backend",
        "api",
        "integration",
        "data",
        "etl",
        "pipeline",
        "bounty",
        "paid",
        "contract",
        "freelance",
        "consultant",
        "help wanted",
    ],
    "exclude_terms": [
        "good first issue",
        "homework",
        "student",
        "unpaid",
        "volunteer",
        "intern",
        "frontend",
        "design",
    ],
    "commercial_signal_terms": [
        "bounty",
        "paid",
        "budget",
        "contract",
        "freelance",
        "consultant",
        "invoice",
        "sponsor",
        "quote",
        "$",
    ],
    "require_commercial_signal": True,
    "sort": "updated",
    "order": "desc",
    "per_page": 20,
    "max_items": 25,
    "min_score": 20,
    "max_age_days": 45,
}

DEFAULT_SAM_GOV_POLICY_CONFIG = {
    "query_terms": [
        "software development",
        "data automation",
        "api integration",
        "dashboard development",
        "database development",
    ],
    "naics_codes": ["541511", "541512", "541519"],
    "notice_types": ["o", "k", "r", "p"],
    "posted_from_days": 21,
    "limit_per_query": 25,
    "max_items": 30,
    "include_terms": [
        "software",
        "development",
        "data",
        "automation",
        "api",
        "dashboard",
        "database",
        "integration",
        "application",
        "web",
        "system",
    ],
    "exclude_terms": [
        "janitorial",
        "construction",
        "hardware only",
        "vehicle",
        "medical supplies",
        "furniture",
        "food service",
    ],
    "min_score": 18,
    "max_age_days": 45,
}

DEFAULT_EMAIL_ALERTS_POLICY_CONFIG = {
    "min_score": 24,
    "max_age_days": 21,
}


DEFAULT_SOURCE_POLICIES = (
    {
        "source_key": "sample_feed",
        "display_name": "Demo Feed Local",
        "method": "fixture_json",
        "risk_level": "low",
        "frequency_minutes": 5,
        "enabled": 0,
        "config_json": "{}",
        "notes": "Fuente local de demostracion. Mantener deshabilitada en produccion real.",
    },
    {
        "source_key": "reddit",
        "display_name": "Reddit Public JSON",
        "method": "official_json_endpoints",
        "risk_level": "medium",
        "frequency_minutes": 30,
        "enabled": 1,
        "config_json": json.dumps(DEFAULT_REDDIT_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Endpoints publicos JSON de Reddit orientados a freelance, automation, scraping y backend.",
    },
    {
        "source_key": "workana_projects",
        "display_name": "Workana Public Projects",
        "method": "public_project_pages",
        "risk_level": "low",
        "frequency_minutes": 90,
        "enabled": 1,
        "config_json": json.dumps(DEFAULT_WORKANA_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente freelance prioritaria: proyectos publicos de Workana con alta intencion de contratar por entregable u horas.",
    },
    {
        "source_key": "greenhouse",
        "display_name": "Greenhouse Public Boards",
        "method": "public_boards_api",
        "risk_level": "low",
        "frequency_minutes": 180,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_GREENHOUSE_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente secundaria: Greenhouse suele traer empleo formal y procesos ATS mas largos. Reactivala solo si quieres complementar con hiring corporativo.",
    },
    {
        "source_key": "lever",
        "display_name": "Lever Public Postings",
        "method": "public_postings_api",
        "risk_level": "low",
        "frequency_minutes": 180,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_LEVER_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente secundaria: Lever trae mas vacantes corporativas que proyectos freelance. Dejamos el conector disponible, pero apagado por default.",
    },
    {
        "source_key": "weworkremotely",
        "display_name": "We Work Remotely RSS",
        "method": "rss_feed",
        "risk_level": "low",
        "frequency_minutes": 120,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_WWR_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente secundaria: buena para remoto, pero mas orientada a empleo formal que a contratos freelance directos.",
    },
    {
        "source_key": "hackernews_jobs",
        "display_name": "Hacker News Jobs",
        "method": "official_api",
        "risk_level": "low",
        "frequency_minutes": 180,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_HN_JOBS_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente secundaria: util para hiring tecnico, pero normalmente no es la mejor puerta de entrada para proyectos freelance rapidos.",
    },
    {
        "source_key": "hn_algolia",
        "display_name": "Hacker News Algolia Signals",
        "method": "algolia_search_api",
        "risk_level": "low",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_HN_ALGOLIA_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Senales comunitarias de HN con intencion comercial ligera. Apagada por defecto; normalmente A2/B salvo compra explicita.",
    },
    {
        "source_key": "greenhouse_jobs",
        "display_name": "Greenhouse Target Signals",
        "method": "target_company_job_board_api",
        "risk_level": "low",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps({**DEFAULT_ATS_SIGNAL_POLICY_CONFIG, "provider": "greenhouse"}, ensure_ascii=False),
        "notes": "Escanea empresas objetivo con Greenhouse y agrupa vacantes tecnicas en una senal comercial por empresa/dia.",
    },
    {
        "source_key": "lever_postings",
        "display_name": "Lever Target Signals",
        "method": "target_company_postings_api",
        "risk_level": "low",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps({**DEFAULT_ATS_SIGNAL_POLICY_CONFIG, "provider": "lever"}, ensure_ascii=False),
        "notes": "Escanea empresas objetivo con Lever y agrupa vacantes tecnicas en una senal comercial por empresa/dia.",
    },
    {
        "source_key": "ashby_jobs",
        "display_name": "Ashby Target Signals",
        "method": "target_company_job_posting_api",
        "risk_level": "low",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps({**DEFAULT_ATS_SIGNAL_POLICY_CONFIG, "provider": "ashby"}, ensure_ascii=False),
        "notes": "Escanea empresas objetivo con Ashby e incluye compensacion solo como evidencia, no como presupuesto inventado.",
    },
    {
        "source_key": "workable_jobs",
        "display_name": "Workable Target Signals",
        "method": "target_company_spi_jobs_api",
        "risk_level": "low",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps({**DEFAULT_ATS_SIGNAL_POLICY_CONFIG, "provider": "workable"}, ensure_ascii=False),
        "notes": "Escanea empresas objetivo con Workable. Requiere WORKABLE_API_TOKEN y queda apagada por defecto.",
    },
    {
        "source_key": "ted_eu",
        "display_name": "TED EU Procurement Lite",
        "method": "official_search_api",
        "risk_level": "low",
        "frequency_minutes": 720,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_TED_EU_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Procurement EU estructurado via TED Search API. Apagado por defecto; filtra CPV/terminos de software, datos, cloud y automatizacion.",
    },
    {
        "source_key": "uk_find_tender",
        "display_name": "UK Find a Tender Lite",
        "method": "ocds_release_package_api",
        "risk_level": "low",
        "frequency_minutes": 720,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_UK_FIND_TENDER_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Find a Tender OCDS JSON. Apagado por defecto; usa notices estructurados y filtra fit tecnico.",
    },
    {
        "source_key": "uk_contracts_finder",
        "display_name": "UK Contracts Finder Lite",
        "method": "ocds_search_api",
        "risk_level": "low",
        "frequency_minutes": 720,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_UK_CONTRACTS_FINDER_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Contracts Finder OCDS Search. Apagado por defecto; excluye awards por default y filtra oportunidades tecnicas.",
    },
    {
        "source_key": "worldbank_procurement",
        "display_name": "World Bank Procurement Lite",
        "method": "official_procurement_notices_api",
        "risk_level": "low",
        "frequency_minutes": 720,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_WORLD_BANK_PROCUREMENT_POLICY_CONFIG, ensure_ascii=False),
        "notes": "World Bank Procurement Notices API. Apagado por defecto; prioriza avisos estructurados de software, datos y reporting.",
    },
    {
        "source_key": "github_issues",
        "display_name": "GitHub Lead Signals",
        "method": "official_search_api",
        "risk_level": "medium",
        "frequency_minutes": 240,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_GITHUB_ISSUES_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Senales tecnicas publicas con posible dolor comercial: issues abiertos con bounty, contrato, pago o ayuda explicita. Usar con tacto y outreach manual.",
    },
    {
        "source_key": "sam_gov",
        "display_name": "SAM.gov Contract Opportunities",
        "method": "official_public_api",
        "risk_level": "low",
        "frequency_minutes": 720,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_SAM_GOV_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Contratos publicos de gobierno USA. Requiere SAM_API_KEY y normalmente implica venta mas lenta, pero con intencion formal y presupuesto.",
    },
    {
        "source_key": "public_pages",
        "display_name": "Paginas Publicas Compatibles",
        "method": "light_html_parse",
        "risk_level": "medium",
        "frequency_minutes": 180,
        "enabled": 0,
        "config_json": json.dumps({"urls": []}),
        "notes": "Configurar URLs compatibles con JSON: {\"urls\": [\"https://...\"]}.",
    },
    {
        "source_key": "email_alerts",
        "display_name": "Alertas por Correo",
        "method": "n8n_webhook_import",
        "risk_level": "low",
        "frequency_minutes": 60,
        "enabled": 0,
        "config_json": json.dumps(DEFAULT_EMAIL_ALERTS_POLICY_CONFIG, ensure_ascii=False),
        "notes": "Fuente recomendada para Upwork, Workana y otros marketplaces via n8n: captura alertas oficiales por correo y empujalas al import interno.",
    },
)


class Database:
    def __init__(self, connection, engine: str) -> None:
        self.connection = connection
        self.engine = engine

    def execute(self, query: str, params: Sequence | None = None):
        cursor = self.connection.cursor()
        cursor.execute(self._prepare_query(query), tuple(params or ()))
        return cursor

    def executescript(self, statements: Sequence[str]) -> None:
        cursor = self.connection.cursor()
        for statement in statements:
            cursor.execute(self._prepare_query(statement))

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _prepare_query(self, query: str) -> str:
        if self.engine == "postgres":
            translated_parts: list[str] = []
            for character in query:
                if character == "?":
                    translated_parts.append("%s")
                elif character == "%":
                    translated_parts.append("%%")
                else:
                    translated_parts.append(character)
            return "".join(translated_parts)
        return query


def get_db() -> Database:
    if "db" not in g:
        database_url = current_app.config.get("DATABASE_URL")
        if database_url:
            g.db = _connect_postgres(database_url)
        else:
            database_path = Path(current_app.config["DATABASE"])
            g.db = _connect_sqlite(database_path)

    return g.db


def close_db(_error: Exception | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def bootstrap_database() -> None:
    db = get_db()
    schema_statements = POSTGRES_SCHEMA_STATEMENTS if db.engine == "postgres" else SQLITE_SCHEMA_STATEMENTS
    db.executescript(schema_statements)
    _ensure_runtime_columns(db)
    for policy in DEFAULT_SOURCE_POLICIES:
        db.execute(
            """
            INSERT INTO source_policies (
                source_key,
                display_name,
                method,
                risk_level,
                frequency_minutes,
                enabled,
                config_json,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO NOTHING
            """,
            (
                policy["source_key"],
                policy["display_name"],
                policy["method"],
                policy["risk_level"],
                policy["frequency_minutes"],
                policy["enabled"],
                policy["config_json"],
                policy["notes"],
            ),
        )
    _refresh_source_policy_defaults(db)
    _cleanup_demo_data(db)
    db.commit()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command() -> None:
        bootstrap_database()
        click.echo("Base de datos inicializada.")

    @app.cli.command("recompute-quality")
    @click.option("--limit", type=int, default=None, help="Maximo de oportunidades a reprocesar.")
    def recompute_quality_command(limit: int | None = None) -> None:
        from .services.pipeline import recompute_quality

        summary = recompute_quality(get_db(), limit=limit)
        click.echo(
            "Processed: {processed} | Updated: {updated} | Rejected: {rejected} | "
            "Duplicates hidden: {duplicates_hidden} | Errors: {errors}".format(**summary)
        )


def _connect_sqlite(database_path: Path) -> Database:
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    try:
        connection.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.OperationalError:
        # Some restricted environments cannot update journal settings at all.
        pass
    try:
        connection.execute("PRAGMA synchronous = NORMAL;")
    except sqlite3.OperationalError:
        pass
    return Database(connection, engine="sqlite")


def _connect_postgres(database_url: str) -> Database:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("DATABASE_URL must use a postgres:// or postgresql:// scheme.")

    try:
        from psycopg import connect
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for PostgreSQL support. Install dependencies from requirements.txt."
        ) from exc

    connection = connect(database_url, row_factory=dict_row)
    return Database(connection, engine="postgres")


def _refresh_source_policy_defaults(db: Database) -> None:
    rows = db.execute(
        """
        SELECT id, source_key, enabled, config_json, notes, frequency_minutes
        FROM source_policies
        """
    ).fetchall()

    is_production = current_app.config.get("ENVIRONMENT") == "production"

    for row in rows:
        source_key = row["source_key"]
        config = _load_json(row["config_json"], default={})

        if row["enabled"] == 0 and _connector_enabled_from_env(source_key):
            db.execute("UPDATE source_policies SET enabled = 1 WHERE id = ?", (row["id"],))

        if source_key == "sample_feed":
            if is_production and row["enabled"] == 1 and config == {}:
                db.execute(
                    "UPDATE source_policies SET enabled = 0, notes = ? WHERE id = ?",
                    ("Fuente local de demostracion. Mantener deshabilitada en produccion real.", row["id"]),
                )
            continue

        if source_key == "reddit" and is_production and (
            _is_blank_or_legacy_reddit_config(config) or _is_current_reddit_public_config(config)
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 120,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_REDDIT_POLICY_CONFIG, ensure_ascii=False),
                    "Deshabilitado en produccion Vercel: Reddit bloquea estos endpoints publicos. Reactivar manualmente solo si luego agregas una integracion autenticada.",
                    row["id"],
                ),
            )
            continue

        if source_key == "reddit" and (
            _is_blank_or_legacy_reddit_config(config)
            or _config_missing_keys(config, "reddit", "quality")
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 1,
                    frequency_minutes = 30,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_REDDIT_POLICY_CONFIG, ensure_ascii=False),
                    "Endpoints publicos JSON de Reddit orientados a freelance, automation, scraping y backend.",
                    row["id"],
                ),
            )
            continue

        if source_key == "workana_projects" and (
            _is_empty_collection_config(config, "search_urls")
            or _config_missing_terms(config, "exclude_terms", ("asistente virtual", "marketing", "sales"))
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 1,
                    frequency_minutes = 90,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_WORKANA_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente freelance prioritaria: proyectos publicos de Workana con alta intencion de contratar por entregable u horas.",
                    row["id"],
                ),
            )
            continue

        if source_key == "greenhouse" and (
            _is_empty_collection_config(config, "boards")
            or _config_missing_terms(config, "exclude_terms", ("manager", "director", "head of"))
            or (
                _config_matches_default(config, DEFAULT_GREENHOUSE_POLICY_CONFIG)
                and row["notes"]
                == "Boards publicos de empresas remotas filtrados a roles de ingenieria, data y automatizacion."
            )
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 180,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_GREENHOUSE_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente secundaria: Greenhouse suele traer empleo formal y procesos ATS mas largos. Reactivala solo si quieres complementar con hiring corporativo.",
                    row["id"],
                ),
            )
            continue

        if source_key == "lever" and (
            _is_empty_collection_config(config, "companies")
            or (
                _config_matches_default(config, DEFAULT_LEVER_POLICY_CONFIG)
                and row["notes"]
                == "Companies de Lever filtradas a roles tecnicos y freelance con mejor probabilidad de cierre rapido."
            )
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 180,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_LEVER_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente secundaria: Lever trae mas vacantes corporativas que proyectos freelance. Dejamos el conector disponible, pero apagado por default.",
                    row["id"],
                ),
            )
            continue

        if source_key == "weworkremotely" and (
            _is_empty_collection_config(config, "rss_urls")
            or _config_missing_terms(config, "exclude_terms", ("frontend", "front-end", "firmware", "bubble"))
            or (
                _config_matches_default(config, DEFAULT_WWR_POLICY_CONFIG)
                and row["notes"]
                == "RSS oficial de We Work Remotely filtrado a roles tecnicos remotos con mejor potencial de cierre."
            )
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 120,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_WWR_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente secundaria: buena para remoto, pero mas orientada a empleo formal que a contratos freelance directos.",
                    row["id"],
                ),
            )
            continue

        if source_key == "hackernews_jobs" and (
            _is_blank_or_missing_keys(
                config,
                "endpoint",
                "item_url_template",
            )
            or (
                _config_matches_default(config, DEFAULT_HN_JOBS_POLICY_CONFIG)
                and row["notes"]
                == "API oficial de Hacker News Jobs filtrada a roles tecnicos con URLs de aplicacion reales."
            )
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 180,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_HN_JOBS_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente secundaria: util para hiring tecnico, pero normalmente no es la mejor puerta de entrada para proyectos freelance rapidos.",
                    row["id"],
                ),
            )
            continue

        if source_key == "hn_algolia" and (
            _is_empty_collection_config(config, "queries")
            or _config_missing_keys(config, "endpoint", "hits_per_page")
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 240,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_HN_ALGOLIA_POLICY_CONFIG, ensure_ascii=False),
                    "Senales comunitarias de HN con intencion comercial ligera. Apagada por defecto; normalmente A2/B salvo compra explicita.",
                    row["id"],
                ),
            )
            continue

        if source_key in {"greenhouse_jobs", "lever_postings", "ashby_jobs", "workable_jobs"} and (
            _config_missing_keys(config, "provider", "max_jobs_per_company")
            or _is_empty_collection_config(config, "include_terms")
        ):
            provider = {
                "greenhouse_jobs": "greenhouse",
                "lever_postings": "lever",
                "ashby_jobs": "ashby",
                "workable_jobs": "workable",
            }[source_key]
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 240,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps({**DEFAULT_ATS_SIGNAL_POLICY_CONFIG, "provider": provider}, ensure_ascii=False),
                    f"Escanea empresas objetivo con {provider}. Agrupa vacantes tecnicas en una senal comercial por empresa/dia.",
                    row["id"],
                ),
            )
            continue

        if source_key in {"ted_eu", "uk_find_tender", "uk_contracts_finder", "worldbank_procurement"} and (
            _config_missing_keys(config, "endpoint", "max_items")
            or _is_empty_collection_config(config, "include_terms")
        ):
            default_config = {
                "ted_eu": DEFAULT_TED_EU_POLICY_CONFIG,
                "uk_find_tender": DEFAULT_UK_FIND_TENDER_POLICY_CONFIG,
                "uk_contracts_finder": DEFAULT_UK_CONTRACTS_FINDER_POLICY_CONFIG,
                "worldbank_procurement": DEFAULT_WORLD_BANK_PROCUREMENT_POLICY_CONFIG,
            }[source_key]
            notes = {
                "ted_eu": "Procurement EU estructurado via TED Search API. Apagado por defecto; filtra CPV/terminos de software, datos, cloud y automatizacion.",
                "uk_find_tender": "Find a Tender OCDS JSON. Apagado por defecto; usa notices estructurados y filtra fit tecnico.",
                "uk_contracts_finder": "Contracts Finder OCDS Search. Apagado por defecto; excluye awards por default y filtra oportunidades tecnicas.",
                "worldbank_procurement": "World Bank Procurement Notices API. Apagado por defecto; prioriza avisos estructurados de software, datos y reporting.",
            }[source_key]
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 720,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(default_config, ensure_ascii=False),
                    notes,
                    row["id"],
                ),
            )
            continue

        if source_key == "github_issues" and (
            _is_empty_collection_config(config, "queries")
            or _config_missing_terms(config, "commercial_signal_terms", ("bounty", "paid", "contract"))
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 240,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_GITHUB_ISSUES_POLICY_CONFIG, ensure_ascii=False),
                    "Senales tecnicas publicas con posible dolor comercial: issues abiertos con bounty, contrato, pago o ayuda explicita. Usar con tacto y outreach manual.",
                    row["id"],
                ),
            )
            continue

        if source_key == "sam_gov" and (
            _is_empty_collection_config(config, "query_terms")
            or _is_empty_collection_config(config, "notice_types")
            or _config_missing_keys(config, "posted_from_days", "limit_per_query")
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 720,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_SAM_GOV_POLICY_CONFIG, ensure_ascii=False),
                    "Contratos publicos de gobierno USA. Requiere SAM_API_KEY y normalmente implica venta mas lenta, pero con intencion formal y presupuesto.",
                    row["id"],
                ),
            )
            continue

        if source_key == "email_alerts" and (
            _config_missing_keys(config, "min_score", "max_age_days")
            or (
                _config_matches_default(config, DEFAULT_EMAIL_ALERTS_POLICY_CONFIG)
                and row["notes"]
                == "Pensada para n8n: leer correo/IMAP o Gmail, normalizar vacantes y enviarlas al endpoint interno de importacion."
            )
        ):
            db.execute(
                """
                UPDATE source_policies
                SET enabled = 0,
                    frequency_minutes = 60,
                    config_json = ?,
                    notes = ?
                WHERE id = ?
                """,
                (
                    json.dumps(DEFAULT_EMAIL_ALERTS_POLICY_CONFIG, ensure_ascii=False),
                    "Fuente recomendada para Upwork, Workana y otros marketplaces via n8n: captura alertas oficiales por correo y empujalas al import interno.",
                    row["id"],
                ),
            )


def _cleanup_demo_data(db: Database) -> None:
    if current_app.config.get("ENVIRONMENT") != "production":
        return

    demo_rows = db.execute(
        "SELECT id FROM opportunities WHERE source_key = 'sample_feed' OR url LIKE 'https://sample.local/%'"
    ).fetchall()
    for row in demo_rows:
        db.execute("DELETE FROM opportunity_events WHERE opportunity_id = ?", (row["id"],))
        db.execute("DELETE FROM commercial_activities WHERE opportunity_id = ?", (row["id"],))
        db.execute("DELETE FROM outreach_drafts WHERE opportunity_id = ?", (row["id"],))
    db.execute(
        "DELETE FROM opportunities WHERE source_key = 'sample_feed' OR url LIKE 'https://sample.local/%'"
    )
    db.execute(
        """
        DELETE FROM buyer_accounts
        WHERE id NOT IN (
            SELECT DISTINCT buyer_account_id
            FROM opportunities
            WHERE buyer_account_id IS NOT NULL
        )
        """
    )


def _ensure_runtime_columns(db: Database) -> None:
    required_columns = {
        "opportunities": {
            "buyer_account_id": "INTEGER",
            "analysis_json": "TEXT NOT NULL DEFAULT '{}'",
            "source_type": "TEXT NOT NULL DEFAULT ''",
            "buyer_name": "TEXT NOT NULL DEFAULT ''",
            "buyer_domain": "TEXT NOT NULL DEFAULT ''",
            "country": "TEXT NOT NULL DEFAULT ''",
            "apply_url": "TEXT NOT NULL DEFAULT ''",
            "estimated_value": "INTEGER",
            "required_skills": "TEXT NOT NULL DEFAULT '[]'",
            "pain_signals": "TEXT NOT NULL DEFAULT '[]'",
            "contact_signals": "TEXT NOT NULL DEFAULT '[]'",
            "evidence_snippets": "TEXT NOT NULL DEFAULT '[]'",
            "deadline_at": "TEXT",
            "score_total": "INTEGER NOT NULL DEFAULT 0",
            "score_money": "INTEGER NOT NULL DEFAULT 0",
            "score_fit": "INTEGER NOT NULL DEFAULT 0",
            "score_urgency": "INTEGER NOT NULL DEFAULT 0",
            "score_contactability": "INTEGER NOT NULL DEFAULT 0",
            "score_confidence": "INTEGER NOT NULL DEFAULT 0",
            "score_tier": "TEXT NOT NULL DEFAULT 'C'",
            "score_reasons": "TEXT NOT NULL DEFAULT '[]'",
            "next_best_action": "TEXT NOT NULL DEFAULT ''",
            "commercial_status": "TEXT NOT NULL DEFAULT 'new'",
            "last_status_at": "TEXT",
            "next_followup_at": "TEXT",
            "last_contacted_at": "TEXT",
            "followup_count": "INTEGER NOT NULL DEFAULT 0",
            "contact_channel": "TEXT NOT NULL DEFAULT ''",
            "contact_value": "TEXT NOT NULL DEFAULT ''",
            "contact_url": "TEXT NOT NULL DEFAULT ''",
            "proposal_value": "INTEGER",
            "won_value": "INTEGER",
            "lost_reason": "TEXT NOT NULL DEFAULT ''",
            "ignored_reason": "TEXT NOT NULL DEFAULT ''",
            "snoozed_until": "TEXT",
            "commercial_notes": "TEXT NOT NULL DEFAULT ''",
            "priority_override": "TEXT NOT NULL DEFAULT ''",
        }
    }

    for table_name, columns in required_columns.items():
        existing = _get_table_columns(db, table_name)
        for column_name, column_definition in columns.items():
            if column_name in existing:
                continue
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    db.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_buyer_account ON opportunities(buyer_account_id)")
    db.commit()


def _get_table_columns(db: Database, table_name: str) -> set[str]:
    if db.engine == "sqlite":
        rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    rows = db.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = ?
        """,
        (table_name,),
    ).fetchall()
    return {row["column_name"] for row in rows}


def _load_json(raw_value: str, *, default):
    try:
        return json.loads(raw_value or "")
    except json.JSONDecodeError:
        return default


def _is_blank_or_legacy_reddit_config(config: dict) -> bool:
    if not config:
        return True

    urls = tuple(config.get("urls") or ())
    search_terms = tuple(config.get("search_terms") or ())
    legacy_urls = (
        "https://www.reddit.com/r/forhire/new/.json?limit=20",
        "https://www.reddit.com/r/freelance_forhire/new/.json?limit=20",
    )
    legacy_search_terms = (
        "python scraping freelance",
        "n8n automation contract",
        "rust backend freelance",
    )

    return urls == legacy_urls and search_terms == legacy_search_terms


def _is_current_reddit_public_config(config: dict) -> bool:
    if not config:
        return False

    urls = tuple(config.get("urls") or ())
    search_terms = tuple(config.get("search_terms") or ())
    return urls == tuple(DEFAULT_REDDIT_POLICY_CONFIG["urls"]) and search_terms == tuple(
        DEFAULT_REDDIT_POLICY_CONFIG["search_terms"]
    )


def _is_empty_collection_config(config: dict, key: str) -> bool:
    if not config:
        return True
    return not bool(config.get(key))


def _is_blank_or_missing_keys(config: dict, *keys: str) -> bool:
    if not config:
        return True
    return any(not config.get(key) for key in keys)


def _config_missing_terms(config: dict, key: str, terms: tuple[str, ...]) -> bool:
    if not config:
        return True
    values = {str(item).strip().lower() for item in config.get(key, []) if str(item).strip()}
    return any(term.lower() not in values for term in terms)


def _config_matches_default(config: dict, expected: dict) -> bool:
    return bool(config) and config == expected


def _config_missing_keys(config: dict, *keys: str) -> bool:
    if not config:
        return True
    return any(key not in config for key in keys)


def _connector_enabled_from_env(source_key: str) -> bool:
    env_flag_by_source = {
        "hn_algolia": "HN_ALGOLIA_ENABLED",
        "greenhouse_jobs": "GREENHOUSE_ENABLED",
        "lever_postings": "LEVER_ENABLED",
        "ashby_jobs": "ASHBY_ENABLED",
        "workable_jobs": "WORKABLE_ENABLED",
        "ted_eu": "TED_EU_ENABLED",
        "uk_find_tender": "UK_FIND_TENDER_ENABLED",
        "uk_contracts_finder": "UK_CONTRACTS_FINDER_ENABLED",
        "worldbank_procurement": "WORLD_BANK_PROCUREMENT_ENABLED",
    }
    flag_name = env_flag_by_source.get(source_key)
    return bool(flag_name and current_app.config.get(flag_name))
