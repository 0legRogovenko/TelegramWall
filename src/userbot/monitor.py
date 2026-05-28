"""Telethon userbot — monitors channels via polling + live events."""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import (
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    UpdateNewChannelMessage,
)

from src.config import config
from src.database import get_session
from src.models import Channel, Post, User, UserChannel
from src.services.summarizer import summarize

logger = logging.getLogger(__name__)

_client: TelegramClient | None = None
POLL_INTERVAL = 30
BATCH_WINDOW_SECS = 90

# (tg_id, channel_id) → {"posts": [...], "label": str, "first_at": float}
_batch_buffer: dict[tuple[int, int], dict] = {}


def _get_media_type(message) -> str | None:
    if message.photo or isinstance(message.media, MessageMediaPhoto):
        return "photo"
    if message.video:
        return "video"
    if message.audio:
        return "audio"
    if isinstance(message.media, MessageMediaDocument):
        return "document"
    return None


def _get_eligible_subscribers(db, channel_id: int, text: str) -> list[tuple[int, str | None]]:
    """Return (telegram_id, ai_filter) for subscribers who pass quiet/keyword checks."""
    now_hour = datetime.now(timezone.utc).hour
    ucs = db.query(UserChannel).filter_by(channel_id=channel_id, is_active=True).all()
    result = []
    for uc in ucs:
        user = uc.user

        # Quiet hours
        if user.quiet_start is not None and user.quiet_end is not None:
            qs, qe = user.quiet_start, user.quiet_end
            in_quiet = (qs <= now_hour < qe) if qs <= qe else (now_hour >= qs or now_hour < qe)
            if in_quiet:
                logger.debug("Skipping user %s (quiet hours)", user.telegram_id)
                continue

        # Keyword filter
        kws = uc.keyword_list
        if kws and not any(kw in (text or "").lower() for kw in kws):
            logger.debug("Skipping user %s (keyword filter)", user.telegram_id)
            continue

        result.append((user.telegram_id, uc.ai_filter))

    logger.debug("Eligible subscribers for channel %s: %s", channel_id, [r[0] for r in result])
    return result


def _should_auto_summary(db, telegram_id: int) -> bool:
    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    return bool(user and user.auto_summary and user.can_auto_summary)


async def _process_message(client: TelegramClient, channel: Channel, msg) -> None:
    if not isinstance(msg, Message):
        return

    db = get_session()
    try:
        existing = db.query(Post).filter_by(channel_id=channel.id, message_id=msg.id).first()
        if existing:
            return

        text = msg.message or ""
        media_type = _get_media_type(msg)
        channel_label = channel.title or f"@{channel.username}"
        subscriber_ids = _get_eligible_subscribers(db, channel.id, text)

        post = Post(channel_id=channel.id, message_id=msg.id, text=text, media_type=media_type)
        db.add(post)
        db.flush()
        post_id = post.id
        db.commit()
        db.close()

    except Exception as exc:
        logger.exception("Error saving post msg_id=%s: %s", msg.id, exc)
        db.rollback()
        db.close()
        return

    if not subscriber_ids:
        return

    logger.info("New post #%s from @%s → %d candidate(s)",
                post_id, channel.username, len(subscriber_ids))

    for tg_id, ai_filter in subscriber_ids:
        # AI filter check (async, non-blocking)
        if ai_filter and text:
            try:
                from src.services.summarizer import is_relevant
                relevant = await asyncio.to_thread(is_relevant, text, ai_filter)
                if not relevant:
                    logger.debug("AI filter skipped post #%s for user %s", post_id, tg_id)
                    continue
            except Exception as exc:
                logger.debug("AI filter error for user %s: %s — delivering anyway", tg_id, exc)

        # Buffer for batch delivery
        key = (tg_id, channel.id)
        if key not in _batch_buffer:
            _batch_buffer[key] = {
                "posts": [],
                "label": channel_label,
                "first_at": time.monotonic(),
            }
        _batch_buffer[key]["posts"].append({"post_id": post_id, "text": text, "msg": msg})
        logger.debug("Buffered post #%s for user %s (batch size=%d)",
                     post_id, tg_id, len(_batch_buffer[key]["posts"]))


