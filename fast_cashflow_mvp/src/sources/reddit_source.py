import json
import urllib.parse
from config import cfg
from src.ingestion.http_client import ResilientClient

class RedditSource:
    def __init__(self):
        self.client = ResilientClient()
        # Using .json extension is a hack to get structured data easily from Reddit without API key
        # fallback to HTML scraping if this fails in V2
        self.urls = [
            # English subreddits
            "https://www.reddit.com/r/forhire/new/.json?limit=50",
            "https://www.reddit.com/r/freelance_forhire/new/.json?limit=30",
            "https://www.reddit.com/r/slavelabour/new/.json?limit=30",
            "https://www.reddit.com/r/remotework/new/.json?limit=20",
            "https://www.reddit.com/r/digitalnomad/search.json?q=hiring&restrict_sr=on&sort=new&limit=20",
            
            # Spanish-language subreddits (if they exist and are active)
            "https://www.reddit.com/r/empleos_AR/new/.json?limit=20",  # Argentina
            "https://www.reddit.com/r/mexico/search.json?q=trabajo+remoto&restrict_sr=on&sort=new&limit=10",
        ]

        # Build search URLs for bilingual keyword searches
        self.search_urls = []
        
        # English keywords in English subreddits
        for kw in cfg.KEYWORDS_EN[:6]:  # Limit to top 6 to avoid too many requests
            q = urllib.parse.quote(f"subreddit:forhire [Hiring] {kw}")
            self.search_urls.append(f"https://www.reddit.com/search.json?q={q}&sort=new&limit=10")
        
        # Spanish keywords in general Reddit and Spanish subreddits
        for kw in cfg.KEYWORDS_ES[:4]:
            q = urllib.parse.quote(f"[Hiring] {kw}")
            self.search_urls.append(f"https://www.reddit.com/search.json?q={q}&sort=new&limit=10")
        
        # Geographic searches for Mexico/LATAM
        for geo in cfg.GEO_KEYWORDS[:3]:
            q = urllib.parse.quote(f"hiring {geo} remote")
            self.search_urls.append(f"https://www.reddit.com/search.json?q={q}&sort=new&limit=10")

    def get_urls(self):
        return self.urls + self.search_urls

    async def get_opportunities(self):
        opps = []
        for url in self.get_urls():
            data = await self.client.fetch(url)
            if not data: continue
            
            try:
                # Try parsing as JSON first (Reddit trick)
                json_data = json.loads(data)
                posts = json_data['data']['children']
                for post in posts:
                    p = post['data']
                    title = p.get('title', '')
                    selftext = p.get('selftext', '')
                    flair = p.get('link_flair_text', '') or ''
                    opps.append({
                        "source": "reddit",
                        "id": p.get('id', ''),
                        "url": f"https://reddit.com{p.get('permalink', '')}",
                        "title": title,
                        "selftext": selftext,
                        "flair": flair,
                        "author": p.get('author', ''),
                        "raw_text": f"{title}\n{selftext}",
                        "posted_at": p.get('created_utc', 0)
                    })
            except:
                pass # HTML fallback logic would go here
        return opps
