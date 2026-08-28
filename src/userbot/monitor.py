"""Telethon userbot — monitors channels via polling + live events."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import Message, UpdateNewChannelMessage

from src.bot.i18n import lang_of, t
from src.config import config
from src.database import get_session
from src.models import BotEvent, BotHealth, Channel, PendingPost, Post, User, UserChannel
from src.services import metrics
from src.services.summarizer import build_digest
from src.userbot.delivery import (
    BATCH_BUFFER as _batch_buffer,
    DELIVERY_CONCURRENCY as _DELIVERY_CONCURRENCY,
    DELIVERY_GRACE_SECS,
    DELIVERY_SEMAPHORE as _delivery_sem,
    FLUSH_TICK_SECS,
    IN_FLIGHT as _in_flight,
    absorb_album_sibling as _absorb_album_sibling,
    batch_flush_loop as _batch_flush_loop,
    deliver_entry as _deliver_entry,
    deliver_to_user as _deliver_to_user,
    entry_due as _entry_due,
    flush_buffer_on_shutdown,
    flush_pending as _flush_pending,
    process_message as _process_message,
    queue_pending as _queue_pending,
    send_summary as _send_summary,
)
from src.userbot.media import (
    MEDIA_CACHE_MAX as _MEDIA_CACHE_MAX,
    MEDIA_FILE_IDS as _media_file_ids,
    cache_file_id as _cache_file_id,
    caption_fits as _caption_fits,
    caption_text_budget as _caption_text_budget,
    download_media_bytes as _download_media_bytes,
    get_media_type as _get_media_type,
    is_file_error as _is_file_error,
    media_filename as _media_filename,
    media_size_ok as _media_size_ok,
    send_media as _send_media,
)
from src.userbot.subscribers import (
    get_eligible_subscriber_details as _get_eligible_subscriber_details,
    get_eligible_subscribers as _get_eligible_subscribers,
    user_lang as _user_lang,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_get_eligible_subscriber_details",
    "_get_eligible_subscribers",
    "_absorb_album_sibling",
    "_batch_buffer",
    "_batch_flush_loop",
    "_cache_file_id",
    "_caption_fits",
    "_caption_text_budget",
    "_download_media_bytes",
    "_deliver_entry",
    "_deliver_to_user",
    "_DELIVERY_CONCURRENCY",
    "_delivery_sem",
    "_entry_due",
    "_flush_pending",
    "_get_media_type",
    "_is_file_error",
    "_in_flight",
    "_MEDIA_CACHE_MAX",
    "_media_file_ids",
    "_media_filename",
    "_media_size_ok",
    "_process_message",
    "_queue_pending",
    "_send_media",
    "_send_summary",
    "_user_lang",
    "DELIVERY_GRACE_SECS",
    "FLUSH_TICK_SECS",
    "flush_buffer_on_shutdown",
]

_client: TelegramClient | None = None

# Polling pace. One GetHistory request per channel per cycle, SPREAD across the
# cycle instead of fired back-to-back: a burst of N history requests every 30s
# from one account is exactly what Telegram flood-limits, and Telethon then
# silently sleeps inside get_messages — observed in prod as an effective poll
# period of ~27 MINUTES instead of 30 seconds.
POLL_INTERVAL = 60
# Per-channel cap per cycle. Catch-up is paginated oldest-first, so a bigger
# backlog is NOT lost — the cursor stops at the last processed message and the
# next cycle continues from there.
POLL_MAX_CATCHUP = 60


def _heartbeat_tick() -> None:
    """Blocking: drain the metrics buffer, then stamp liveness. Never raises."""
    metrics.flush()
    metrics.heartbeat()


async def _heartbeat_loop() -> None:
    """Stamp liveness every 5 min — this is what the external watchdog reads.

    The body is guarded because nothing awaits this task: an escaped exception
    would kill it silently, freezing last_seen_at and making the watchdog
    report a perfectly healthy bot as dead — forever.
    """
    while True:
        try:
            await asyncio.to_thread(_heartbeat_tick)
        except Exception as exc:
            logger.warning("Heartbeat tick failed: %s", exc)
        await asyncio.sleep(300)


def _report_due(db) -> bool:
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


def _mark_report_sent(db) -> None:
    row = db.query(BotHealth).filter_by(id=1).first()
    if row is None:
        row = BotHealth(id=1)
        db.add(row)
    row.last_report_on = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    db.commit()


def _claim_report(db) -> bool:
    """Mark today's report as sent, up front. Returns False if the write failed.

    Claiming BEFORE sending is deliberate. If the mark fails after a successful
    send, the loop would re-send the full report every 10 minutes for the rest
    of the day — dozens of duplicates to every admin. Losing one report to a
    failed send is the better direction, and /report fetches it on demand.
    """
    try:
        _mark_report_sent(db)
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Could not claim daily report: %s", exc)
        return False


async def _report_loop() -> None:
    """Send the daily admin report once per day, catching up after downtime."""
    from src.services.report import send_report_to_admins
    while True:
        db = get_session()
        try:
            claimed = _report_due(db) and _claim_report(db)
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


async def _subscription_notice_loop() -> None:
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


async def _upsell_loop() -> None:
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


def _cleanup_old_posts(db) -> int:
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


def _run_cleanup_once() -> None:
    """Blocking DB purge — run via asyncio.to_thread to keep the loop free."""
    db = get_session()
    try:
        n = _cleanup_old_posts(db)
        if n:
            logger.info("Cleanup: purged %d post(s) older than %d day(s)",
                        n, config.POST_RETENTION_DAYS)
    except Exception as exc:
        db.rollback()
        logger.warning("Post cleanup failed: %s", exc)
    finally:
        db.close()


async def _cleanup_loop() -> None:
    """Daily: purge old posts from the DB (off the event loop thread)."""
    while True:
        await asyncio.sleep(24 * 3600)
        await asyncio.to_thread(_run_cleanup_once)


MAX_MESSAGE_CHARS = 3900  # Telegram limit is 4096; keep headroom for tags


def _split_message(text: str) -> list[str]:
    """Split a long message into Telegram-sized chunks on paragraph boundaries."""
    if len(text) <= MAX_MESSAGE_CHARS:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        while len(para) > MAX_MESSAGE_CHARS:  # a single oversized paragraph
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:MAX_MESSAGE_CHARS])
            para = para[MAX_MESSAGE_CHARS:]
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > MAX_MESSAGE_CHARS:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _digest_html(ai_text: str) -> str:
    """Escape AI output for HTML parse_mode and bold the source headers."""
    lines = []
    for line in ai_text.splitlines():
        esc = html.escape(line)
        if esc.startswith("📢"):
            esc = f"<b>{esc}</b>"
        lines.append(esc)
    return "\n".join(lines)


async def _build_and_send_digest(
    telegram_id: int, db, channel_ids: list[int] | None = None
) -> bool:
    """AI-generated digest grouped by source. Returns True if sent.

    channel_ids: user-picked sources; None = all active channels (daily digest).
    """
    from src.bot.app import ptb_app
    if ptb_app is None:
        return False

    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    if not user or not user.can_auto_summary:
        return False
    lang = lang_of(user)

    ucs = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).all()
    if channel_ids is not None:
        wanted = set(channel_ids)
        ucs = [uc for uc in ucs if uc.channel_id in wanted]
    if not ucs:
        return False

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    sections: list[tuple[str, list[str]]] = []
    for uc in ucs:
        posts = (
            db.query(Post)
            .filter(
                Post.channel_id == uc.channel_id,
                Post.created_at >= since,
                Post.text.isnot(None),
                Post.text != "",
            )
            .order_by(Post.created_at.desc())
            .limit(8)
            .all()
        )
        if posts:
            sections.append((uc.channel.username, [p.text for p in posts]))
    if not sections:
        return False

    ai_text = await asyncio.to_thread(build_digest, sections, lang)

    date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    full = t("digest_header", lang, date=date_str) + "\n\n" + _digest_html(ai_text)
    if ptb_app.bot.username:  # viral share signature
        full += "\n\n" + t("digest_footer", lang, bot=ptb_app.bot.username)
    for chunk in _split_message(full):
        await ptb_app.bot.send_message(
            chat_id=telegram_id,
            text=chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    return True


async def send_digest_now(telegram_id: int, channel_ids: list[int] | None = None) -> bool:
    """Send digest on demand for a specific user. Returns True on success."""
    db = get_session()
    try:
        return await _build_and_send_digest(telegram_id, db, channel_ids)
    except Exception as exc:
        logger.warning("On-demand digest failed for user %s: %s", telegram_id, exc)
        return False
    finally:
        db.close()


async def _send_daily_digest() -> None:
    """Send daily digest to Pro users who enabled it."""
    db = get_session()
    try:
        users = db.query(User).filter_by(digest_enabled=True).all()
        tg_ids = [u.telegram_id for u in users if u.can_auto_summary]
    finally:
        db.close()

    for tg_id in tg_ids:
        db = get_session()
        try:
            sent = await _build_and_send_digest(tg_id, db)
            if sent:
                logger.info("Daily digest sent to user %s", tg_id)
        except Exception as exc:
            logger.warning("Daily digest failed for user %s: %s", tg_id, exc)
        finally:
            db.close()


async def _digest_loop() -> None:
    """Fire daily digest at DIGEST_HOUR_UTC every day."""
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=config.DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_secs = (target - now).total_seconds()
        logger.info("Next digest in %.0f minutes", sleep_secs / 60)
        await asyncio.sleep(sleep_secs)
        await _send_daily_digest()


async def _poll_one_channel(
    client: TelegramClient, ch_id: int, tg_channel_id: int,
    username: str | None, title: str | None, min_id: int,
) -> None:
    if min_id > 0:
        # Oldest-first pagination: EVERYTHING newer than the cursor, capped per
        # cycle. The old get_messages(limit=20, min_id) returned the NEWEST 20
        # and the cursor then jumped past the rest — any backlog beyond 20
        # (routine after a restart gap) was silently lost forever.
        messages = [
            m async for m in client.iter_messages(
                tg_channel_id, min_id=min_id, reverse=True, limit=POLL_MAX_CATCHUP,
            )
        ]
    else:
        # Freshly added channel (no cursor yet): backfill the newest 20 only —
        # oldest-first from id 0 would replay the channel's entire history.
        recent = await client.get_messages(tg_channel_id, limit=20)
        messages = list(reversed(recent))

    if not messages:
        return
    logger.info("Poll @%s: %d new message(s)", username, len(messages))
    ch_obj = Channel(id=ch_id, telegram_id=tg_channel_id, username=username, title=title)
    for msg in messages:
        await _process_message(client, ch_obj, msg)


async def _poll_channels(client: TelegramClient) -> None:
    while True:
        db = get_session()
        try:
            from src.services.blocklist import blocked_usernames
            blocked = blocked_usernames(db)
            channels = (
                db.query(Channel)
                .join(UserChannel)
                .filter(
                    Channel.telegram_id.isnot(None),
                    UserChannel.is_active.is_(True),
                )
                .distinct()
                .all()
            )
            channel_list = [
                (ch.id, ch.telegram_id, ch.username, ch.title, ch.last_message_id or 0)
                for ch in channels
                if (ch.username or "").lower() not in blocked
            ]
        finally:
            db.close()

        if not channel_list:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # Spacing between channels sums to one POLL_INTERVAL per full cycle.
        spacing = POLL_INTERVAL / len(channel_list)
        for ch_id, tg_channel_id, username, title, min_id in channel_list:
            try:
                await _poll_one_channel(client, ch_id, tg_channel_id, username, title, min_id)
            except FloodWaitError as exc:
                # Made visible on purpose (flood_sleep_threshold is lowered at
                # startup): silent in-library sleeps were how a 30s poll turned
                # into a 27-minute one with nothing in the logs.
                wait = min(exc.seconds, 300)
                logger.warning("Flood wait %ss polling @%s — backing off", exc.seconds, username)
                await asyncio.sleep(wait)
            except Exception as exc:
                logger.warning("Poll failed for @%s: %s", username, exc)
            await asyncio.sleep(spacing)


def _register_live_handler(client: TelegramClient) -> None:
    @client.on(events.Raw(UpdateNewChannelMessage))
    async def on_channel_message(update: UpdateNewChannelMessage):
        msg = update.message
        if not isinstance(msg, Message):
            return
        channel_id = getattr(msg.peer_id, "channel_id", None)
        if channel_id is None:
            return

        db = get_session()
        try:
            channel = db.query(Channel).filter_by(telegram_id=channel_id).first()
        finally:
            db.close()

        if not channel:
            logger.debug("Live: ignored channel telegram_id=%s", channel_id)
            return

        logger.info("Live event from @%s msg_id=%s", channel.username, msg.id)
        await _process_message(client, channel, msg)


async def _resolve_channels(client: TelegramClient) -> None:
    db = get_session()
    try:
        channels = db.query(Channel).all()
        for ch in channels:
            try:
                entity = await client.get_entity(f"@{ch.username}")
                ch.telegram_id = entity.id
                ch.title = getattr(entity, "title", ch.username)
            except Exception as exc:
                logger.warning("Cannot resolve @%s: %s", ch.username, exc)
                continue
        db.commit()
    finally:
        db.close()


async def start_userbot() -> TelegramClient:
    global _client

    if config.SESSION_STRING:
        from telethon.sessions import StringSession
        client = TelegramClient(
            StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH
        )
        await client.start()
        logger.info("Userbot started from SESSION_STRING")
    else:
        client = TelegramClient(config.SESSION_PATH, config.API_ID, config.API_HASH)
        await client.start(phone=config.PHONE)

    # Short flood waits are slept through; anything longer RAISES so the poll
    # loop can log and back off. Telethon's default (60s) swallowed every wait
    # silently — polling degraded to ~27-minute cycles with clean-looking logs.
    client.flood_sleep_threshold = 10

    _register_live_handler(client)
    await _resolve_channels(client)

    loop = asyncio.get_event_loop()

    await asyncio.to_thread(metrics.heartbeat, True)
    await asyncio.to_thread(metrics.record, metrics.STARTED)

    async def _startup_maintenance() -> None:
        # Order matters: deliver restart-persisted batches first, THEN purge —
        # cleanup skips pending-referenced posts anyway, but flushing first
        # gets the user their posts promptly and empties the queue.
        try:
            await _flush_pending(client)
        except Exception as exc:
            logger.warning("Startup flush failed: %s", exc)
        await asyncio.to_thread(_run_cleanup_once)

    loop.create_task(_poll_channels(client))
    loop.create_task(_digest_loop())
    loop.create_task(_batch_flush_loop(client))
    loop.create_task(_startup_maintenance())
    loop.create_task(_cleanup_loop())
    loop.create_task(_heartbeat_loop())
    loop.create_task(_report_loop())
    loop.create_task(_subscription_notice_loop())
    loop.create_task(_upsell_loop())

    _client = client
    db = get_session()
    try:
        n = db.query(Channel).count()
    finally:
        db.close()
    logger.info("Userbot started — watching %d channel(s), polling every %ds", n, POLL_INTERVAL)
    return client


async def refresh_channels() -> None:
    if _client is not None:
        await _resolve_channels(_client)