async def _deliver_to_user(
    client: TelegramClient,
    tg_id: int,
    msg,
    channel_label: str,
    post_id: int,
    text: str,
) -> None:
    from src.bot.app import ptb_app

    forwarded = False

    # Try forward via Bot API (works if bot is member of the channel)
    from_chat_id = int(f"-100{msg.peer_id.channel_id}")
    try:
        await ptb_app.bot.forward_message(
            chat_id=tg_id,
            from_chat_id=from_chat_id,
            message_id=msg.id,
        )
        logger.info("✅ Forwarded post #%s to user %s", post_id, tg_id)
        forwarded = True
    except Exception as exc:
        logger.info("Bot API forward failed (%s) — falling back to text", exc)

    # Fallback: send text via Bot API (always delivers to the bot chat)
    if not forwarded:
        if text:
            try:
                time_str = msg.date.strftime("%d.%m  %H:%M") if msg.date else ""
                header = f"📢 <b>{channel_label}</b>"
                if time_str:
                    header += f" <i>· {time_str} UTC</i>"
                await ptb_app.bot.send_message(
                    chat_id=tg_id,
                    text=f"{header}\n\n{text}\n\n<i>#{post_id}</i>",
                    parse_mode="HTML",
                )
                logger.info("✅ Sent text post #%s to user %s", post_id, tg_id)
                forwarded = True
            except Exception as exc:
                logger.warning("Cannot deliver post #%s to user %s: %s", post_id, tg_id, exc)
                return
        else:
            logger.warning(
                "Post #%s has no text and could not be forwarded to user %s", post_id, tg_id
            )
            return

    db = get_session()
    try:
        if _should_auto_summary(db, tg_id) and text and len(text.strip()) >= 50:
            await _send_summary(client, tg_id, post_id, text, channel_label, db)
    finally:
        db.close()


