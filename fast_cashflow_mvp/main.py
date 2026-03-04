import asyncio
import sys
import hashlib
from src.utils.logger import logger, get_logger
from src.utils.report import write_report, write_csv_report
from src.sources.reddit_source import RedditSource
from src.ai.ollama_client import OllamaBrain
from src.validation.scorer import Scorer
from src.validation.deduplicator import Deduplicator
from src.validation.post_filter import PostFilter
from src.validation.bot_detector import BotDetector
from src.storage.sqlite_repo import SqliteRepo
from src.storage.hunter_memory import HunterMemory
from src.alerting.telegram import send_alert
from config import cfg

async def run_cycle():
    log = get_logger("INIT")
    log.info("=" * 80)
    log.info("🚀 Starting new Fast-Cashflow hunting cycle...")
    log.info("=" * 80)
    
    # Init modules
    log = get_logger("SETUP")
    log.info("🔧 Initializing modules...")
    reddit = RedditSource()
    brain = OllamaBrain()
    scorer = Scorer()
    db = SqliteRepo()
    dedup = Deduplicator(db)
    post_filter = PostFilter()
    bot_detector = BotDetector()
    memory = HunterMemory()
    log.info("✓ All modules ready")

    # 1. Fetch
    log = get_logger("SCRAPING")
    log.info(f"🌐 Fetching from {len(reddit.get_urls())} Reddit URLs...")
    raw_opps = await reddit.get_opportunities()
    log.info(f"✓ Fetched {len(raw_opps)} raw posts from Reddit")
    report_items = []
    
    # Statistics counters
    stats = {
        "total": len(raw_opps),
        "seen_before": 0,
        "bots_detected": 0,
        "filtered_intent": 0,
        "filtered_spam": 0,
        "duplicates": 0,
        "low_relevance": 0,
        "ai_rejected": 0,
        "high_value": 0
    }

    log = get_logger("FILTERING")
    log.info(f"🔍 Starting multi-stage filtering on {len(raw_opps)} posts...")
    log.info("")
    
    for idx, opp in enumerate(raw_opps, 1):
        post_id = opp.get("id", "")
        title = opp.get("title", "")
        selftext = opp.get("selftext", "")
        flair = opp.get("flair", "")
        url = opp.get("url", "")
        author = opp.get("author", "")
        title_guess = title.strip() or opp.get("raw_text", "").split("\n")[0].strip()
        content_hash = hashlib.sha1(f"{title}\n{selftext}".encode("utf-8")).hexdigest()

        log = get_logger(f"POST-{idx}/{len(raw_opps)}")
        log.debug(f"📝 Processing: {title_guess[:60]}...")

        if not post_id:
            log.warning(f"❌ Missing ID - skipping")
            stats["filtered_spam"] += 1
            report_items.append({
                "status": "trash",
                "reason": "missing_id",
                "title": title_guess,
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": 0
            })
            continue

        # 2. Memory check (skip if already seen)
        if memory.has(post_id, url=url, content_hash=content_hash):
            log.debug(f"💤 Already seen - skipping")
            stats["seen_before"] += 1
            report_items.append({
                "status": "trash",
                "reason": "seen_before",
                "title": title_guess,
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": 0
            })
            continue

        # 2.5. Bot detection (check username patterns)
        is_bot, bot_reason = bot_detector.is_bot(opp)
        if is_bot:
            log.debug(f"🤖 Bot detected: {bot_reason}")
            stats["bots_detected"] += 1
            report_items.append({
                "status": "trash",
                "reason": f"bot_{bot_reason}",
                "title": title_guess,
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": 0
            })
            continue

        # 3. Strict intent + quality filter
        accept, reason = post_filter.should_accept(title, selftext, flair)
        if not accept:
            log.debug(f"🚫 Rejected: {reason}")
            if "spam" in reason or "scam" in reason:
                stats["filtered_spam"] += 1
            else:
                stats["filtered_intent"] += 1
            report_items.append({
                "status": "trash",
                "reason": reason,
                "title": title_guess,
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": 0
            })
            continue

        # 3.5 Deep bot detection (only for posts that pass intent filters)
        author = opp.get("author", "")
        if author and author not in ["[deleted]", "AutoModerator", "[removed]"]:
            log.debug(f"🔍 Performing deep bot check for user: {author}")
            is_bot_deep, deep_reason = await bot_detector.is_bot_deep(author, reddit.client)
            if is_bot_deep:
                log.warning(f"🤖 Deep Bot Detected: {deep_reason}")
                stats["bots_detected"] += 1
                report_items.append({
                    "status": "trash",
                    "reason": f"bot_{deep_reason}",
                    "title": title_guess,
                    "url": opp.get("url", ""),
                    "author": author,
                    "source": opp.get("source", ""),
                    "total_score": 0
                })
                continue

        # Save immediately after passing filters
        memory.add(post_id, url=url, content_hash=content_hash)

        # 4. Fast Filter (Dedup + Relevance)
        if not dedup.is_new(title_guess):
            log.debug(f"♻️  Duplicate detected - skipping")
            stats["duplicates"] += 1
            report_items.append({
                "status": "trash",
                "reason": "duplicate",
                "title": title_guess,
                "url": opp.get("url", ""),
                "source": opp.get("source", ""),
                "total_score": 0
            })
            continue

        relevance_score = scorer.hard_filter(f"{title}\n{selftext}\n{flair}")
        log.debug(f"🎯 Relevance score: {relevance_score}")
        
        if relevance_score <= 0:
            log.debug(f"📉 Low relevance (score={relevance_score}) - skipping")
            stats["low_relevance"] += 1
            report_items.append({
                "status": "trash",
                "reason": "low_relevance",
                "title": title_guess,
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": relevance_score
            })
            continue

        # 5. AI Analysis (Heavy lifting)
        log.info(f"🧠 Analyzing with Local AI (Ollama)...")
        ai_result = await brain.extract_and_score(opp.get("raw_text", ""))
        
        if not ai_result.get('is_job'):
            log.debug(f"🤖 AI says: NOT a valid job post")
            stats["ai_rejected"] += 1
            report_items.append({
                "status": "trash",
                "reason": ai_result.get("error", "ai_rejected"),
                "title": ai_result.get("title", title_guess),
                "summary": ai_result.get("summary", ""),
                "price_str": ai_result.get("price_str", "N/A"),
                "tech_stack": ai_result.get("tech_stack", []),
                "playbook_hint": ai_result.get("playbook_hint", ""),
                "match_confidence": ai_result.get("match_confidence", 0),
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": relevance_score
            })
            continue

        ai_score = ai_result.get("ai_score")
        if ai_score is None:
            ai_score = 0
        total_score = relevance_score + ai_score
        
        # 6. Action (alert if relevance score > 0)
        if relevance_score > 0:
            log = get_logger("💎 OPPORTUNITY")
            log.success(f"🎉 HIGH VALUE FOUND! Score: {total_score}")
            log.success(f"   Title: {ai_result.get('title', 'Unknown')}")
            log.success(f"   Price: {ai_result.get('price_str', 'N/A')}")
            log.success(f"   Stack: {', '.join(ai_result.get('tech_stack', []))}")
            log.success(f"   URL: {opp.get('url', '')[:60]}...")
            stats["high_value"] += 1
            
            alert_data = {
                "title": ai_result.get('title', 'Unknown'),
                "url": opp.get('url', ''),
                "total_score": total_score,
                "price_str": ai_result.get('price_str'),
                "tech_stack": ai_result.get('tech_stack'),
                "playbook_hint": ai_result.get('playbook_hint'),
                "match_confidence": ai_result.get('match_confidence')
            }
            
            log = get_logger("ALERTING")
            log.info(f"📲 Sending Telegram alert...")
            await send_alert(alert_data)
            db.add(post_id, alert_data['title'], opp.get('url', ''), total_score)
            log.info(f"✓ Alert sent and saved to DB")
            report_items.append({
                "status": "alert",
                "reason": "score_pass",
                "title": alert_data["title"],
                "summary": ai_result.get("summary", ""),
                "price_str": alert_data.get("price_str", "N/A"),
                "tech_stack": alert_data.get("tech_stack", []),
                "playbook_hint": ai_result.get("playbook_hint", ""),
                "match_confidence": ai_result.get("match_confidence", 0),
                "url": opp.get("url", ""),
                "author": author,
                "source": opp.get("source", ""),
                "total_score": total_score
            })
        else:
            logger.info(f"Score too low ({total_score}): {post_id}")
            report_items.append({
                "status": "trash",
                "reason": "score_below_threshold",
                "title": ai_result.get("title", title_guess),
                "summary": ai_result.get("summary", ""),
                "price_str": ai_result.get("price_str", "N/A"),
                "tech_stack": ai_result.get("tech_stack", []),
                "url": opp.get("url", ""),
                "source": opp.get("source", ""),
                "total_score": total_score
            })

    # Generate report
    log = get_logger("REPORTING")
    log.info(f"📊 Generating HTML report...")
    write_report(
        "report.html",
        report_items,
        {"sources": reddit.get_urls()}
    )
    write_csv_report("report.csv", report_items)
    log.info(f"✓ Reports saved to report.html and report.csv")
    
    # Final summary
    log = get_logger("SUMMARY")
    log.info("=" * 80)
    log.info("📈 CYCLE COMPLETE - Statistics:")
    log.info(f"   Total posts processed: {stats['total']}")
    log.info(f"   ❌ Already seen: {stats['seen_before']}")
    log.info(f"   ❌ Bots detected: {stats['bots_detected']}")
    log.info(f"   ❌ Wrong intent: {stats['filtered_intent']}")
    log.info(f"   ❌ Spam/scam detected: {stats['filtered_spam']}")
    log.info(f"   ❌ Duplicates: {stats['duplicates']}")
    log.info(f"   ❌ Low relevance: {stats['low_relevance']}")
    log.info(f"   ❌ AI rejected: {stats['ai_rejected']}")
    log.info(f"   ✅ HIGH VALUE OPPORTUNITIES: {stats['high_value']}")
    log.info("=" * 80)

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
