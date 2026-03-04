class PeopleScorer:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.keywords = cfg.get(
            "score_keywords",
            [
                "dating",
                "date",
                "relationship",
                "meet",
                "meetup",
                "hang out",
                "friends",
                "friendship",
                "networking",
                "language exchange",
                "coffee",
                "citas",
                "cita",
                "conocer",
                "amistad",
                "pareja",
                "salir",
            ],
        )

        self.intimacy_keywords = cfg.get(
            "score_bonus_keywords",
            [
                "romance",
                "romantic",
                "algo serio",
                "serio",
                "chemistry",
                "connection",
            ],
        )

    def score(self, text: str) -> int:
        score = 0
        text = (text or "").lower()

        for kw in self.keywords:
            if kw in text:
                score += 5

        for kw in self.intimacy_keywords:
            if kw in text:
                score += 8

        return score
