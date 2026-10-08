"""Telethon userbot — monitors channels via polling + live events."""
import asyncio
import html
import io
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import joinedload
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    Message,
    MessageMediaDocument,
    MessageMediaPhoto,
    UpdateNewChannelMessage,
)

from src.bot.i18n import lang_of, t
from src.bot.keyboards import summary_button
from src.bot.messages import send_html, split_html, visible_units
from src.config import config
from src.database import db_session, get_session
from src.models import (
    BotEvent, BotHealth, Channel, DailyDigest, PendingPost, Post, User, UserChannel,
)
from src.services import metrics
from src.services.summarizer import build_digest, summarize

logger = logging.getLogger(__name__)

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

# Every post is delivered as its own message (no burst summaries — users
# explicitly preferred separate posts). The short grace lets an album caption
# that rides on a later item attach before its post leaves the buffer;
# PTB's AIORateLimiter paces the sends when a backfill produces many at once.
DELIVERY_GRACE_SECS = 20
FLUSH_TICK_SECS = 10

# Deliveries run as tasks so one slow media download cannot head-of-line-block
# every other user's posts. The semaphore caps parallelism; _in_flight keeps
# per-(user, channel) ordering and lets the SIGTERM flush persist entries that
# were mid-delivery when the restart hit.
_DELIVERY_CONCURRENCY = 3
_delivery_sem = asyncio.Semaphore(_DELIVERY_CONCURRENCY)
_in_flight: dict[tuple[int, int], dict] = {}
_pending_keys: set[tuple[int, int]] = set()
_poll_ready = asyncio.Event()
_pending_flush_lock = asyncio.Lock()

# (tg_id, channel_id) → {"posts": [...], "label": str, "username": str, "first_at": float}
_batch_buffer: dict[tuple[int, int], dict] = {}

# Bot API file_id per delivered post: a channel with N subscribers uploads the
# file once and reuses the id for the other N-1 sends. Process-local — after a
# restart the first delivery simply re-uploads.
_media_file_ids: dict[int, str] = {}
_MEDIA_CACHE_MAX = 500

# Telegram's caption limit is 1024 visible chars. The budget for post text is
# computed per-message (the header carries the channel title, which varies);
# this cap just keeps very long posts readable as a separate message.
_CAPTION_TEXT_BUDGET = 900


def _caption_text_budget(header: str, post_id: int) -> int:
    """How many text chars fit into the caption next to this header.

    Telegram counts visible (parsed) characters, so tags are stripped.
    A margin absorbs entity unescaping and emoji counting as two UTF-16 units.
    """
    overhead = visible_units(header) + len(f"#{post_id}") + 4
    return min(_CAPTION_TEXT_BUDGET, 1024 - overhead - 20)


def _caption_fits(caption: str) -> bool:
    """Whether an HTML caption fits Telegram's 1024-visible-character limit."""
    return visible_units(caption) <= 1024


def _get_media_type(message) -> str | None:
    """Classify the message's own attachment.

    Deliberately inspects message.media instead of Telethon's .photo/.video/
    .document helpers: those fall through to the WEB PREVIEW of a link, so a
    plain text post linking an article would be classified as a photo and
    delivered as that article's og:image.
    """
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        attrs = getattr(getattr(media, "document", None), "attributes", None) or []
        for attr in attrs:
            if isinstance(attr, DocumentAttributeVideo):
                return "video"
            if isinstance(attr, DocumentAttributeAudio):
                return "audio"
        return "document"
    return None  # web previews, polls, geo, contacts — nothing to re-upload


