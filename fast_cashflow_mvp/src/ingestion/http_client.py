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
