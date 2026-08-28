"""AI digest formatting, generation, delivery, and scheduling."""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from src.bot.i18n import lang_of, t
from src.config import config
from src.database import get_session
from src.models import Post, User, UserChannel
from src.services.summarizer import build_digest

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_MESSAGE_CHARS",
    "build_and_send_digest",
    "digest_html",
    "digest_loop",
    "send_daily_digest",
    "send_digest_now",
    "split_message",
]

MAX_MESSAGE_CHARS = 3900  # Telegram limit is 4096; keep headroom for tags


def split_message(text: str) -> list[str]:
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


def digest_html(ai_text: str) -> str:
    """Escape AI output for HTML parse_mode and bold the source headers."""
    lines = []
    for line in ai_text.splitlines():
        esc = html.escape(line)
        if esc.startswith("📢"):
            esc = f"<b>{esc}</b>"
        lines.append(esc)
    return "\n".join(lines)


async def build_and_send_digest(
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
    full = t("digest_header", lang, date=date_str) + "\n\n" + digest_html(ai_text)
    if ptb_app.bot.username:  # viral share signature
        full += "\n\n" + t("digest_footer", lang, bot=ptb_app.bot.username)
    for chunk in split_message(full):
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
        return await build_and_send_digest(telegram_id, db, channel_ids)
    except Exception as exc:
        logger.warning("On-demand digest failed for user %s: %s", telegram_id, exc)
        return False
    finally:
        db.close()


async def send_daily_digest() -> None:
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
            sent = await build_and_send_digest(tg_id, db)
            if sent:
                logger.info("Daily digest sent to user %s", tg_id)
        except Exception as exc:
            logger.warning("Daily digest failed for user %s: %s", tg_id, exc)
        finally:
            db.close()


async def digest_loop() -> None:
    """Fire daily digest at DIGEST_HOUR_UTC every day."""
    while True:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=config.DIGEST_HOUR_UTC, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        sleep_secs = (target - now).total_seconds()
        logger.info("Next digest in %.0f minutes", sleep_secs / 60)
        await asyncio.sleep(sleep_secs)
        await send_daily_digest()
