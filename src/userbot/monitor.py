"""Telethon userbot — monitors channels via polling + live events."""
import asyncio
import html
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.types import (
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    UpdateNewChannelMessage,
)

from src.bot.i18n import count_new_posts, lang_of, t
from src.bot.keyboards import SEP
from src.config import config
from src.database import get_session
from src.models import Channel, PendingPost, Post, User, UserChannel
from src.services.summarizer import build_digest, summarize

logger = logging.getLogger(__name__)

_client: TelegramClient | None = None
POLL_INTERVAL = 30
BATCH_WINDOW_SECS = 90

# (tg_id, channel_id) → {"posts": [...], "label": str, "username": str, "first_at": float}
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


def _batch_allowed_channel_ids(db, limited_users: list[tuple[int, int]]) -> dict[int, set[int]]:
    """Return {user_id: set of allowed channel_ids} for users on limited tiers.

    Queries ALL UserChannels (active or not) so deactivating old channels
    cannot shift newer ones into the allowed window. Stable tie-breaking via
    (created_at, id) prevents non-determinism at identical timestamps.
    Single batch query replaces N individual queries.
    """
    if not limited_users:
        return {}
    user_ids = [uid for uid, _ in limited_users]
    limits = {uid: lim for uid, lim in limited_users}

    rows = (
        db.query(UserChannel.user_id, UserChannel.channel_id)
        .filter(UserChannel.user_id.in_(user_ids))
        .order_by(UserChannel.user_id, UserChannel.created_at, UserChannel.id)
        .all()
    )

    result: dict[int, set[int]] = {}
    counts: dict[int, int] = {}
    for user_id, ch_id in rows:
        seen = counts.get(user_id, 0)
        if seen < limits[user_id]:
            result.setdefault(user_id, set()).add(ch_id)
            counts[user_id] = seen + 1
    return result


def _get_eligible_subscribers(db, channel_id: int, text: str) -> list[tuple[int, str | None]]:
    """Return (telegram_id, ai_filter) for subscribers who pass all delivery checks.

    Checks applied (in order):
    1. Quiet hours
    2. Keyword filter
    3. Channel-limit enforcement — if the user's tier allows fewer channels
       than they currently have, only the earliest-added ones are delivered.
       The UserChannel record is NOT modified (soft enforcement).
    """
    now_hour = datetime.now(timezone.utc).hour
    ucs = (
        db.query(UserChannel)
        .filter_by(channel_id=channel_id, is_active=True)
        .options(joinedload(UserChannel.user).joinedload(User.subscriptions))
        .all()
    )

    limited_users = [
        (uc.user.id, uc.user.channel_limit)
        for uc in ucs
        if uc.user.channel_limit is not None
    ]
    allowed_map = _batch_allowed_channel_ids(db, limited_users)

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

        # Channel limit enforcement (soft): skip if channel is outside allowed set
        if user.channel_limit is not None:
            if channel_id not in allowed_map.get(user.id, set()):
                logger.debug(
                    "Skipping user %s (channel %s exceeds tier limit %d)",
                    user.telegram_id, channel_id, user.channel_limit,
                )
                continue

        result.append((user.telegram_id, uc.ai_filter))

    logger.debug("Eligible subscribers for channel %s: %s", channel_id, [r[0] for r in result])
    return result