async def _send_summary(
    client: TelegramClient,
    tg_id: int,
    post_id: int,
    text: str,
    channel_label: str,
    db,
) -> None:
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        return
    if not post.summary:
        try:
            post.summary = summarize(text)
            db.commit()
        except Exception as exc:
            logger.error("Summarization failed for post %s: %s", post_id, exc)
            return
    from src.bot.app import ptb_app
    try:
        await ptb_app.bot.send_message(
            chat_id=tg_id,
            text=(
                f"📝 <b>Саммари</b> из <b>{channel_label}</b>:\n\n{post.summary}\n\n"
                f"Запросить снова: /summary {post_id}"  # noqa: E231
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Cannot send summary to %s: %s", tg_id, exc)


async def _send_batch(client: TelegramClient, tg_id: int, entry: dict) -> None:
    from src.bot.app import ptb_app
    posts = entry["posts"]
    label = entry["label"]

    if len(posts) == 1:
        p = posts[0]
        await _deliver_to_user(client, tg_id, p["msg"], label, p["post_id"], p["text"])
        return

    if ptb_app is None:
        return

    sep = "―――――――――――――――"
    lines = [f"📢 <b>{label}</b> — {len(posts)} новых поста"]
    for i, p in enumerate(posts, 1):
        preview = (p["text"] or "[медиа]")[:120]
        if len(p["text"] or "") > 120:
            preview += "…"
        lines.append(f"{sep}\n{i}. {preview}\n/summary {p['post_id']}")
    try:
        await ptb_app.bot.send_message(
            chat_id=tg_id,
            text="\n\n".join(lines),
            parse_mode="HTML",
        )
        logger.info("✅ Batch (%d posts) from %s → user %s", len(posts), label, tg_id)
    except Exception as exc:
        logger.warning("Cannot send batch to user %s: %s", tg_id, exc)


async def _batch_flush_loop(client: TelegramClient) -> None:
    while True:
        await asyncio.sleep(30)
        now = time.monotonic()
        to_flush = [
            key for key, entry in list(_batch_buffer.items())
            if now - entry["first_at"] >= BATCH_WINDOW_SECS
        ]
        for key in to_flush:
            entry = _batch_buffer.pop(key, None)
            if not entry:
                continue
            tg_id, _ = key
            try:
                await _send_batch(client, tg_id, entry)
            except Exception as exc:
                logger.warning("Batch flush error for user %s: %s", tg_id, exc)


async def _build_and_send_digest(telegram_id: int, db) -> bool:
    """Build and send digest for one user. Returns True if sent, False if nothing to send."""
    from src.bot.app import ptb_app
    if ptb_app is None:
        return False

    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    if not user or not user.can_auto_summary:
        return False

    ucs = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).all()
    channel_ids = [uc.channel_id for uc in ucs]
    if not channel_ids:
        return False

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    posts = (
        db.query(Post)
        .filter(Post.channel_id.in_(channel_ids), Post.created_at >= since)
        .order_by(Post.created_at.desc())
        .limit(10)
        .all()
    )
    if not posts:
        return False

    ch_map = {uc.channel_id: uc.channel.username for uc in ucs}
    date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    sep = "―――――――――――――――"
    lines = [f"📰 <b>Дайджест {date_str}</b> — {len(posts)} постов"]
    for i, post in enumerate(posts, 1):
        ch_name = ch_map.get(post.channel_id, "?")
        time_str = post.created_at.strftime("%H:%M") if post.created_at else ""
        preview = (post.text or "[медиа]")[:200]
        if len(post.text or "") > 200:
            preview += "…"
        lines.append(
            f"{sep}\n"
            f"{i}. 📢 <b>@{ch_name}</b> <i>{time_str} UTC</i>\n"
            f"{preview}\n"
            f"/summary {post.id}"
        )

    await ptb_app.bot.send_message(
        chat_id=telegram_id,
        text="\n\n".join(lines),
        parse_mode="HTML",
    )
    return True


async def send_digest_now(telegram_id: int) -> bool:
    """Send digest on demand for a specific user. Returns True on success."""
    db = get_session()
    try:
        return await _build_and_send_digest(telegram_id, db)
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


async def _poll_channels(client: TelegramClient) -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL)
        db = get_session()
        try:
            channels = db.query(Channel).filter(Channel.telegram_id.isnot(None)).all()
            channel_list = [(ch.id, ch.telegram_id, ch.username, ch.title) for ch in channels]
        finally:
            db.close()

        for ch_id, tg_channel_id, username, title in channel_list:
            db = get_session()
            try:
                last_post = (
                    db.query(Post)
                    .filter_by(channel_id=ch_id)
                    .order_by(Post.message_id.desc())
                    .first()
                )
                min_id = last_post.message_id if last_post else 0
            finally:
                db.close()

            try:
                messages = await client.get_messages(tg_channel_id, limit=20, min_id=min_id)
                if messages:
                    logger.info("Poll @%s: %d new message(s)", username, len(messages))
                    ch_obj = Channel(
                        id=ch_id, telegram_id=tg_channel_id, username=username, title=title
                    )
                    for msg in reversed(messages):
                        await _process_message(client, ch_obj, msg)
            except Exception as exc:
                logger.warning("Poll failed for @%s: %s", username, exc)


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
            try:
                await client(JoinChannelRequest(entity))
                logger.info("Joined @%s", ch.username)
            except Exception as exc:
                logger.warning("Join @%s failed: %s — polling will cover it", ch.username, exc)
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

    _register_live_handler(client)
    await _resolve_channels(client)

    loop = asyncio.get_event_loop()
    loop.create_task(_poll_channels(client))
    loop.create_task(_digest_loop())
    loop.create_task(_batch_flush_loop(client))

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
