Aquí tienes el archivo **`agent.md`** definitivo y optimizado. Está diseñado para que **Gemini CLI** (o Cursor/Windsurf) lo lea y construya todo el sistema de una sola vez.

Copia el siguiente bloque de código, guárdalo como `agent.md` en una carpeta vacía y ejecútalo con tu CLI.

```markdown
# AGENT: Fast-Cashflow Engine (MVP "Rata Cósmica")

## ROLE
You are a Senior Python Architect specializing in resilient, low-cost scraping and local AI integration.

## GOAL
Build a "Fast-Cashflow Engine" MVP that:
1.  **Scrapes** specific sources (Reddit r/forhire, r/freelance) using resilient HTTP strategies.
2.  **Extracts** structured data (Title, Price, Tech Stack) using **Ollama (Local AI)** when regex fails.
3.  **Scores** opportunities based on "Cashflow Potential" (Fast money vs. Long project).
4.  **Deduplicates** to avoid spam.
5.  **Alerts** via Telegram immediately for high-value targets.
6.  **Stores** history in SQLite.

## CONSTRAINTS
- **No paid APIs.** Use generic HTTP requests with rotation/backoff.
- **Local AI only.** Use `ollama` library or direct HTTP to localhost:11434.
- **Resiliency.** If scraping fails, wait and retry. If parsing fails, dump text to AI.
- **Target Stack.** Prioritize Python, .NET, C#, Scrapers, Automation.

## DIRECTORY STRUCTURE
Create the following structure:
```text
fast_cashflow_mvp/
├── .env.example
├── requirements.txt
├── main.py
├── config.py
├── src/
│   ├── __init__.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── http_client.py    # Resilient Fetcher
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── reddit_source.py  # Reddit Implementation
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── ollama_client.py  # Local LLM Interface
│   ├── validation/
│   │   ├── __init__.py
│   │   ├── scorer.py         # Logic & Rules
│   │   ├── deduplicator.py   # RapidFuzz logic
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── sqlite_repo.py    # DB Operations
│   ├── alerting/
│   │   ├── __init__.py
│   │   ├── telegram.py       # Notification Logic
│   └── utils/
│       ├── __init__.py
│       ├── logger.py

```

## FILES CONTENT

### 1. `requirements.txt`

```text
aiohttp==3.9.1
beautifulsoup4==4.12.2
lxml==5.1.0
python-dotenv==1.0.0
pydantic==2.5.3
dateparser==1.2.0
rapidfuzz==3.6.1
loguru==0.7.2
ollama==0.1.6

```

### 2. `.env.example`

```ini
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral
# Keywords separated by comma
TARGET_KEYWORDS=python,scraping,bot,automation,.net,c#,fix,urgent,bug

```

### 3. `config.py`

```python
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
    KEYWORDS = os.getenv("TARGET_KEYWORDS", "python,script").split(",")
    DB_PATH = "opportunities.db"
    
    # Thresholds
    MIN_SCORE_TO_ALERT = 3
    SIMILARITY_THRESHOLD = 85.0

cfg = Config()

```

### 4. `src/utils/logger.py`

```python
import sys
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
logger.add("runtime.log", rotation="1 day", retention="3 days")

```

### 5. `src/ingestion/http_client.py`

```python
import aiohttp
import asyncio
import random
from loguru import logger

class ResilientClient:
    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15',
        ]

    async def fetch(self, url: str) -> str:
        headers = {'User-Agent': random.choice(self.user_agents)}
        delay = random.uniform(1, 3)
        await asyncio.sleep(delay)
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    elif resp.status == 429:
                        logger.warning(f"Rate limited on {url}. Waiting...")
                        await asyncio.sleep(10)
                        return None
                    else:
                        logger.error(f"Failed {url}: {resp.status}")
                        return None
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return None

```

### 6. `src/ai/ollama_client.py`

```python
import aiohttp
import json
from config import cfg
from loguru import logger

class OllamaBrain:
    async def extract_and_score(self, raw_text: str) -> dict:
        prompt = f"""
        Analyze this freelance job post. Extract data and score it.
        
        TEXT:
        {raw_text[:2000]}
        
        RULES:
        - "urgency": 1 if words like "asap", "urgent", "today", "fix", "broken" exist. Else 0.
        - "stack_match": 1 if Python, .NET, C#, Scraping, Bot, Automation mentioned. Else 0.
        - "price_quality": 1 if budget is $50-$1000 or "hourly". 0 if too low (<$10) or too complex (>$3000).
        
        Return ONLY valid JSON:
        {{
            "is_job": boolean,
            "title": "short title",
            "price_str": "extracted price",
            "tech_stack": ["list", "of", "tech"],
            "summary": "1 sentence summary",
            "ai_score": (urgency + stack_match + price_quality)
        }}
        """
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": cfg.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                }
                async with session.post(f"{cfg.OLLAMA_HOST}/api/generate", json=payload) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        return json.loads(res['response'])
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return {"ai_score": 0, "is_job": False}
        return {"ai_score": 0, "is_job": False}

```

### 7. `src/sources/reddit_source.py`

```python
import json
from bs4 import BeautifulSoup
from src.ingestion.http_client import ResilientClient

