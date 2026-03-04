import aiohttp
import json
import re
from config import cfg
from loguru import logger

def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = text[start:end + 1]
    candidate = candidate.replace("“", "\"").replace("”", "\"").replace("’", "'")
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(candidate)
    except Exception:
        return None

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
            "playbook_hint": "How to respond effectively (max 15 words)",
            "match_confidence": 0-100,
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
                        parsed = _extract_json(res.get("response", ""))
                        if parsed:
                            # Ensure defaults
                            parsed.setdefault("playbook_hint", "Be professional and mention similar work.")
                            parsed.setdefault("match_confidence", 50)
                            return parsed
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return {"ai_score": 0, "is_job": False, "error": "ai_call_failed"}

        # Retry with stricter prompt if JSON is malformed
        retry_prompt = f"""
        Return ONLY valid minified JSON. No extra text.
        {{
          "is_job": boolean,
          "title": "string",
          "price_str": "string",
          "tech_stack": [],
          "summary": "string",
          "playbook_hint": "string",
          "match_confidence": number,
          "ai_score": number
        }}
        TEXT:
        {raw_text[:1500]}
        """
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": cfg.OLLAMA_MODEL,
                    "prompt": retry_prompt,
                    "stream": False,
                    "format": "json"
                }
                async with session.post(f"{cfg.OLLAMA_HOST}/api/generate", json=payload) as resp:
                    if resp.status == 200:
                        res = await resp.json()
                        parsed = _extract_json(res.get("response", ""))
                        if parsed:
                            return parsed
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            return {"ai_score": 0, "is_job": False, "error": "ai_call_failed"}
        return {"ai_score": 0, "is_job": False, "error": "ai_json_parse"}
