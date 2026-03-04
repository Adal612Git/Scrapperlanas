from rapidfuzz import fuzz
from src.storage.sqlite_repo import SqliteRepo

class Deduplicator:
    def __init__(self, db: SqliteRepo):
        self.db = db

    def is_new(self, title: str) -> bool:
        recent_titles = self.db.get_recent_titles(limit=50)
        for old_title in recent_titles:
            ratio = fuzz.ratio(title.lower(), old_title.lower())
            if ratio > 85:
                return False
        return True
