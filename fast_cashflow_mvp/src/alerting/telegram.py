import aiohttp
from config import cfg
from loguru import logger


async def send_alert(data: dict):
    if not cfg.TELEGRAM_TOKEN:
        logger.warning("No Telegram Token configured")
        return
    if not cfg.TELEGRAM_CHAT_ID:
        logger.warning("No Telegram Chat ID configured")
        return

    msg = (
        "💎 <b>FAST CASHFLOW ALERT</b>\n"
        f"🏆 Score: {data['total_score']}\n"
        f"🎯 Confidence: {data.get('match_confidence', 0)}%\n\n"
        f"📌 <b>{data['title']}</b>\n"
        f"💰 Price: {data.get('price_str', 'N/A')}\n"
        f"🛠️ Stack: {', '.join(data.get('tech_stack', []))}\n\n"
        f"💡 <i>Playbook: {data.get('playbook_hint', 'N/A')}</i>\n\n"
        f"🔗 {data['url']}"
    )

    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        await session.post(url, json={
            "chat_id": cfg.TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        })

