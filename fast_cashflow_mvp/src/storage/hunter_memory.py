import sqlite3
import time

BUSY_SLEEP_SECONDS = 0.3
BUSY_RETRIES = 10

class HunterMemory:
    def __init__(self, path="hunter_memory.db"):
        self.conn = sqlite3.connect(
            path,
            check_same_thread=False,
            timeout=30.0
        )
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=30000;")
        self._create_table()

    def _create_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_posts (
                id TEXT PRIMARY KEY,
                url TEXT,
                content_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Ensure columns exist for older DBs
        self._ensure_column("url", "TEXT")
        self._ensure_column("content_hash", "TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_url ON seen_posts(url)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_seen_hash ON seen_posts(content_hash)")
        self.conn.commit()

    def _ensure_column(self, name: str, col_type: str):
        cur = self.conn.execute("PRAGMA table_info(seen_posts)")
        cols = {row[1] for row in cur.fetchall()}
        if name in cols:
            return
        self.conn.execute(f"ALTER TABLE seen_posts ADD COLUMN {name} {col_type}")

    def has(self, post_id: str, url: str = None, content_hash: str = None) -> bool:
        for _ in range(BUSY_RETRIES):
            try:
                if post_id:
                    cur = self.conn.execute(
                        "SELECT 1 FROM seen_posts WHERE id = ? LIMIT 1",
                        (post_id,)
                    )
                    if cur.fetchone() is not None:
                        return True
                if url:
                    cur = self.conn.execute(
                        "SELECT 1 FROM seen_posts WHERE url = ? LIMIT 1",
                        (url,)
                    )
                    if cur.fetchone() is not None:
                        return True
                if content_hash:
                    cur = self.conn.execute(
                        "SELECT 1 FROM seen_posts WHERE content_hash = ? LIMIT 1",
                        (content_hash,)
                    )
                    if cur.fetchone() is not None:
                        return True
                return False
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                time.sleep(BUSY_SLEEP_SECONDS)
        raise sqlite3.OperationalError("database is locked (after retries)")

    def add(self, post_id: str, url: str = None, content_hash: str = None):
        for _ in range(BUSY_RETRIES):
            try:
                self.conn.execute(
                    "INSERT INTO seen_posts (id, url, content_hash) VALUES (?, ?, ?)",
                    (post_id, url, content_hash)
                )
                self.conn.commit()
                return
            except sqlite3.IntegrityError:
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                time.sleep(BUSY_SLEEP_SECONDS)
        raise sqlite3.OperationalError("database is locked (after retries)")
