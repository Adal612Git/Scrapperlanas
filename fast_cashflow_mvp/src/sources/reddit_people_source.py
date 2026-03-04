import json
import urllib.parse
from src.ingestion.http_client import ResilientClient


class RedditPeopleSource:
    def __init__(self):
        self.client = ResilientClient()
        self.urls = [
            "https://www.reddit.com/r/r4r/new/.json?limit=50",
            "https://www.reddit.com/r/ForeverAloneDating/new/.json?limit=50",
            "https://www.reddit.com/r/MeetPeople/new/.json?limit=50",
            "https://www.reddit.com/r/MakeNewFriendsHere/new/.json?limit=50",
            "https://www.reddit.com/r/Needafriend/new/.json?limit=50",
        ]

        self.search_urls = []
        mexico_terms = ["mexico", "cdmx", "guadalajara", "monterrey"]
        for sub in ["r4r", "ForeverAloneDating", "MeetPeople", "MakeNewFriendsHere", "Needafriend"]:
            for term in mexico_terms:
                q = urllib.parse.quote(f"subreddit:{sub} {term}")
                self.search_urls.append(
                    f"https://www.reddit.com/search.json?q={q}&sort=new&limit=25"
                )

    def get_urls(self):
        return self.urls + self.search_urls

    async def get_opportunities(self):
        opps = []
        for url in self.get_urls():
            data = await self.client.fetch(url)
            if not data:
                continue

            try:
                json_data = json.loads(data)
                posts = json_data["data"]["children"]
                for post in posts:
                    p = post["data"]
                    title = p.get("title", "")
                    selftext = p.get("selftext", "")
                    flair = p.get("link_flair_text", "") or ""
                    opps.append(
                        {
                            "source": "reddit",
                            "id": p.get("id", ""),
                            "url": f"https://reddit.com{p.get('permalink', '')}",
                            "title": title,
                            "selftext": selftext,
                            "flair": flair,
                            "raw_text": f"{title}\n{selftext}",
                            "posted_at": p.get("created_utc", 0),
                        }
                    )
            except Exception:
                pass
        return opps
