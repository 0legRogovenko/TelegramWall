"""AI features: /summary, /digest, /autosummary."""
import asyncio
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user
from src.bot.i18n import lang_of, t
from src.bot.keyboards import digest_keyboard, subscribe_keyboard
from src.config import config
from src.database import db_session
from src.models import Post
from src.services.summarizer import summarize


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        if not user.can_summary:
            await update.message.reply_text(
                t("sum_unavailable", lang), parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return

        if not context.args:
            await update.message.reply_text(t("sum_usage", lang), parse_mode="HTML")
            return

        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("sum_bad_id", lang))
            return

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            await update.message.reply_text(
                t("sum_not_found", lang, id=post_id), parse_mode="HTML"
            )
            return
        if not post.text:
            await update.message.reply_text(t("sum_no_text", lang))
            return

        if post.summary:
            await update.message.reply_text(
                t("sum_header", lang, id=post_id, text=post.summary),
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
                t("sum_header", lang, id=post_id, text=summary_text),
                parse_mode="HTML",
            )
        except Exception as exc:
            await msg.edit_text(t("sum_error", lang, err=exc), parse_mode="HTML")


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