class RedditSource:
    def __init__(self):
        self.client = ResilientClient()
        # Using .json extension is a hack to get structured data easily from Reddit without API key
        # fallback to HTML scraping if this fails in V2
        self.urls = [
            "[https://www.reddit.com/r/forhire/new/.json?limit=10](https://www.reddit.com/r/forhire/new/.json?limit=10)",
            "[https://www.reddit.com/r/freelance_forhire/new/.json?limit=5](https://www.reddit.com/r/freelance_forhire/new/.json?limit=5)"
        ]

    async def get_opportunities(self):
        opps = []
        for url in self.urls:
            data = await self.client.fetch(url)
            if not data: continue
            
            try:
                # Try parsing as JSON first (Reddit trick)
                json_data = json.loads(data)
                posts = json_data['data']['children']
                for post in posts:
                    p = post['data']
                    opps.append({
                        "source": "reddit",
                        "id": p['id'],
                        "url": f"[https://reddit.com](https://reddit.com){p['permalink']}",
                        "raw_text": f"{p['title']} \n {p['selftext']}",
                        "posted_at": p['created_utc']
                    })
            except:
                pass # HTML fallback logic would go here
        return opps

```

### 8. `src/validation/scorer.py`

```python
from config import cfg

class Scorer:
    def hard_filter(self, text: str) -> int:
        """Fast regex-based scoring before AI"""
        score = 0
        text = text.lower()
        
        # Keyword matching
        for kw in cfg.KEYWORDS:
            if kw in text:
                score += 1
        
        # Negative filter
        if "hiring" not in text and "hiring" not in text: 
             # Rough check, Reddit usually uses [Hiring] tag
             if "[hiring]" in text: score += 2
        
        return score

```

### 9. `src/validation/deduplicator.py`

```python
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

```

### 10. `src/storage/sqlite_repo.py`

```python
import sqlite3
from config import cfg

class SqliteRepo:
    def __init__(self):
        self.conn = sqlite3.connect(cfg.DB_PATH, check_same_thread=False)
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
        try:
            self.conn.execute("INSERT INTO jobs (id, title, url, score) VALUES (?, ?, ?, ?)", 
                             (id, title, url, score))
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def get_recent_titles(self, limit=50):
        cur = self.conn.execute("SELECT title FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
        return [row[0] for row in cur.fetchall()]

```

### 11. `src/alerting/telegram.py`

```python
import aiohttp
from config import cfg
from loguru import logger

async def send_alert(data: dict):
    if not cfg.TELEGRAM_TOKEN:
        logger.warning("No Telegram Token configured")
        return

    msg = f"""
💰 **FAST CASHFLOW ALERT** 💰
Score: {data['total_score']}/5

📌 **{data['title']}**
💵 {data.get('price_str', 'N/A')}
🛠 {', '.join(data.get('tech_stack', []))}

🔗 {data['url']}
    """
    
    url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){cfg.TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        await session.post(url, json={
            "chat_id": cfg.TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        })

```

### 12. `main.py`

```python
import asyncio
import sys
from loguru import logger
from src.sources.reddit_source import RedditSource
from src.ai.ollama_client import OllamaBrain
from src.validation.scorer import Scorer
from src.validation.deduplicator import Deduplicator
from src.storage.sqlite_repo import SqliteRepo
from src.alerting.telegram import send_alert
from config import cfg

async def run_cycle():
    logger.info("Starting cycle...")
    
    # Init modules
    reddit = RedditSource()
    brain = OllamaBrain()
    scorer = Scorer()
    db = SqliteRepo()
    dedup = Deduplicator(db)

    # 1. Fetch
    raw_opps = await reddit.get_opportunities()
    logger.info(f"Fetched {len(raw_opps)} raw items")

    for opp in raw_opps:
        # 2. Fast Filter (Dedup + Keyword)
        if not dedup.is_new(opp['raw_text'].split('\n')[0]):
            logger.info(f"Skipping duplicate: {opp['id']}")
            continue

        base_score = scorer.hard_filter(opp['raw_text'])
        if base_score < 1:
            logger.info(f"Low base score: {opp['id']}")
            continue

        # 3. AI Analysis (Heavy lifting)
        logger.info(f"Analyzing with AI: {opp['id']}")
        ai_result = await brain.extract_and_score(opp['raw_text'])
        
        if not ai_result.get('is_job'):
            continue

        total_score = base_score + ai_result.get('ai_score', 0)
        
        # 4. Action
        if total_score >= cfg.MIN_SCORE_TO_ALERT:
            logger.success(f"HIGH VALUE FOUND: {total_score}")
            alert_data = {
                "title": ai_result.get('title', 'Unknown'),
                "url": opp['url'],
                "total_score": total_score,
                "price_str": ai_result.get('price_str'),
                "tech_stack": ai_result.get('tech_stack')
            }
            await send_alert(alert_data)
            db.add(opp['id'], alert_data['title'], opp['url'], total_score)
        else:
             logger.info(f"Score too low ({total_score}): {opp['id']}")

async def main():
    loop_mode = "--loop" in sys.argv
    while True:
        try:
            await run_cycle()
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
        
        if not loop_mode:
            break
        
        logger.info("Sleeping 5 minutes...")
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())

```

## INSTRUCTIONS TO EXECUTE

1. Create the directory `fast_cashflow_mvp` and all files listed above with their contents.
2. Initialize a git repository (optional).
3. Notify the user when files are created.

```

```