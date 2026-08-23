"""AI features: /summary, /digest, /autosummary."""
import asyncio
import html
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user
from src.bot.i18n import lang_of, t
from src.bot.keyboards import digest_keyboard, subscribe_keyboard
from src.config import config
from src.database import db_session
from src.models import Post, User
from src.services.ai_access import authorized_post, claim_summary_request
from src.services.summarizer import summarize


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        if not context.args:
            key = "sum_usage" if user.can_summary else "sum_unavailable"
            await update.message.reply_text(
                t(key, lang), parse_mode="HTML",
                reply_markup=None if user.can_summary else subscribe_keyboard(lang),
            )
            return

        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("sum_bad_id", lang))
            return

        post = authorized_post(db, user, post_id)
        if not post:
            await update.message.reply_text(
                t("sum_not_found", lang, id=post_id), parse_mode="HTML"
            )
            return
        if not post.text:
            await update.message.reply_text(t("sum_no_text", lang))
            return

        if not user.can_summary:
            user.pending_summary_post_id = post_id
            db.commit()
            await update.message.reply_text(
                t("sum_saved_for_payment", lang, id=post_id),
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return

        if post.summary:
            await update.message.reply_text(
                t("sum_header", lang, id=post_id, text=html.escape(post.summary)),
                parse_mode="HTML",
            )
            return

        if not claim_summary_request(db, user):
            await update.message.reply_text(
                t("sum_quota", lang, limit=config.AI_DAILY_SUMMARY_LIMIT),
                parse_mode="HTML",
            )
            return

        msg = await update.message.reply_text(t("sum_generating", lang))
        try:
            # to_thread: the Anthropic call is blocking — keep the event loop alive
            summary_text = await asyncio.to_thread(summarize, post.text, lang)
            post.summary = summary_text
            db.commit()
            await msg.edit_text(
                t("sum_header", lang, id=post_id, text=html.escape(summary_text)),
                parse_mode="HTML",
            )
        except Exception as exc:
            await msg.edit_text(
                t("sum_error", lang, err=html.escape(str(exc))), parse_mode="HTML"
            )


def _load_pending_summary(
    telegram_id: int,
) -> tuple[int, str, str, str | None] | None:
    """Load and authorize a pending request without leaking ORM objects."""
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if not user or not user.can_summary or not user.pending_summary_post_id:
            return None
        post_id = user.pending_summary_post_id
        post = authorized_post(db, user, post_id)
        if not post or not post.text:
            user.pending_summary_post_id = None
            db.commit()
            return None
        lang = lang_of(user)
        if not post.summary and not claim_summary_request(db, user):
            return None
        return post_id, lang, post.text, post.summary


def _cache_summary(post_id: int, summary_text: str) -> None:
    with db_session() as db:
        post = db.query(Post).filter_by(id=post_id).first()
        if post and not post.summary:
            post.summary = summary_text
            db.commit()


def _clear_pending_summary(telegram_id: int, post_id: int) -> None:
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if user and user.pending_summary_post_id == post_id:
            user.pending_summary_post_id = None
            db.commit()


async def resume_pending_summary(telegram_id: int, bot) -> bool:
    """Finish a summary the user requested before activating AI access."""
    pending = await asyncio.to_thread(_load_pending_summary, telegram_id)
    if pending is None:
        return False
    post_id, lang, post_text, cached_summary = pending

    if cached_summary:
        summary_text = cached_summary
    else:
        try:
            summary_text = await asyncio.to_thread(summarize, post_text, lang)
            await asyncio.to_thread(_cache_summary, post_id, summary_text)
        except Exception:
            return False

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=t(
                "sum_resumed_after_payment", lang, id=post_id,
                text=html.escape(summary_text),
            ),
            parse_mode="HTML",
        )
    except Exception:
        return False

    # Clear only after Telegram accepted the message. A transient send failure
    # must not erase the action the user expected us to resume after payment.
    await asyncio.to_thread(_clear_pending_summary, telegram_id, post_id)
    return True


async def cmd_autosummary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirect to /digest which now controls both autosummary and digest."""
    await cmd_digest(update, context)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        if not user.can_auto_summary:
            await update.message.reply_text(
                t("digest_unavailable", lang), parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return
        await update.message.reply_text(
            t("digest_settings", lang, hour=config.DIGEST_HOUR_UTC),
            parse_mode="HTML",
            reply_markup=digest_keyboard(user.digest_enabled, user.auto_summary, lang),
        )


async def cmd_summary_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /summary_<id> — clickable single-token form of /summary <id>."""
    m = re.match(r"^/summary_(\d+)", update.message.text.strip())
    if not m:
        return
    context.args = [m.group(1)]
    await cmd_summary(update, context)
