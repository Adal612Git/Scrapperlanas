"""
Bot Detector - Identifies suspicious Reddit accounts

Analyzes Reddit user metadata to detect bots and spam accounts.
Filters based on:
- Account karma (total and post/comment ratio)
- Account age
- Username patterns (e.g., bot123, user456789)
- Posting frequency patterns
"""

import re
from datetime import datetime, timezone
from src.utils.logger import get_logger

log = get_logger("BOT_DETECTOR")

class BotDetector:
    def __init__(self):
        # Thresholds for bot detection
        self.MIN_KARMA = 50  # Minimum total karma to not be flagged
        self.MIN_ACCOUNT_AGE_DAYS = 30  # Minimum account age in days
        
        # Suspicious username patterns
        self.suspicious_patterns = [
            re.compile(r'^[a-z]+\d{5,}$', re.IGNORECASE),  # e.g., user12345
            re.compile(r'^bot[_-]?', re.IGNORECASE),  # e.g., bot_xyz
            re.compile(r'bot$', re.IGNORECASE),  # e.g., spambot
            re.compile(r'^(test|spam|fake)', re.IGNORECASE),
            re.compile(r'^[a-z]{1,3}\d{6,}$', re.IGNORECASE),  # e.g., ab123456
        ]
    
    def is_bot(self, post_data: dict) -> tuple[bool, str]:
        """
        Detect if a post is from a bot account.
        
        Args:
            post_data: Reddit post data from JSON API
            
        Returns:
            (is_bot: bool, reason: str)
        """
        author = post_data.get("author", "[deleted]")
        
        # Skip deleted/automod but don't block them - they might be legitimate
        # We'll let other filters handle this
        if author in ["[deleted]", "AutoModerator", "[removed]"]:
            return False, "skipped_deleted"
        
        # Check username patterns
        for pattern in self.suspicious_patterns:
            if pattern.search(author):
                log.debug(f"🤖 Suspicious username pattern: {author}")
                return True, "suspicious_username"
        
        # NOTE: Reddit's public .json API doesn't give us all user metadata
        # We would need to fetch /user/{username}/about.json separately to get:
        # - Total karma
        # - Account creation date
        # For MVP, we'll skip this extra request for performance
        # But we can still use post-level signals
        
        # Check if author flair contains "bot" or suspicious keywords
        author_flair = post_data.get("author_flair_text", "")
        if author_flair and any(kw in author_flair.lower() for kw in ["bot", "spam", "fake"]):
            log.debug(f"🤖 Suspicious flair: {author_flair}")
            return True, "suspicious_flair"
        
        # Check if post is from a very new account (if we have created_utc)
        # Note: created_utc is for the POST, not the USER
        # We'd need a separate API call to get user account age
        
        return False, "looks_human"
    
    async def is_bot_deep(self, author: str, http_client) -> tuple[bool, str]:
        """
        Deep bot detection - requires additional API call to user profile.
        Only use this for high-value posts to avoid rate limits.
        
        Args:
            author: Reddit username
            http_client: ResilientClient to make requests
            
        Returns:
            (is_bot: bool, reason: str)
        """
        if author in ["[deleted]", "AutoModerator", "[removed]"]:
            return True, "deleted_or_automod"
        
        # Fetch user about data
        url = f"https://www.reddit.com/user/{author}/about.json"
        data = await http_client.fetch(url)
        
        if not data:
            log.warning(f"⚠️  Could not fetch user data for {author}")
            return False, "api_error"
        
        try:
            import json
            user_data = json.loads(data)["data"]
            
            # Check total karma
            total_karma = user_data.get("total_karma", 0) or \
                         (user_data.get("link_karma", 0) + user_data.get("comment_karma", 0))
            
            if total_karma < self.MIN_KARMA:
                log.debug(f"🤖 Low karma: {total_karma} for {author}")
                return True, f"low_karma_{total_karma}"
            
            # Check account age
            created_utc = user_data.get("created_utc", 0)
            if created_utc:
                account_age_days = (datetime.now(timezone.utc).timestamp() - created_utc) / 86400
                if account_age_days < self.MIN_ACCOUNT_AGE_DAYS:
                    log.debug(f"🤖 New account: {account_age_days:.1f} days for {author}")
                    return True, f"new_account_{int(account_age_days)}d"
            
            return False, "verified_human"
            
        except Exception as e:
            log.error(f"Error parsing user data for {author}: {e}")
            return False, "parse_error"
