"""AI features: /summary, /digest, /autosummary."""
import asyncio
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user
from src.bot.keyboards import digest_keyboard, subscribe_keyboard
from src.config import config
from src.database import db_session
from src.models import Post
from src.services.summarizer import summarize


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_summary:
            await update.message.reply_text(
                "📝 <b>Саммари недоступно</b>\n\n"
                "Доступно на тарифах Basic ⭐ и Pro 💎.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return

        if not context.args:
            await update.message.reply_text(
                "📝 Использование: <code>/summary &lt;ID поста&gt;</code>\n\n"
                "ID указан под каждым постом.",
                parse_mode="HTML",
            )
            return

        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ ID поста должен быть числом.")
            return

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            await update.message.reply_text(
                f"❌ Пост <b>#{post_id}</b> не найден.", parse_mode="HTML"
            )
            return
        if not post.text:
            await update.message.reply_text("❌ В этом посте нет текста для саммари.")
            return

        if post.summary:
            await update.message.reply_text(
                f"📝 <b>Саммари #{post_id}</b>\n\n{post.summary}",
                parse_mode="HTML",
            )
            return

        msg = await update.message.reply_text("⏳ Генерирую саммари…")
        try:
            # to_thread: the Anthropic call is blocking — keep the event loop alive
            summary_text = await asyncio.to_thread(summarize, post.text)
            post.summary = summary_text
            db.commit()
            await msg.edit_text(
                f"📝 <b>Саммари #{post_id}</b>\n\n{summary_text}",
                parse_mode="HTML",
            )
        except Exception as exc:
            await msg.edit_text(f"❌ <b>Ошибка:</b> {exc}", parse_mode="HTML")


async def cmd_autosummary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirect to /digest which now controls both autosummary and digest."""
    await cmd_digest(update, context)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_auto_summary:
            await update.message.reply_text(
                "📰 <b>AI-режим недоступен</b>\n\n"
                "Авто-саммари и дайджест доступны на тарифе Pro 💎.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
        await update.message.reply_text(
            "📰 <b>AI-режим</b>\n\n"
            "  <b>Авто-саммари</b> — краткое изложение с каждым постом\n"
            f"  <b>Дайджест</b> — сводка за день в {config.DIGEST_HOUR_UTC:02d}:00 UTC\n\n"
            "Настройте кнопками ниже:",
            parse_mode="HTML",
            reply_markup=digest_keyboard(user.digest_enabled, user.auto_summary),
        )


async def cmd_summary_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /summary_<id> — clickable single-token form of /summary <id>."""
    m = re.match(r"^/summary_(\d+)", update.message.text.strip())
    if not m:
        return
    context.args = [m.group(1)]
    await cmd_summary(update, context)
