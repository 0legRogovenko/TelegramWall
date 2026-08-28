"""Post persistence, ordered delivery, buffering, and restart recovery."""
import asyncio
import html
import logging
import time

from telethon import TelegramClient
from telethon.tl.types import Message

from src.bot.i18n import lang_of, t
from src.bot.keyboards import summary_button
from src.database import db_session, get_session
from src.models import Channel, PendingPost, Post, User
from src.services import metrics
from src.services.summarizer import summarize
from src.userbot.media import (
    MEDIA_FILE_IDS,
    cache_file_id,
    caption_fits,
    caption_text_budget,
    download_media_bytes,
    get_media_type,
    is_file_error,
    media_filename,
    media_size_ok,
    send_media,
)
from src.userbot.subscribers import get_eligible_subscriber_details, user_lang

logger = logging.getLogger(__name__)

DELIVERY_GRACE_SECS = 20
FLUSH_TICK_SECS = 10
DELIVERY_CONCURRENCY = 3
DELIVERY_SEMAPHORE = asyncio.Semaphore(DELIVERY_CONCURRENCY)
IN_FLIGHT: dict[tuple[int, int], dict] = {}
BATCH_BUFFER: dict[tuple[int, int], dict] = {}

__all__ = [
    "BATCH_BUFFER",
    "DELIVERY_GRACE_SECS",
    "FLUSH_TICK_SECS",
    "IN_FLIGHT",
    "absorb_album_sibling",
    "batch_flush_loop",
    "deliver_entry",
    "deliver_to_user",
    "entry_due",
    "flush_buffer_on_shutdown",
    "flush_pending",
    "process_message",
    "queue_pending",
    "send_summary",
]


def absorb_album_sibling(db, channel_id: int, msg, head: Post) -> None:
    """An album item whose group already has a Post: advance the polling
    cursor past it and, if the caption rides on this item, attach the text
    to the saved post (and to any batch-buffer copy awaiting delivery)."""
    try:
        ch_row = db.query(Channel).filter_by(id=channel_id).first()
        if ch_row and (ch_row.last_message_id or 0) < msg.id:
            ch_row.last_message_id = msg.id
        if msg.message and not head.text:
            head.text = msg.message
            for buf in BATCH_BUFFER.values():
                for p in buf["posts"]:
                    if p["post_id"] == head.id and not p["text"]:
                        p["text"] = msg.message
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("Album sibling absorb failed: %s", exc)


async def process_message(client: TelegramClient, channel: Channel, msg) -> None:
    if not isinstance(msg, Message):
        return

    grouped_id = getattr(msg, "grouped_id", None)

    try:
        with db_session() as db:
            existing = db.query(Post).filter_by(
                channel_id=channel.id, message_id=msg.id
            ).first()
            if existing:
                return

            # Albums: one Post per grouped_id, the rest absorbed into it. The
            # check hits the DB rather than an in-memory map so an album spanning
            # a restart is not split into two posts and delivered twice.
            if grouped_id:
                head = (
                    db.query(Post)
                    .filter_by(channel_id=channel.id, grouped_id=grouped_id)
                    .order_by(Post.message_id)
                    .first()
                )
                if head is not None:
                    absorb_album_sibling(db, channel.id, msg, head)
                    return

            text = msg.message or ""
            media_type = get_media_type(msg)
            channel_label = channel.title or f"@{channel.username}"
            subscriber_ids = get_eligible_subscriber_details(db, channel.id, text)

            post = Post(
                channel_id=channel.id, message_id=msg.id, text=text,
                media_type=media_type, grouped_id=grouped_id,
            )
            db.add(post)
            db.flush()
            post_id = post.id
            ch_row = db.query(Channel).filter_by(id=channel.id).first()
            if ch_row and (ch_row.last_message_id or 0) < msg.id:
                ch_row.last_message_id = msg.id
            db.commit()
        metrics.record(metrics.POST_SAVED)

    except Exception as exc:
        logger.exception("Error saving post msg_id=%s: %s", msg.id, exc)
        return

    if not subscriber_ids:
        return

    logger.info("New post #%s from @%s → %d candidate(s)",
                post_id, channel.username, len(subscriber_ids))

    for tg_id, ai_filter, digest_only in subscriber_ids:
        # Daily digest is a reading mode, not an extra duplicate notification.
        # The post remains in the DB and is included in the user's digest.
        if digest_only:
            logger.debug("Digest-only user %s: post #%s stored without instant send",
                         tg_id, post_id)
            continue
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
        if key not in BATCH_BUFFER:
            BATCH_BUFFER[key] = {
                "posts": [],
                "label": channel_label,
                "username": channel.username,
                "first_at": time.monotonic(),
            }
        BATCH_BUFFER[key]["posts"].append({"post_id": post_id, "text": text, "msg": msg})
        logger.debug("Buffered post #%s for user %s (batch size=%d)",
                     post_id, tg_id, len(BATCH_BUFFER[key]["posts"]))


