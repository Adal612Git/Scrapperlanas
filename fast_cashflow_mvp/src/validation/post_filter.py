import re

class PostFilter:
    def __init__(self):
        self._spam_patterns = [
            re.compile(r"data entry", re.IGNORECASE),
            re.compile(r"typing job", re.IGNORECASE),
            re.compile(r"looking for \d+ people", re.IGNORECASE),
            re.compile(r"no experience", re.IGNORECASE),
        ]

        # Basic scam/phishing signals to avoid time-wasters
        self._scam_patterns = [
            re.compile(r"verification fee", re.IGNORECASE),
            re.compile(r"registration fee", re.IGNORECASE),
            re.compile(r"pay(ing)? a? fee", re.IGNORECASE),
            re.compile(r"upfront payment", re.IGNORECASE),
            re.compile(r"gift card", re.IGNORECASE),
            re.compile(r"cash app", re.IGNORECASE),
            re.compile(r"crypto", re.IGNORECASE),
            re.compile(r"wire transfer", re.IGNORECASE),
            re.compile(r"check payment", re.IGNORECASE),
            re.compile(r"whatsapp", re.IGNORECASE),
            re.compile(r"telegram", re.IGNORECASE),
            re.compile(r"dm me on", re.IGNORECASE),
            re.compile(r"contact me on", re.IGNORECASE),
            re.compile(r"bit\.ly/", re.IGNORECASE),
            re.compile(r"tinyurl\.com/", re.IGNORECASE),
        ]

    def should_accept(self, title: str, selftext: str, flair: str):
        title = title or ""
        selftext = selftext or ""
        flair = flair or ""

        title_flair = f"{title}\n{flair}".lower()
        if "[for hire]" in title_flair or "for hire" in title_flair:
            return False, "intent_for_hire"
        
        if "[offer]" in title_flair:
            return False, "intent_offer"

        # Accept if [Hiring], [Task], or hiring-related keywords appear in title/flair
        accepted_intents = ["[hiring]", "hiring", "[task]", "looking for", "seeking"]
        if not any(intent in title_flair for intent in accepted_intents):
            return False, "intent_not_hiring"

        body = selftext.strip()
        if not body or body.lower() in ("[deleted]", "[removed]"):
            return False, "empty_or_removed"

        haystack = f"{title}\n{selftext}\n{flair}"
        for pattern in self._spam_patterns:
            if pattern.search(haystack):
                return False, "spam_pattern"

        for pattern in self._scam_patterns:
            if pattern.search(haystack):
                return False, "scam_pattern"

        return True, "ok"
