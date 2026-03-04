import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
    
    # Bilingual keywords for job matching
    KEYWORDS_EN = [
        kw.strip().lower()
        for kw in os.getenv("TARGET_KEYWORDS_EN", "python,scraping,bot,automation,.net,c#,backend,api,n8n,agent,llm,workflow").split(",")
        if kw.strip()
    ]
    KEYWORDS_ES = [
        kw.strip().lower()
        for kw in os.getenv("TARGET_KEYWORDS_ES", "python,scraping,automatización,bot,desarrollo,programador,backend,api,integración,flujo").split(",")
        if kw.strip()
    ]
    # Combined keywords for searching
    KEYWORDS = list(set(KEYWORDS_EN + KEYWORDS_ES))
    
    # Geographic keywords for Mexico/LATAM
    GEO_KEYWORDS = [
        kw.strip().lower()
        for kw in os.getenv("GEO_KEYWORDS", "mexico,méxico,cdmx,guadalajara,monterrey,latam,latinoamérica,remoto,remote").split(",")
        if kw.strip()
    ]
    
    DB_PATH = "opportunities.db"
    LOOP_SLEEP_SECONDS = int(os.getenv("LOOP_SLEEP_SECONDS", "180"))
    
    # Thresholds
    MIN_SCORE_TO_ALERT = 3
    SIMILARITY_THRESHOLD = 85.0

cfg = Config()
