import re


class PeoplePostFilter:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.min_age = int(cfg.get("min_age", 18))
        self.require_age = bool(cfg.get("require_age", True))
        self.require_female = bool(cfg.get("require_female", True))

        self._spam_patterns = self._compile_patterns(
            cfg.get(
                "spam_patterns",
                [
                    "onlyfans",
                    "fansly",
                    "snapchat",
                    "premium",
                    "escort",
                    "sugar\\s*(baby|daddy)",
                ],
            )
        )

        self._scam_patterns = self._compile_patterns(
            cfg.get(
                "scam_patterns",
                [
                    "verification fee",
                    "registration fee",
                    "upfront payment",
                    "gift card",
                    "cash app",
                    "crypto",
                    "wire transfer",
                    "check payment",
                    "whatsapp",
                    "telegram",
                    "dm me on",
                    "contact me on",
                    "bit\\.ly/",
                    "tinyurl\\.com/",
                ],
            )
        )

        self._female_patterns = self._compile_patterns(
            cfg.get(
                "female_patterns",
                [
                    "\\bf4[amr]\\b",
                    "\\b\\[?f4[amr]\\]?\\b",
                    "\\bf\\d{2}\\b",
                    "\\b\\d{2}f\\b",
                    "\\bfemale\\b",
                    "\\bwoman\\b",
                    "\\bgirl\\b",
                ],
            )
        )

        self._age_patterns = self._compile_patterns(
            [
                "\\b(\\d{2})\\s*(?:yo|y/o|years? old|años?)\\b",
                "\\b(\\d{2})[fm]\\b",
                "\\b[fm](\\d{2})\\b",
            ]
        )

        self._minor_patterns = self._compile_patterns(
            cfg.get(
                "minor_patterns",
                [
                    "\\bminor\\b",
                    "\\bunderage\\b",
                    "\\bhigh school\\b",
                ],
            )
        )

        self._location_terms = [
            term.lower()
            for term in cfg.get(
                "location_terms",
                [
                    "mexico",
                    "méxico",
                    "cdmx",
                    "ciudad de mexico",
                    "ciudad de méxico",
                    "guadalajara",
                    "monterrey",
                    "puebla",
                    "queretaro",
                    "querétaro",
                    "tijuana",
                    "merida",
                    "mérida",
                    "cancun",
                    "cancún",
                ],
            )
        ]

        self._required_any_terms = [
            term.lower()
            for term in cfg.get("required_any_terms", [])
        ]

    def _compile_patterns(self, patterns):
        return [re.compile(p, re.IGNORECASE) for p in patterns]

    def _extract_age(self, text: str):
        for pattern in self._age_patterns:
            match = pattern.search(text)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return None
        return None

    def should_accept(self, title: str, selftext: str, flair: str):
        title = title or ""
        selftext = selftext or ""
        flair = flair or ""

        title_flair = f"{title}\n{flair}"
        if self.require_female and not any(p.search(title_flair) for p in self._female_patterns):
            return False, "not_female"

        body = selftext.strip()
        if not body or body.lower() in ("[deleted]", "[removed]"):
            return False, "empty_or_removed"

        haystack = f"{title}\n{selftext}\n{flair}"
        if any(p.search(haystack) for p in self._minor_patterns):
            return False, "minor_signal"

        age = self._extract_age(haystack)
        if self.require_age and (age is None or age < self.min_age):
            return False, "age_not_confirmed"

        if self._location_terms and not any(term in haystack.lower() for term in self._location_terms):
            return False, "not_mexico"

        for pattern in self._spam_patterns:
            if pattern.search(haystack):
                return False, "spam_pattern"

        for pattern in self._scam_patterns:
            if pattern.search(haystack):
                return False, "scam_pattern"

        if self._required_any_terms:
            if not any(term in haystack.lower() for term in self._required_any_terms):
                return False, "missing_required_terms"

        return True, "ok"