def _media_filename(message) -> str | None:
    """Original filename, so re-uploaded documents don't arrive as
    'application.octet-stream' (PTB's fallback name for raw bytes)."""
    media = getattr(message, "media", None)
    attrs = getattr(getattr(media, "document", None), "attributes", None) or []
    for attr in attrs:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
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

    Checks applied:
    1. Channel-limit enforcement — if the user's tier allows fewer channels
       than they currently have, only the earliest-added ones are delivered.
       The UserChannel record is NOT modified (soft enforcement).
    """
    return [
        (telegram_id, ai_filter)
        for telegram_id, ai_filter, _ in _get_eligible_subscriber_details(
            db, channel_id, text
        )
    ]


def _get_eligible_subscriber_details(
    db, channel_id: int, text: str,
) -> list[tuple[int, str | None, bool]]:
    """Return eligible subscribers plus whether they chose digest-only delivery."""
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

        # Channel limit enforcement (soft): skip if channel is outside allowed set
        if user.channel_limit is not None:
            if channel_id not in allowed_map.get(user.id, set()):
                logger.debug(
                    "Skipping user %s (channel %s exceeds tier limit %d)",
                    user.telegram_id, channel_id, user.channel_limit,
                )
                continue

        digest_only = user.digest_enabled and user.can_auto_summary
        result.append((user.telegram_id, uc.ai_filter, digest_only))

    logger.debug("Eligible subscribers for channel %s: %s", channel_id, [r[0] for r in result])
    return result


def _user_lang(db, telegram_id: int) -> str:
    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    return lang_of(user) if user else "ru"


def _media_size_ok(msg) -> bool:
    size = getattr(getattr(msg, "file", None), "size", None)
    if size is None:
        return True  # photos may not report a size; they are small anyway
    return size <= config.MEDIA_MAX_MB * 1024 * 1024


async def _download_media_bytes(client: TelegramClient, msg) -> io.BytesIO | None:
    """Fetch the media via the userbot. None on any failure — delivery falls
    back to the text/link form rather than dying.

    Returns the buffer itself rather than .getvalue() so a multi-MB file is
    not held twice in memory on the single worker thread.
    """
    try:
        buf = io.BytesIO()
        await client.download_media(msg, file=buf)
        if not buf.getbuffer().nbytes:
            return None
        buf.seek(0)
        return buf
    except Exception as exc:
        logger.warning("Media download failed for msg %s: %s", msg.id, exc)
        return None


async def _send_media(
    tg_id: int, media_kind: str, media, caption: str,
    reply_markup=None, filename: str | None = None,
):
    """Send one media message via the bot. Returns the PTB Message."""
    from src.bot.app import ptb_app
    if hasattr(media, "seek"):
        media.seek(0)  # reusable across retries
    kwargs = dict(
        chat_id=tg_id, caption=caption, parse_mode="HTML", reply_markup=reply_markup,
        # PTB's defaults (read 5s / media write 20s) are far too tight for
        # multi-MB uploads: they raise TimedOut after Telegram already accepted
        # the message, which would double-deliver the post.
        read_timeout=120, write_timeout=120, connect_timeout=30,
    )
    if media_kind == "photo":
        return await ptb_app.bot.send_photo(photo=media, **kwargs)
    if media_kind == "video":
        return await ptb_app.bot.send_video(video=media, **kwargs)
    if media_kind == "audio":
        return await ptb_app.bot.send_audio(audio=media, **kwargs)
    if filename:
        kwargs["filename"] = filename
    return await ptb_app.bot.send_document(document=media, **kwargs)


def _is_file_error(exc: Exception) -> bool:
    """True when the failure is about the FILE, not the recipient.

    A cached file_id must survive per-user failures (blocked bot, deleted
    account); dropping it there would make every remaining subscriber
    re-download and re-upload the same multi-MB file.
    """
    text = str(exc).lower()
    return any(s in text for s in (
        "file", "wrong file identifier", "media", "caption", "photo", "document",
    ))


def _cache_file_id(post_id: int, sent) -> None:
    """Remember the Bot API file_id from the first upload for reuse."""
    try:
        fid = None
        if sent.photo:
            fid = sent.photo[-1].file_id
        elif sent.video:
            fid = sent.video.file_id
        elif sent.audio:
            fid = sent.audio.file_id
        elif sent.document:
            fid = sent.document.file_id
        if fid:
            if len(_media_file_ids) >= _MEDIA_CACHE_MAX:
                _media_file_ids.pop(next(iter(_media_file_ids)))
            _media_file_ids[post_id] = fid
    except Exception:
        pass  # cache is an optimization; never let it break delivery


def _absorb_album_sibling(db, channel_id: int, msg, head: Post) -> None:
    """An album item whose group already has a Post: advance the polling
    cursor past it and, if the caption rides on this item, attach the text
    to the saved post (and to any batch-buffer copy awaiting delivery)."""
    try:
        ch_row = db.query(Channel).filter_by(id=channel_id).first()
        if ch_row and (ch_row.last_message_id or 0) < msg.id:
            ch_row.last_message_id = msg.id
        if msg.message and not head.text:
            head.text = msg.message
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("Album sibling absorb failed: %s", exc)


def _save_message(channel: Channel, msg):
    """Save with a worker-owned session; return plain values to the event loop."""
    grouped_id = getattr(msg, "grouped_id", None)
    with db_session() as db:
        existing = db.query(Post).filter_by(
            channel_id=channel.id, message_id=msg.id
        ).first()
        if existing:
            return

        # Albums stay durable across restarts; buffer updates happen on the loop.
        if grouped_id:
            head = (
                db.query(Post)
                .filter_by(channel_id=channel.id, grouped_id=grouped_id)
                .order_by(Post.message_id)
                .first()
            )
            if head is not None:
                _absorb_album_sibling(db, channel.id, msg, head)
                return head.id, head.text or "", [], False

        text = msg.message or ""
        subscriber_ids = _get_eligible_subscriber_details(db, channel.id, text)
        post = Post(
            channel_id=channel.id, message_id=msg.id, text=text,
            media_type=_get_media_type(msg), grouped_id=grouped_id,
        )
        db.add(post)
        db.flush()
        post_id = post.id
        ch_row = db.query(Channel).filter_by(id=channel.id).first()
        if ch_row and (ch_row.last_message_id or 0) < msg.id:
            ch_row.last_message_id = msg.id
        db.commit()
        return post_id, text, subscriber_ids, True


async def _process_message(client: TelegramClient, channel: Channel, msg) -> None:
    if not isinstance(msg, Message):
        return
    try:
        saved = await asyncio.to_thread(_save_message, channel, msg)
        if saved is None:
            return
        post_id, text, subscriber_ids, is_new = saved
        if not is_new:
            # Keep event-loop-owned buffers out of the database worker.
            for buf in _batch_buffer.values():
                for p in buf["posts"]:
                    if p["post_id"] == post_id and not p["text"]:
                        p["text"] = text
            return
        metrics.record(metrics.POST_SAVED)

    except Exception as exc:
        logger.exception("Error saving post msg_id=%s: %s", msg.id, exc)
        raise

    if not subscriber_ids:
        return

    logger.info("New post #%s from @%s → %d candidate(s)",
                post_id, channel.username, len(subscriber_ids))

    channel_label = channel.title or f"@{channel.username}"
    for tg_id, _ai_filter, digest_only in subscriber_ids:
        # Daily digest is a reading mode, not an extra duplicate notification.
        # The post remains in the DB and is included in the user's digest.
        if digest_only:
            logger.debug("Digest-only user %s: post #%s stored without instant send",
                         tg_id, post_id)
            continue
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


def _delivery_preferences(tg_id: int, post_id: int) -> tuple[str, bool, str | None]:
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=tg_id).options(
            joinedload(User.subscriptions),
        ).first()
        if not user:
            return "ru", False, None
        row = db.query(UserChannel.ai_filter).join(
            Post, Post.channel_id == UserChannel.channel_id,
        ).filter(UserChannel.user_id == user.id, Post.id == post_id).first()
        return lang_of(user), user.auto_summary and user.can_auto_summary, row[0] if row else None


def _post_summary(post_id: int) -> str | None:
    with db_session() as db:
        row = db.query(Post.summary).filter_by(id=post_id).first()
        return row[0] if row else None


def _store_summary(post_id: int, summary: str) -> None:
    with db_session() as db:
        db.query(Post).filter_by(id=post_id).update({Post.summary: summary})
        db.commit()


async def _deliver_to_user(
    client: TelegramClient,
    tg_id: int,
    msg,
    channel_label: str,
    post_id: int,
    text: str,
    username: str | None = None,
) -> None:
    from src.bot.app import ptb_app

    # Auto-summary mode: send only the summary + a link to the original post,
    # never the full post. Falls through to normal delivery if AI fails.
    lang, auto_summary, ai_filter = await asyncio.to_thread(_delivery_preferences, tg_id, post_id)
    if ai_filter and text:
        from src.services.summarizer import is_relevant
        if not await asyncio.to_thread(is_relevant, text, ai_filter):
            return
    if auto_summary and text and len(text.strip()) >= 50:
        sent = await _send_summary(
            client, tg_id, post_id, text, channel_label, lang=lang,
            username=username, msg_id=msg.id, msg=msg,
        )
        if sent:
            return

    # Header is shared by every delivery form: channel name as a hyperlink
    # to the original post.
    if username:
        url = f"https://t.me/{username}/{msg.id}"
    else:
        url = f"https://t.me/c/{msg.peer_id.channel_id}/{msg.id}"
    time_str = msg.date.strftime("%d.%m  %H:%M") if msg.date else ""
    header = f'📢 <b><a href="{url}">{html.escape(channel_label)}</a></b>'
    if time_str:
        header += f" <i>· {time_str} UTC</i>"

    # Media: the bot is not a member of the channel, so it cannot forward.
    # The userbot (same process) downloads the file instead, the bot uploads
    # it once, and the returned Bot API file_id is reused for every other
    # subscriber of this post.
    media_kind = _get_media_type(msg)
    media_sent = False
    caption_covers_text = False
    if media_kind:
        media = _media_file_ids.get(post_id)
        uploaded_bytes = False
        if media is None and _media_size_ok(msg):
            media = await _download_media_bytes(client, msg)
            uploaded_bytes = media is not None
        if media is not None:
            fits = bool(text) and visible_units(html.escape(text)) <= _caption_text_budget(
                header, post_id,
            )
            caption = header
            markup = None
            if fits:
                caption += f"\n\n{html.escape(text)}\n\n<i>#{post_id}</i>"
                markup = summary_button(post_id, lang)
            try:
                sent = await _send_media(
                    tg_id, media_kind, media, caption, markup,
                    filename=_media_filename(msg),
                )
                if uploaded_bytes:
                    _cache_file_id(post_id, sent)
                media_sent = True
                caption_covers_text = fits
                logger.info("✅ Sent %s post #%s to user %s", media_kind, post_id, tg_id)
                metrics.record(metrics.DELIVERED_POST)
            except Exception as exc:
                # Drop the cached id only when the FILE is at fault. A blocked
                # or deleted recipient says nothing about the file, and
                # dropping it there would make every remaining subscriber
                # re-download and re-upload the same multi-MB media.
                if _is_file_error(exc):
                    _media_file_ids.pop(post_id, None)
                logger.warning("Media send failed for post #%s to user %s: %s",
                               post_id, tg_id, exc)
                # Recorded on its own axis: the post may still be delivered as
                # text below, so this must not masquerade as a clean delivery
                # in the daily report.
                metrics.record(metrics.ERROR_MEDIA, str(exc))

    if media_sent and (caption_covers_text or not text):
        return

    # Text message: the whole post when there is no media, the full text as a
    # follow-up when the caption budget was too small for it, or the fallback
    # when the media itself could not be delivered.
    if text:
        try:
            await send_html(
                ptb_app.bot, tg_id, f"{header}\n\n{html.escape(text)}\n\n<i>#{post_id}</i>",
                summary_button(post_id, lang),
            )
            logger.info("✅ Sent post #%s to user %s", post_id, tg_id)
            if not media_sent:
                metrics.record(metrics.DELIVERED_POST)
        except Exception as exc:
            logger.warning("Cannot deliver post #%s to user %s: %s", post_id, tg_id, exc)
            # Exactly one outcome per post per user: if the media already
            # landed, this follow-up failure is not a failed delivery.
            if not media_sent:
                metrics.record(metrics.ERROR_DELIVERY, str(exc))
            raise
        return

    # Media-only post that could not be re-uploaded (too big, download or send
    # failed): send the link note. The old code tried forward_message here,
    # which ALWAYS failed — a bot can only forward from chats it belongs to.
    try:
        await ptb_app.bot.send_message(
            chat_id=tg_id,
            text=f"{header}\n\n{t('media_fallback', lang)}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("✅ Sent media-fallback for post #%s to user %s", post_id, tg_id)
        metrics.record(metrics.DELIVERED_POST)
    except Exception as exc:
        logger.warning("Cannot deliver post #%s to user %s: %s", post_id, tg_id, exc)
        metrics.record(metrics.ERROR_DELIVERY, str(exc))
        raise


async def _send_summary(
    client: TelegramClient,
    tg_id: int,
    post_id: int,
    text: str,
    channel_label: str,
    lang: str = "ru",
    username: str | None = None,
    msg_id: int | None = None,
    msg=None,
) -> bool:
    """Send the AI summary with a link to the original post. Returns True on success.

    A media post keeps its media: the summary rides as the caption, so
    auto-summary mode shortens the feed without stripping the picture.
    Any media problem falls back to the plain text form — the summary itself
    must arrive either way.
    """
    summary = await asyncio.to_thread(_post_summary, post_id)
    if not summary:
        try:
            # to_thread: the Anthropic call is blocking — keep the event loop alive
            summary = await asyncio.to_thread(summarize, text, lang)
            await asyncio.to_thread(_store_summary, post_id, summary)
        except Exception as exc:
            logger.error("Summarization failed for post %s: %s", post_id, exc)
            return False

    if username and msg_id:
        url = f"https://t.me/{username}/{msg_id}"
    elif username:
        url = f"https://t.me/{username}"
    else:
        url = f"https://t.me/{channel_label.lstrip('@')}"

    body = t("auto_summary_msg", lang, label=html.escape(channel_label),
             text=html.escape(summary), url=url, id=post_id)

    media_kind = _get_media_type(msg) if msg is not None else None
    # Summaries are capped at ~250 tokens, so the caption limit is rarely an
    # issue — but a long channel label plus a wordy summary can still cross
    # 1024 visible chars, and then the whole send would fail.
    fits_caption = _caption_fits(body)
    if media_kind:
        media = _media_file_ids.get(post_id)
        uploaded_bytes = False
        if media is None and _media_size_ok(msg):
            media = await _download_media_bytes(client, msg)
            uploaded_bytes = media is not None
        if media is not None:
            try:
                sent = await _send_media(
                    tg_id, media_kind, media,
                    body if fits_caption else f"📝 <b>{html.escape(channel_label)}</b>",
                    filename=_media_filename(msg),
                )
                if uploaded_bytes:
                    _cache_file_id(post_id, sent)
                logger.info("✅ Auto-summary+%s for post #%s → user %s",
                            media_kind, post_id, tg_id)
                if fits_caption:
                    metrics.record(metrics.DELIVERED_SUMMARY)
                    return True
            except Exception as exc:
                if _is_file_error(exc):
                    _media_file_ids.pop(post_id, None)
                logger.warning("Summary media send failed for post #%s to %s: %s",
                               post_id, tg_id, exc)
                metrics.record(metrics.ERROR_MEDIA, str(exc))

    from src.bot.app import ptb_app
    try:
        await send_html(ptb_app.bot, tg_id, body)
        logger.info("✅ Auto-summary for post #%s → user %s", post_id, tg_id)
        metrics.record(metrics.DELIVERED_SUMMARY)
        return True
    except Exception as exc:
        logger.warning("Cannot send summary to %s: %s", tg_id, exc)
        metrics.record(metrics.ERROR_DELIVERY, str(exc))
        return False


def _queue_pending(tg_id: int, channel_id: int, posts: list[dict]) -> bool:
    """Persist a buffered batch so a restart or send failure can't lose it.

    Used by the SIGTERM handler and the delivery-error path; the rows are
    delivered right after the next startup by _flush_pending().
    """
    db = None
    try:
        db = get_session()
        for p in posts:
            exists = db.query(PendingPost).filter_by(
                telegram_id=tg_id, post_id=p["post_id"]
            ).first()
            if not exists:
                db.add(PendingPost(
                    telegram_id=tg_id, channel_id=channel_id, post_id=p["post_id"]
                ))
        db.commit()
        logger.info("Persisted %d buffered post(s) for user %s until restart",
                    len(posts), tg_id)
        return True
    except Exception as exc:
        logger.warning("Cannot queue pending posts for user %s: %s", tg_id, exc)
        if db is not None:
            db.rollback()
        return False
    finally:
        if db is not None:
            db.close()


def flush_buffer_on_shutdown() -> None:
    """Persist everything still buffered in memory before the process dies.

    Called from the SIGTERM handler — GitHub Actions restarts the bot every
    few hours, and without this the in-memory buffer would be lost.
    """
    # In-flight entries too: SIGTERM can land mid-delivery, after the entry
    # left the buffer but before the send finished. pending_posts dedupes by
    # (telegram_id, post_id), so a delivery that DID complete just before the
    # snapshot at worst re-queues rows that startup delivery will skip.
    entries = list(_batch_buffer.items()) + list(_in_flight.items())
    _batch_buffer.clear()
    _in_flight.clear()
    for (tg_id, channel_id), entry in entries:
        _queue_pending(tg_id, channel_id, entry["posts"])
    if entries:
        logger.info("Shutdown flush: %d buffered batch(es) persisted", len(entries))
    # Metrics buffer too — otherwise every restart drops the events since the
    # last periodic flush, and restarts are frequent by design.
    written = metrics.flush()
    if written:
        logger.info("Shutdown flush: %d metric event(s) persisted", written)


def _entry_due(entry: dict, now: float) -> bool:
    return now - entry["first_at"] >= DELIVERY_GRACE_SECS


async def _deliver_entry(client: TelegramClient, key: tuple[int, int], entry: dict) -> None:
    """Deliver every buffered post individually, oldest first."""
    tg_id, channel_id = key
    posts = entry["posts"]
    try:
        async with _delivery_sem:
            for i, p in enumerate(posts):
                try:
                    await _deliver_to_user(
                        client, tg_id, p["msg"], entry["label"], p["post_id"], p["text"],
                        username=entry.get("username"),
                    )
                except Exception as exc:
                    # Persist the UNDELIVERED tail for the next startup; what
                    # already went out must not be re-sent.
                    logger.warning("Delivery error for user %s: %s — re-queued %d post(s)",
                                   tg_id, exc, len(posts) - i)
                    metrics.record(metrics.ERROR_DELIVERY, str(exc))
                    queued = await asyncio.to_thread(
                        _queue_pending, tg_id, channel_id, posts[i:],
                    )
                    if queued:
                        _pending_keys.add(key)
                    else:
                        newer = _batch_buffer.pop(key, None)
                        entry["posts"] = posts[i:] + (newer["posts"] if newer else [])
                        entry["first_at"] = time.monotonic()
                        _batch_buffer[key] = entry
                    return
                else:
                    # Shutdown must persist only the tail still awaiting delivery.
                    entry["posts"] = posts[i + 1:]
    finally:
        _in_flight.pop(key, None)


async def _batch_flush_loop(client: TelegramClient) -> None:
    """Flush due buffer entries as delivery tasks.

    Tasks rather than serial awaits: one 20MB media download must not stall
    every other user's delivery. _in_flight guards per-(user, channel) order —
    while a key is being delivered, its next entry stays buffered.
    """
    while True:
        await asyncio.sleep(FLUSH_TICK_SECS)
        now = time.monotonic()
        for key in list(_batch_buffer.keys()):
            if key in _in_flight or key in _pending_keys:
                continue
            entry = _batch_buffer.get(key)
            if entry is None or not _entry_due(entry, now):
                continue
            _batch_buffer.pop(key, None)
            _in_flight[key] = entry
            asyncio.get_running_loop().create_task(_deliver_entry(client, key, entry))


def _pending_snapshot():
    db = get_session()
    try:
        rows = (
            db.query(PendingPost)
            .order_by(PendingPost.telegram_id, PendingPost.channel_id, PendingPost.post_id)
            .all()
        )
        if not rows:
            return []

        groups: dict[tuple[int, int], list[int]] = {}
        for r in rows:
            groups.setdefault((r.telegram_id, r.channel_id), []).append(r.post_id)

        result = []
        for (tg_id, channel_id), post_ids in groups.items():
            channel = db.query(Channel).filter_by(id=channel_id).first()
            posts = (
                db.query(Post)
                .filter(Post.id.in_(post_ids))
                .order_by(Post.id)
                .all()
            )
            if not channel or not posts:
                for pid in post_ids:
                    db.query(PendingPost).filter_by(telegram_id=tg_id, post_id=pid).delete()
                db.commit()
                continue
            result.append((
                tg_id, channel_id, channel.telegram_id, channel.username,
                channel.title or f"@{channel.username}", _user_lang(db, tg_id),
                [(p.id, p.message_id, p.text or "") for p in posts],
            ))
        return result
    finally:
        db.close()


def _drop_pending(tg_id: int, post_id: int):
    with db_session() as db:
        db.query(PendingPost).filter_by(telegram_id=tg_id, post_id=post_id).delete()
        db.commit()


async def _flush_pending(client: TelegramClient | None = None) -> None:
    # Startup may still be downloading media when the periodic retry wakes up.
    # Serialize snapshots too, or one pass can replay rows the other just removed.
    async with _pending_flush_lock:
        await _flush_pending_once(client)


async def _flush_pending_once(client: TelegramClient | None = None) -> None:
    """Retry ordered tails with short worker-owned database sessions."""
    from src.bot.app import ptb_app
    if ptb_app is None:
        return
    groups = await asyncio.to_thread(_pending_snapshot)
    _pending_keys.update((g[0], g[1]) for g in groups)
    for tg_id, channel_id, tg_channel_id, username, label, lang, posts in groups:
        key = (tg_id, channel_id)
        if key in _in_flight:
            continue
        entry = {"label": label, "username": username,
                 "posts": [{"post_id": pid, "text": text} for pid, _, text in posts]}
        _in_flight[key] = entry
        try:
            async with _delivery_sem:
                for post_id, message_id, text in posts:
                    msg = None
                    if client is not None and tg_channel_id:
                        try:
                            msg = await client.get_messages(tg_channel_id, ids=message_id)
                        except Exception as exc:
                            logger.debug("Cannot refetch pending msg %s: %s", message_id, exc)
                    try:
                        if msg is not None:
                            await _deliver_to_user(
                                client, tg_id, msg, label, post_id, text, username=username,
                            )
                        elif text:
                            url = f"https://t.me/{username}/{message_id}"
                            header = f'📢 <b><a href="{url}">{html.escape(label)}</a></b>'
                            await send_html(
                                ptb_app.bot, tg_id, f"{header}\n\n{html.escape(text)}"
                                f"\n\n<i>#{post_id}</i>", summary_button(post_id, lang),
                            )
                            metrics.record(metrics.DELIVERED_POST)
                        await asyncio.to_thread(_drop_pending, tg_id, post_id)
                        entry["posts"] = entry["posts"][1:]
                    except Exception as exc:
                        logger.warning("Cannot deliver pending post #%s to %s: %s",
                                       post_id, tg_id, exc)
                        break  # The tail must not overtake the failed first post.
                else:
                    _pending_keys.discard(key)
        finally:
            _in_flight.pop(key, None)


async def _pending_retry_loop(client: TelegramClient) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _flush_pending(client)
        except Exception as exc:
            logger.warning("Pending delivery retry failed: %s", exc)


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
    db.query(DailyDigest).filter(DailyDigest.day < cutoff.date().isoformat()).delete(
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
    return split_html(text, MAX_MESSAGE_CHARS)


def _digest_html(ai_text: str) -> str:
    """Escape AI output for HTML parse_mode and bold the source headers."""
    lines = []
    for line in ai_text.splitlines():
        esc = html.escape(line)
        if esc.startswith("📢"):
            esc = f"<b>{esc}</b>"
        lines.append(esc)
    return "\n".join(lines)


def _digest_input(telegram_id: int, channel_ids: list[int] | None):
    """Fetch plain digest inputs in a worker, without holding a session over AI I/O."""
    from src.services.blocklist import blocked_usernames
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).options(
            joinedload(User.subscriptions),
        ).first()
        if not user or not user.can_auto_summary:
            return "ru", []
        ucs = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).options(
            joinedload(UserChannel.channel),
        ).all()
        blocked = blocked_usernames(db)
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        sections = []
        for uc in ucs:
            if channel_ids is not None and uc.channel_id not in channel_ids:
                continue
            if uc.channel.username.lower() in blocked:
                continue
            posts = db.query(Post.text).filter(
                Post.channel_id == uc.channel_id, Post.created_at >= since,
                Post.text.isnot(None), Post.text != "",
            ).order_by(Post.created_at.desc(), Post.id.desc()).limit(8).all()
            if posts:
                sections.append((uc.channel.username, [p[0] for p in posts]))
        return lang_of(user), sections


def _daily_state(telegram_id: int, day: str):
    with db_session() as db:
        row = db.get(DailyDigest, (telegram_id, day))
        return (row.message, row.next_chunk, row.completed) if row else None


def _prepare_daily(telegram_id: int, day: str, message: str) -> None:
    with db_session() as db:
        if db.get(DailyDigest, (telegram_id, day)) is None:
            db.add(DailyDigest(telegram_id=telegram_id, day=day, message=message,
                               completed=not message))
            db.commit()


def _advance_daily(telegram_id: int, day: str, next_chunk: int, completed: bool):
    with db_session() as db:
        db.query(DailyDigest).filter_by(telegram_id=telegram_id, day=day).update({
            DailyDigest.next_chunk: next_chunk, DailyDigest.completed: completed,
        })
        db.commit()


async def _build_and_send_digest(
    telegram_id: int, channel_ids: list[int] | None = None, *, day: str | None = None,
) -> bool:
    from src.bot.app import ptb_app
    if ptb_app is None:
        raise RuntimeError("Bot application is unavailable")
    state = await asyncio.to_thread(_daily_state, telegram_id, day) if day else None
    if state and state[2]:
        return True
    if state:
        full, start, _ = state
    else:
        lang, sections = await asyncio.to_thread(_digest_input, telegram_id, channel_ids)
        if not sections:
            if day:
                await asyncio.to_thread(_prepare_daily, telegram_id, day, "")
            return False
        ai_text = await asyncio.to_thread(build_digest, sections, lang)
        if not ai_text.strip():
            raise RuntimeError("AI returned an empty digest")
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")
        full = t("digest_header", lang, date=date_str) + "\n\n" + _digest_html(ai_text)
        if ptb_app.bot.username:
            full += "\n\n" + t("digest_footer", lang, bot=html.escape(ptb_app.bot.username))
        start = 0
        if day:
            await asyncio.to_thread(_prepare_daily, telegram_id, day, full)
    chunks = _split_message(full)
    for index in range(start, len(chunks)):
        await ptb_app.bot.send_message(
            chat_id=telegram_id, text=chunks[index], parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if day:
            await asyncio.to_thread(_advance_daily, telegram_id, day, index + 1,
                                    index == len(chunks) - 1)
    return True


async def send_digest_now(telegram_id: int, channel_ids: list[int] | None = None) -> bool:
    """Send digest on demand for a specific user. Returns True on success."""
    return await _build_and_send_digest(telegram_id, channel_ids)


def _daily_digest_users(day: str) -> list[int]:
    with db_session() as db:
        users = db.query(User).filter_by(digest_enabled=True).options(
            joinedload(User.subscriptions),
        ).all()
        completed = {tg for (tg,) in db.query(DailyDigest.telegram_id).filter_by(
            day=day, completed=True,
        ).all()}
        return [u.telegram_id for u in users
                if u.can_auto_summary and u.telegram_id not in completed]


async def _send_daily_digest() -> None:
    """Send daily digest to Pro users who enabled it."""
    day = datetime.now(timezone.utc).date().isoformat()
    tg_ids = await asyncio.to_thread(_daily_digest_users, day)
    for tg_id in tg_ids:
        try:
            sent = await _build_and_send_digest(tg_id, day=day)
            if sent:
                logger.info("Daily digest sent to user %s", tg_id)
        except Exception as exc:
            logger.warning("Daily digest failed for user %s: %s", tg_id, exc)


async def _digest_loop(wait_for_poll: bool = False) -> None:
    """Catch up a missed run; persisted per-user state prevents restart duplicates."""
    if wait_for_poll:
        # A restart gap must be backfilled before deciding that today's digest is empty.
        await _poll_ready.wait()
    while True:
        now = datetime.now(timezone.utc)
        if now.hour >= config.DIGEST_HOUR_UTC:
            try:
                await _send_daily_digest()
            except Exception as exc:
                logger.warning("Daily digest scheduler failed: %s", exc)
        await asyncio.sleep(300)


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


def _poll_channel_snapshot():
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
        return [
            (ch.id, ch.telegram_id, ch.username, ch.title, ch.last_message_id or 0)
            for ch in channels
            if (ch.username or "").lower() not in blocked
        ]
    finally:
        db.close()


async def _poll_channels(client: TelegramClient) -> None:
    while True:
        try:
            channel_list = await asyncio.to_thread(_poll_channel_snapshot)
        except Exception as exc:
            logger.warning("Cannot load channels for polling: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)
            continue

        if not channel_list:
            _poll_ready.set()
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # Spacing between channels sums to one POLL_INTERVAL per full cycle.
        spacing = POLL_INTERVAL / len(channel_list)
        for ch_id, tg_channel_id, username, title, min_id in channel_list:
            started = time.monotonic()
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
            await asyncio.sleep(max(0, spacing - (time.monotonic() - started)))
        _poll_ready.set()


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
    loop.create_task(_digest_loop(wait_for_poll=True))
    loop.create_task(_batch_flush_loop(client))
    loop.create_task(_pending_retry_loop(client))
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
