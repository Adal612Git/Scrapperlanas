import asyncio
import sys
import hashlib
import json
from pathlib import Path
from src.utils.logger import logger
from src.utils.report import write_report
from src.sources.reddit_people_source import RedditPeopleSource
from src.validation.people_filter import PeoplePostFilter
from src.validation.people_scorer import PeopleScorer
from src.storage.hunter_memory import HunterMemory
from src.alerting.telegram import send_alert
from config import cfg


async def run_cycle():
    logger.info("Starting people cycle...")

    reddit = RedditPeopleSource()
    filter_cfg = {}
    cfg_path = Path("people_filters.json")
    if cfg_path.exists():
        try:
            filter_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            filter_cfg = {}
    post_filter = PeoplePostFilter(filter_cfg)
    scorer = PeopleScorer(filter_cfg)
    memory = HunterMemory(path="people_memory.db")

    raw_opps = await reddit.get_opportunities()
    logger.info(f"Fetched {len(raw_opps)} raw items")
    report_items = []

    for opp in raw_opps:
        post_id = opp.get("id", "")
        title = opp.get("title", "")
        selftext = opp.get("selftext", "")
        flair = opp.get("flair", "")
        url = opp.get("url", "")
        title_guess = title.strip() or opp.get("raw_text", "").split("\n")[0].strip()
        content_hash = hashlib.sha1(f"{title}\n{selftext}".encode("utf-8")).hexdigest()

        if not post_id:
            logger.info("Skipping post with missing id")
            report_items.append(
                {
                    "status": "trash",
                    "reason": "missing_id",
                    "title": title_guess,
                    "url": url,
                    "source": opp.get("source", ""),
                    "total_score": 0,
                }
            )
            continue

        if memory.has(post_id, url=url, content_hash=content_hash):
            logger.info(f"Skipping already seen: {post_id}")
            report_items.append(
                {
                    "status": "trash",
                    "reason": "seen_before",
                    "title": title_guess,
                    "url": url,
                    "source": opp.get("source", ""),
                    "total_score": 0,
                }
            )
            continue

        accept, reason = post_filter.should_accept(title, selftext, flair)
        if not accept:
            logger.info(f"Filtered out ({reason}): {post_id}")
            report_items.append(
                {
                    "status": "trash",
                    "reason": reason,
                    "title": title_guess,
                    "url": url,
                    "source": opp.get("source", ""),
                    "total_score": 0,
                }
            )
            continue

        memory.add(post_id, url=url, content_hash=content_hash)

        relevance_score = scorer.score(f"{title}\n{selftext}\n{flair}")
        if relevance_score <= 0:
            logger.info(f"Low relevance score: {post_id}")
            report_items.append(
                {
                    "status": "trash",
                    "reason": "low_relevance",
                    "title": title_guess,
                    "url": url,
                    "source": opp.get("source", ""),
                    "total_score": relevance_score,
                }
            )
            continue

        alert_data = {
            "title": title_guess,
            "url": url,
            "total_score": relevance_score,
            "price_str": "N/A",
            "tech_stack": [],
        }
        await send_alert(alert_data)
        report_items.append(
            {
                "status": "alert",
                "reason": "score_pass",
                "title": alert_data["title"],
                "summary": "",
                "price_str": "N/A",
                "tech_stack": [],
                "url": url,
                "source": opp.get("source", ""),
                "total_score": relevance_score,
            }
        )

    write_report(
        "report_people.html",
        report_items,
        {"sources": reddit.get_urls()},
    )


async def main():
    loop_mode = "--loop" in sys.argv
    while True:
        try:
            await run_cycle()
        except Exception as e:
            logger.error(f"Cycle failed: {e}")

        if not loop_mode:
            break

        logger.info(f"Sleeping {cfg.LOOP_SLEEP_SECONDS} seconds...")
        await asyncio.sleep(cfg.LOOP_SLEEP_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
