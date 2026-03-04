import sqlite3
import time
from config import cfg

BUSY_SLEEP_SECONDS = 0.3
BUSY_RETRIES = 10

class SqliteRepo:
    def __init__(self):
        self.conn = sqlite3.connect(
            cfg.DB_PATH,
            check_same_thread=False,
            timeout=30.0
        )
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=30000;")
        self.create_table()

    def create_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                score INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add(self, id, title, url, score):
        for _ in range(BUSY_RETRIES):
            try:
                self.conn.execute(
                    "INSERT INTO jobs (id, title, url, score) VALUES (?, ?, ?, ?)",
                    (id, title, url, score)
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

    def get_recent_titles(self, limit=50):
        for _ in range(BUSY_RETRIES):
            try:
                cur = self.conn.execute(
                    "SELECT title FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                )
                return [row[0] for row in cur.fetchall()]
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                time.sleep(BUSY_SLEEP_SECONDS)
        raise sqlite3.OperationalError("database is locked (after retries)")
