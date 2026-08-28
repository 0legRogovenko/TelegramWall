"""Telegram media classification, limits, transfer, and file ID reuse."""
import html
import io
import logging
import re

from telethon import TelegramClient
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from src.config import config

logger = logging.getLogger(__name__)

MEDIA_CACHE_MAX = 500
MEDIA_FILE_IDS: dict[int, str] = {}
CAPTION_TEXT_BUDGET = 900

__all__ = [
    "MEDIA_CACHE_MAX",
    "MEDIA_FILE_IDS",
    "cache_file_id",
    "caption_fits",
    "caption_text_budget",
    "download_media_bytes",
    "get_media_type",
    "is_file_error",
    "media_filename",
    "media_size_ok",
    "send_media",
]


def caption_text_budget(header: str, post_id: int) -> int:
    """How many text chars fit into the caption next to this header.

    Telegram counts visible (parsed) characters, so tags are stripped.
    A margin absorbs entity unescaping and emoji counting as two UTF-16 units.
    """
    visible_header = html.unescape(re.sub(r"<[^>]+>", "", header))
    overhead = len(visible_header) + len(f"#{post_id}") + 4
    return min(CAPTION_TEXT_BUDGET, 1024 - overhead - 20)


def caption_fits(caption: str) -> bool:
    """Whether an HTML caption fits Telegram's visible-character limit."""
    visible = html.unescape(re.sub(r"<[^>]+>", "", caption))
    return len(visible) <= 1000


def get_media_type(message) -> str | None:
    """Classify only the message's own attachment, not a web preview."""
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
    return None


def media_filename(message) -> str | None:
    """Return the original filename for re-uploaded documents."""
    media = getattr(message, "media", None)
    attrs = getattr(getattr(media, "document", None), "attributes", None) or []
    for attr in attrs:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def media_size_ok(msg) -> bool:
    size = getattr(getattr(msg, "file", None), "size", None)
    if size is None:
        return True
    return size <= config.MEDIA_MAX_MB * 1024 * 1024


async def download_media_bytes(
    client: TelegramClient, msg,
) -> io.BytesIO | None:
    """Fetch media via the userbot, returning None on any failure."""
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


async def send_media(
    tg_id: int,
    media_kind: str,
    media,
    caption: str,
    reply_markup=None,
    filename: str | None = None,
):
    """Send one media message via the bot and return the PTB Message."""
    from src.bot.app import ptb_app

    if hasattr(media, "seek"):
        media.seek(0)
    kwargs = dict(
        chat_id=tg_id,
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
        read_timeout=120,
        write_timeout=120,
        connect_timeout=30,
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


def is_file_error(exc: Exception) -> bool:
    """Return whether a failure concerns the file rather than recipient."""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "file",
            "wrong file identifier",
            "media",
            "caption",
            "photo",
            "document",
        )
    )


def cache_file_id(post_id: int, sent) -> None:
    """Remember the Bot API file_id from the first upload for reuse."""
    try:
        file_id = None
        if sent.photo:
            file_id = sent.photo[-1].file_id
        elif sent.video:
            file_id = sent.video.file_id
        elif sent.audio:
            file_id = sent.audio.file_id
        elif sent.document:
            file_id = sent.document.file_id
        if file_id:
            if len(MEDIA_FILE_IDS) >= MEDIA_CACHE_MAX:
                MEDIA_FILE_IDS.pop(next(iter(MEDIA_FILE_IDS)))
            MEDIA_FILE_IDS[post_id] = file_id
    except Exception:
        pass
