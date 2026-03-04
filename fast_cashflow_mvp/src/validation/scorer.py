from config import cfg

class Scorer:
    def __init__(self):
        # Weighted keywords for more nuanced scoring
        self.weights = {
            "scraping": 15,
            "bot": 15,
            "automation": 15,
            "python": 10,
            ".net": 10,
            "c#": 10,
            "backend": 8,
            "api": 8,
            "n8n": 12,
            "agent": 12,
            "llm": 12,
            "workflow": 10,
            "remote": 5,
            "remoto": 5,
            "urgent": 10,
            "asap": 10,
            "fix": 10
        }
        self.keywords = cfg.KEYWORDS

    def hard_filter(self, text: str) -> int:
        """Relevance scoring for alerts."""
        score = 0
        text = (text or "").lower()

        # Apply weights
        for kw, weight in self.weights.items():
            if kw in text:
                score += weight

        # Bonus for budget mention
        if "$" in text or "usd" in text or "budget" in text or "paid" in text:
            score += 5

        return score

