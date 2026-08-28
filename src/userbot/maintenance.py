"""Operational heartbeat, reports, notices, offers, and data cleanup."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.config import config
from src.database import get_session
from src.models import BotEvent, BotHealth, PendingPost, Post
from src.services import metrics

logger = logging.getLogger(__name__)

__all__ = [
    "claim_report",
    "cleanup_loop",
    "cleanup_old_posts",
    "heartbeat_loop",
    "heartbeat_tick",
    "mark_report_sent",
    "report_due",
    "report_loop",
    "run_cleanup_once",
    "subscription_notice_loop",
    "upsell_loop",
]


def heartbeat_tick() -> None:
    """Blocking: drain the metrics buffer, then stamp liveness. Never raises."""
    metrics.flush()
    metrics.heartbeat()


async def heartbeat_loop() -> None:
    """Stamp liveness every 5 min — this is what the external watchdog reads.

    The body is guarded because nothing awaits this task: an escaped exception
    would kill it silently, freezing last_seen_at and making the watchdog
    report a perfectly healthy bot as dead — forever.
    """
    while True:
        try:
            await asyncio.to_thread(heartbeat_tick)
        except Exception as exc:
            logger.warning("Heartbeat tick failed: %s", exc)
        await asyncio.sleep(300)


def report_due(db) -> bool:
    """True if today's report hasn't been sent and the report hour has passed.

    Stored per-date so a restart can still deliver a report the process
    missed while it was down.
    """
    now = datetime.now(timezone.utc)
    if now.hour < config.ADMIN_REPORT_HOUR_UTC:
        return False
    row = db.query(BotHealth).filter_by(id=1).first()
    today = now.strftime("%Y-%m-%d")
    return not row or row.last_report_on != today


def mark_report_sent(db) -> None:
    row = db.query(BotHealth).filter_by(id=1).first()
    if row is None:
        row = BotHealth(id=1)
        db.add(row)
    row.last_report_on = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.commit()


def claim_report(db) -> bool:
    """Mark today's report as sent, up front. Returns False if the write failed.

    Claiming BEFORE sending is deliberate. If the mark fails after a successful
    send, the loop would re-send the full report every 10 minutes for the rest
    of the day — dozens of duplicates to every admin. Losing one report to a
    failed send is the better direction, and /report fetches it on demand.
    """
    try:
        mark_report_sent(db)
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Could not claim daily report: %s", exc)
        return False


async def report_loop() -> None:
    """Send the daily admin report once per day, catching up after downtime."""
    from src.services.report import send_report_to_admins
    while True:
        db = get_session()
        try:
            claimed = report_due(db) and claim_report(db)
        except Exception as exc:
            claimed = False
            logger.debug("Report due-check failed: %s", exc)
        finally:
            db.close()

        if claimed:
            try:
                # Drain buffered events first so the report counts today's work.
                await asyncio.to_thread(metrics.flush)
                if await send_report_to_admins():
                    logger.info("Daily admin report sent")
            except Exception as exc:
                logger.warning("Daily report failed: %s", exc)

        await asyncio.sleep(600)  # re-check every 10 min


async def subscription_notice_loop() -> None:
    """Warn users before access expires and confirm when it has expired."""
    from src.services.subscription_notifier import send_due_notifications

    while True:
        try:
            sent = await send_due_notifications()
            if sent:
                logger.info("Sent %d subscription expiry notification(s)", sent)
        except Exception as exc:
            logger.warning("Subscription notification check failed: %s", exc)
        await asyncio.sleep(1800)


async def upsell_loop() -> None:
    """Send bounded paid-feature campaigns to eligible Free users hourly."""
    from src.services.upsell import send_due_upsells

    while True:
        try:
            sent = await send_due_upsells()
            if sent:
                logger.info("Sent %d paid-feature offer(s)", sent)
        except Exception as exc:
            logger.warning("Upsell pass failed: %s", exc)
        await asyncio.sleep(3600)


def cleanup_old_posts(db) -> int:
    """Purge posts older than POST_RETENTION_DAYS from the DB.

    Chat messages already sent to users are untouched — only DB rows go.
    The polling cursor lives on Channel.last_message_id, so deleting posts
    never causes old posts to be re-fetched or re-delivered.

    Posts still referenced by pending_posts (an undelivered burst that a
    restart persisted) are NEVER deleted — so cleanup can't race the startup
    flush or destroy work that hasn't reached the user yet.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.POST_RETENTION_DAYS)

    # Ops events age out on the same schedule — they only feed the daily report.
    # This runs before the posts pass and outside its early return: bot_events
    # grows on every delivery and AI call, so it must be trimmed even on days
    # when no post is old enough to expire.
    db.query(BotEvent).filter(BotEvent.created_at < cutoff).delete(
        synchronize_session=False
    )

    undelivered = db.query(PendingPost.post_id)
    old_ids = [
        row[0] for row in db.query(Post.id)
        .filter(Post.created_at < cutoff, Post.id.notin_(undelivered))
        .all()
    ]
    if not old_ids:
        db.commit()
        return 0
    from src.models import Bookmark
    deleted = 0
    for i in range(0, len(old_ids), 500):
        chunk = old_ids[i:i + 500]
        db.query(Bookmark).filter(Bookmark.post_id.in_(chunk)).delete(
            synchronize_session=False
        )
        deleted += db.query(Post).filter(Post.id.in_(chunk)).delete(
            synchronize_session=False
        )
    db.commit()
    return deleted


def run_cleanup_once() -> None:
    """Blocking DB purge — run via asyncio.to_thread to keep the loop free."""
    db = get_session()
    try:
        n = cleanup_old_posts(db)
        if n:
            logger.info("Cleanup: purged %d post(s) older than %d day(s)",
                        n, config.POST_RETENTION_DAYS)
    except Exception as exc:
        db.rollback()
        logger.warning("Post cleanup failed: %s", exc)
    finally:
        db.close()


async def cleanup_loop() -> None:
    """Daily: purge old posts from the DB (off the event loop thread)."""
    while True:
        await asyncio.sleep(24 * 3600)
        await asyncio.to_thread(run_cleanup_once)
