"""Telethon userbot — monitors channels via polling + live events."""
import asyncio
import logging

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import Message, UpdateNewChannelMessage

from src.config import config
from src.database import get_session
from src.models import Channel, UserChannel
from src.services import metrics
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
from src.userbot.digests import (
    MAX_MESSAGE_CHARS,
    build_and_send_digest as _build_and_send_digest,
    digest_html as _digest_html,
    digest_loop as _digest_loop,
    send_daily_digest as _send_daily_digest,
    send_digest_now,
    split_message as _split_message,
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
from src.userbot.maintenance import (
    claim_report as _claim_report,
    cleanup_loop as _cleanup_loop,
    cleanup_old_posts as _cleanup_old_posts,
    heartbeat_loop as _heartbeat_loop,
    heartbeat_tick as _heartbeat_tick,
    mark_report_sent as _mark_report_sent,
    report_due as _report_due,
    report_loop as _report_loop,
    run_cleanup_once as _run_cleanup_once,
    subscription_notice_loop as _subscription_notice_loop,
    upsell_loop as _upsell_loop,
)
from src.userbot.subscribers import (
    get_eligible_subscriber_details as _get_eligible_subscriber_details,
    get_eligible_subscribers as _get_eligible_subscribers,
    user_lang as _user_lang,
)

logger = logging.getLogger(__name__)

__all__ = [
    "flush_buffer_on_shutdown",
    "refresh_channels",
    "send_digest_now",
    "start_userbot",
]

# Private aliases intentionally preserve imports used by tests and older
# integrations while __all__ documents the supported public runtime surface.
_COMPATIBILITY_EXPORTS = (
    _DELIVERY_CONCURRENCY,
    _MEDIA_CACHE_MAX,
    DELIVERY_GRACE_SECS,
    FLUSH_TICK_SECS,
    MAX_MESSAGE_CHARS,
    _absorb_album_sibling,
    _batch_buffer,
    _batch_flush_loop,
    _build_and_send_digest,
    _cache_file_id,
    _caption_fits,
    _caption_text_budget,
    _claim_report,
    _cleanup_loop,
    _cleanup_old_posts,
    _deliver_entry,
    _deliver_to_user,
    _delivery_sem,
    _digest_html,
    _digest_loop,
    _download_media_bytes,
    _entry_due,
    _flush_pending,
    _get_eligible_subscriber_details,
    _get_eligible_subscribers,
    _get_media_type,
    _heartbeat_loop,
    _heartbeat_tick,
    _in_flight,
    _is_file_error,
    _mark_report_sent,
    _media_file_ids,
    _media_filename,
    _media_size_ok,
    _process_message,
    _queue_pending,
    _report_due,
    _report_loop,
    _run_cleanup_once,
    _send_daily_digest,
    _send_media,
    _send_summary,
    _split_message,
    _subscription_notice_loop,
    _upsell_loop,
    _user_lang,
)

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