async def deliver_to_user(
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
    lang = "ru"
    db = get_session()
    try:
        user = db.query(User).filter_by(telegram_id=tg_id).first()
        lang = lang_of(user) if user else "ru"
        if (
            user and user.auto_summary and user.can_auto_summary
            and text and len(text.strip()) >= 50
        ):
            sent = await send_summary(
                client, tg_id, post_id, text, channel_label, db, lang,
                username=username, msg_id=msg.id, msg=msg,
            )
            if sent:
                return
    finally:
        db.close()
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
    media_kind = get_media_type(msg)
    media_sent = False
    caption_covers_text = False
    if media_kind:
        media = MEDIA_FILE_IDS.get(post_id)
        uploaded_bytes = False
        if media is None and media_size_ok(msg):
            media = await download_media_bytes(client, msg)
            uploaded_bytes = media is not None
        if media is not None:
            fits = bool(text) and len(text) <= caption_text_budget(header, post_id)
            caption = header
            markup = None
            if fits:
                caption += f"\n\n{html.escape(text)}\n\n<i>#{post_id}</i>"
                markup = summary_button(post_id, lang)
            try:
                sent = await send_media(
                    tg_id, media_kind, media, caption, markup,
                    filename=media_filename(msg),
                )
                if uploaded_bytes:
                    cache_file_id(post_id, sent)
                media_sent = True
                caption_covers_text = fits
                logger.info("✅ Sent %s post #%s to user %s", media_kind, post_id, tg_id)
                metrics.record(metrics.DELIVERED_POST)
            except Exception as exc:
                # Drop the cached id only when the FILE is at fault. A blocked
                # or deleted recipient says nothing about the file, and
                # dropping it there would make every remaining subscriber
                # re-download and re-upload the same multi-MB media.
                if is_file_error(exc):
                    MEDIA_FILE_IDS.pop(post_id, None)
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
            await ptb_app.bot.send_message(
                chat_id=tg_id,
                text=f"{header}\n\n{html.escape(text)}\n\n<i>#{post_id}</i>",
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=summary_button(post_id, lang),
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


async def send_summary(
    client: TelegramClient,
    tg_id: int,
    post_id: int,
    text: str,
    channel_label: str,
    db,
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
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        return False
    if not post.summary:
        try:
            # to_thread: the Anthropic call is blocking — keep the event loop alive
            post.summary = await asyncio.to_thread(summarize, text, lang)
            db.commit()
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
             text=html.escape(post.summary), url=url, id=post_id)

    media_kind = get_media_type(msg) if msg is not None else None
    # Summaries are capped at ~250 tokens, so the caption limit is rarely an
    # issue — but a long channel label plus a wordy summary can still cross
    # 1024 visible chars, and then the whole send would fail.
    if media_kind and not caption_fits(body):
        media_kind = None
    if media_kind:
        media = MEDIA_FILE_IDS.get(post_id)
        uploaded_bytes = False
        if media is None and media_size_ok(msg):
            media = await download_media_bytes(client, msg)
            uploaded_bytes = media is not None
        if media is not None:
            try:
                sent = await send_media(
                    tg_id, media_kind, media, body, filename=media_filename(msg),
                )
                if uploaded_bytes:
                    cache_file_id(post_id, sent)
                logger.info("✅ Auto-summary+%s for post #%s → user %s",
                            media_kind, post_id, tg_id)
                metrics.record(metrics.DELIVERED_SUMMARY)
                return True
            except Exception as exc:
                if is_file_error(exc):
                    MEDIA_FILE_IDS.pop(post_id, None)
                logger.warning("Summary media send failed for post #%s to %s: %s",
                               post_id, tg_id, exc)
                metrics.record(metrics.ERROR_MEDIA, str(exc))

    from src.bot.app import ptb_app
    try:
        await ptb_app.bot.send_message(
            chat_id=tg_id,
            text=body,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        logger.info("✅ Auto-summary for post #%s → user %s", post_id, tg_id)
        metrics.record(metrics.DELIVERED_SUMMARY)
        return True
    except Exception as exc:
        logger.warning("Cannot send summary to %s: %s", tg_id, exc)
        metrics.record(metrics.ERROR_DELIVERY, str(exc))
        return False


def queue_pending(tg_id: int, channel_id: int, posts: list[dict]) -> None:
    """Persist a buffered batch so a restart or send failure can't lose it.

    Used by the SIGTERM handler and the delivery-error path; the rows are
    delivered right after the next startup by flush_pending().
    """
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
        logger.info("Persisted %d buffered post(s) for user %s until restart",
                    len(posts), tg_id)
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
    # In-flight entries too: SIGTERM can land mid-delivery, after the entry
    # left the buffer but before the send finished. pending_posts dedupes by
    # (telegram_id, post_id), so a delivery that DID complete just before the
    # snapshot at worst re-queues rows that startup delivery will skip.
    entries = list(BATCH_BUFFER.items()) + list(IN_FLIGHT.items())
    BATCH_BUFFER.clear()
    IN_FLIGHT.clear()
    for (tg_id, channel_id), entry in entries:
        queue_pending(tg_id, channel_id, entry["posts"])
    if entries:
        logger.info("Shutdown flush: %d buffered batch(es) persisted", len(entries))
    # Metrics buffer too — otherwise every restart drops the events since the
    # last periodic flush, and restarts are frequent by design.
    written = metrics.flush()
    if written:
        logger.info("Shutdown flush: %d metric event(s) persisted", written)


def entry_due(entry: dict, now: float) -> bool:
    return now - entry["first_at"] >= DELIVERY_GRACE_SECS


async def deliver_entry(client: TelegramClient, key: tuple[int, int], entry: dict) -> None:
    """Deliver every buffered post individually, oldest first."""
    tg_id, channel_id = key
    posts = entry["posts"]
    try:
        async with DELIVERY_SEMAPHORE:
            for i, p in enumerate(posts):
                try:
                    await deliver_to_user(
                        client, tg_id, p["msg"], entry["label"], p["post_id"], p["text"],
                        username=entry.get("username"),
                    )
                except Exception as exc:
                    # Persist the UNDELIVERED tail for the next startup; what
                    # already went out must not be re-sent.
                    logger.warning("Delivery error for user %s: %s — re-queued %d post(s)",
                                   tg_id, exc, len(posts) - i)
                    metrics.record(metrics.ERROR_DELIVERY, str(exc))
                    queue_pending(tg_id, channel_id, posts[i:])
                    return
    finally:
        IN_FLIGHT.pop(key, None)


async def batch_flush_loop(client: TelegramClient) -> None:
    """Flush due buffer entries as delivery tasks.

    Tasks rather than serial awaits: one 20MB media download must not stall
    every other user's delivery. IN_FLIGHT guards per-(user, channel) order —
    while a key is being delivered, its next entry stays buffered.
    """
    while True:
        await asyncio.sleep(FLUSH_TICK_SECS)
        now = time.monotonic()
        for key in list(BATCH_BUFFER.keys()):
            if key in IN_FLIGHT:
                continue
            entry = BATCH_BUFFER.get(key)
            if entry is None or not entry_due(entry, now):
                continue
            BATCH_BUFFER.pop(key, None)
            IN_FLIGHT[key] = entry
            asyncio.get_running_loop().create_task(deliver_entry(client, key, entry))


async def flush_pending(client: TelegramClient | None = None) -> None:
    """Deliver queued posts left over from a restart — each as its own message.

    The original Telethon message is re-fetched when possible so media and the
    date survive the restart; a message that is gone (deleted, channel lost)
    falls back to the stored text with a header link, or is dropped if there
    is nothing sensible left to send.
    """
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
            def _drop_row(pid):
                db.query(PendingPost).filter(
                    PendingPost.telegram_id == tg_id,
                    PendingPost.post_id == pid,
                ).delete(synchronize_session=False)
                db.commit()

            channel = db.query(Channel).filter_by(id=channel_id).first()
            posts = (
                db.query(Post)
                .filter(Post.id.in_(post_ids))
                .order_by(Post.id)
                .all()
            )
            if not channel or not posts:
                for pid in post_ids:
                    _drop_row(pid)
                continue

            label = channel.title or f"@{channel.username}"
            lang = user_lang(db, tg_id)
            for post in posts:
                msg = None
                if client is not None and channel.telegram_id:
                    try:
                        msg = await client.get_messages(
                            channel.telegram_id, ids=post.message_id
                        )
                    except Exception as exc:
                        logger.debug("Cannot refetch msg %s of @%s: %s",
                                     post.message_id, channel.username, exc)
                try:
                    if msg is not None:
                        await deliver_to_user(
                            client, tg_id, msg, label, post.id, post.text or "",
                            username=channel.username,
                        )
                    elif post.text:
                        url = f"https://t.me/{channel.username}/{post.message_id}"
                        header = (f'\U0001F4E2 <b><a href="{url}">'
                                  f'{html.escape(label)}</a></b>')
                        await ptb_app.bot.send_message(
                            chat_id=tg_id,
                            text=(f"{header}\n\n{html.escape(post.text)}"
                                  f"\n\n<i>#{post.id}</i>"),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                            reply_markup=summary_button(post.id, lang),
                        )
                        metrics.record(metrics.DELIVERED_POST)
                    # media-only post whose message is gone: nothing to send
                    _drop_row(post.id)
                except Exception as exc:
                    db.rollback()
                    logger.warning("Cannot deliver persisted post #%s to user %s: %s",
                                   post.id, tg_id, exc)
                    # row stays — the next startup retries
    finally:
        db.close()