def _user_lang(db, telegram_id: int) -> str:
    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    return lang_of(user) if user else "ru"


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
                "username": channel.username,
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
                header = f"📢 <b>{html.escape(channel_label)}</b>"
                if time_str:
                    header += f" <i>· {time_str} UTC</i>"
                await ptb_app.bot.send_message(
                    chat_id=tg_id,
                    text=f"{header}\n\n{html.escape(text)}\n\n<i>#{post_id}</i>",
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
        user = db.query(User).filter_by(telegram_id=tg_id).first()
        if (
            user and user.auto_summary and user.can_auto_summary
            and text and len(text.strip()) >= 50
        ):
            await _send_summary(
                client, tg_id, post_id, text, channel_label, db, lang_of(user)
            )
    finally:
        db.close()


async def _send_summary(
    client: TelegramClient,
    tg_id: int,
    post_id: int,
    text: str,
    channel_label: str,
    db,
    lang: str = "ru",
) -> None:
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        return
    if not post.summary:
        try:
            # to_thread: the Anthropic call is blocking — keep the event loop alive
            post.summary = await asyncio.to_thread(summarize, text, lang)
            db.commit()
        except Exception as exc:
            logger.error("Summarization failed for post %s: %s", post_id, exc)
            return
    from src.bot.app import ptb_app
    try:
        await ptb_app.bot.send_message(
            chat_id=tg_id,
            text=t("auto_summary_msg", lang, label=html.escape(channel_label),
                   text=html.escape(post.summary), id=post_id),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Cannot send summary to %s: %s", tg_id, exc)


def _format_batch_message(
    label: str, username: str, posts: list[dict], lang: str = "ru"
) -> str:
    """Summary message for several posts from one channel.

    posts: [{"post_id": int, "text": str}]
    """
    n = len(posts)
    lines = [t("batch_header", lang, label=html.escape(label),
               phrase=count_new_posts(n, lang))]
    for i, p in enumerate(posts, 1):
        preview = html.escape((p["text"] or "[media]")[:120])
        if len(p["text"] or "") > 120:
            preview += "…"
        lines.append(f"{SEP}\n{i}. {preview}\n/summary_{p['post_id']}")
    lines.append(f"{SEP}\n" + t("open_channel", lang, u=username))
    return "\n\n".join(lines)


def _queue_pending(tg_id: int, channel_id: int, posts: list[dict]) -> None:
    """Persist a multi-post batch for scheduled delivery (survives restarts)."""
    db = get_session()
    try:
        for p in posts:
            exists = db.query(PendingPost).filter_by(
                telegram_id=tg_id, post_id=p["post_id"]
            ).first()
            if not exists:
                db.add(PendingPost(
                    telegram_id=tg_id, channel_id=channel_id, post_id=p["post_id"]
                ))
        db.commit()
        logger.info("Queued %d post(s) for user %s (delivery at %s UTC)",
                    len(posts), tg_id, config.BATCH_HOURS_UTC)
    except Exception as exc:
        logger.warning("Cannot queue pending posts for user %s: %s", tg_id, exc)
        db.rollback()
    finally:
        db.close()


def flush_buffer_on_shutdown() -> None:
    """Persist everything still buffered in memory before the process dies.

    Called from the SIGTERM handler — GitHub Actions restarts the bot every
    few hours, and without this the in-memory buffer would be lost.
    """
    entries = list(_batch_buffer.items())
    _batch_buffer.clear()
    for (tg_id, channel_id), entry in entries:
        _queue_pending(tg_id, channel_id, entry["posts"])
    if entries:
        logger.info("Shutdown flush: %d buffered batch(es) persisted", len(entries))


async def _batch_flush_loop(client: TelegramClient) -> None:
    """Every 30s: single posts → deliver now; multi-post dumps → queue for 9/23."""
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
            tg_id, channel_id = key
            try:
                posts = entry["posts"]
                if len(posts) == 1:
                    p = posts[0]
                    await _deliver_to_user(
                        client, tg_id, p["msg"], entry["label"], p["post_id"], p["text"]
                    )
                else:
                    _queue_pending(tg_id, channel_id, posts)
            except Exception as exc:
                logger.warning("Batch flush error for user %s: %s", tg_id, exc)


async def _flush_pending() -> None:
    """Deliver all queued multi-post batches, grouped by user + channel."""
    from src.bot.app import ptb_app
    if ptb_app is None:
        return

    db = get_session()
    try:
        rows = (
            db.query(PendingPost)
            .order_by(PendingPost.telegram_id, PendingPost.channel_id, PendingPost.post_id)
            .all()
        )
        if not rows:
            return

        groups: dict[tuple[int, int], list[int]] = {}
        for r in rows:
            groups.setdefault((r.telegram_id, r.channel_id), []).append(r.post_id)

        for (tg_id, channel_id), post_ids in groups.items():
            channel = db.query(Channel).filter_by(id=channel_id).first()
            if not channel:
                continue
            posts = (
                db.query(Post)
                .filter(Post.id.in_(post_ids))
                .order_by(Post.id)
                .all()
            )
            label = channel.title or f"@{channel.username}"
            text = _format_batch_message(
                label, channel.username,
                [{"post_id": p.id, "text": p.text or ""} for p in posts],
                lang=_user_lang(db, tg_id),
            )
            try:
                await ptb_app.bot.send_message(
                    chat_id=tg_id, text=text, parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                db.query(PendingPost).filter(
                    PendingPost.telegram_id == tg_id,
                    PendingPost.channel_id == channel_id,
                    PendingPost.post_id.in_(post_ids),
                ).delete(synchronize_session=False)
                db.commit()
                logger.info("✅ Scheduled batch (%d posts) @%s → user %s",
                            len(posts), channel.username, tg_id)
            except Exception as exc:
                db.rollback()
                logger.warning("Cannot send scheduled batch to user %s: %s", tg_id, exc)
    finally:
        db.close()


async def _pending_delivery_loop() -> None:
    """Fire queued batch delivery at each hour in BATCH_HOURS_UTC (e.g. 6 и 20 UTC)."""
    hours = sorted(set(config.BATCH_HOURS_UTC)) or [6, 20]
    while True:
        now = datetime.now(timezone.utc)
        candidates = [
            now.replace(hour=h, minute=0, second=0, microsecond=0) for h in hours
        ]
        future = [t for t in candidates if t > now]
        target = future[0] if future else candidates[0] + timedelta(days=1)
        sleep_secs = (target - now).total_seconds()
        logger.info("Next scheduled batch delivery in %.0f minutes", sleep_secs / 60)
        await asyncio.sleep(sleep_secs)
        try:
            await _flush_pending()
        except Exception as exc:
            logger.warning("Scheduled batch delivery failed: %s", exc)


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
    header = t("digest_header", lang, date=date_str)
    await ptb_app.bot.send_message(
        chat_id=telegram_id,
        text=f"{header}\n\n{_digest_html(ai_text)}",
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
    loop.create_task(_pending_delivery_loop())

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
