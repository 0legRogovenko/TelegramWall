"""Reply keyboard buttons and free-text input flow."""
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.ai import cmd_digest, cmd_summary
from src.bot.handlers.base import _get_or_create_user
from src.bot.handlers.channels import _normalize_channel, cmd_add_channel, cmd_channels
from src.bot.i18n import lang_of, t
from src.bot.keyboards import subscribe_keyboard
from src.database import db_session


async def btn_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_channels(update, context)


async def btn_add_channel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
    context.user_data["awaiting_channel"] = True
    await update.message.reply_text(t("prompt_channel", lang), parse_mode="HTML")


async def btn_summary_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        if not user.can_summary:
            await update.message.reply_text(
                t("sum_unavailable", lang), parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return
    context.user_data["awaiting_summary_id"] = True
    await update.message.reply_text(t("prompt_sum_id", lang), parse_mode="HTML")


async def btn_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_digest(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if context.user_data.get("awaiting_channel"):
        if not _normalize_channel(text):
            with db_session() as db:
                user = _get_or_create_user(db, update.effective_user)
                lang = lang_of(user)
            await update.message.reply_text(
                t("prompt_channel_retry", lang), parse_mode="HTML",
            )
            return  # keep awaiting_channel so the user can retry
        context.user_data["awaiting_channel"] = False
        context.args = [text]
        await cmd_add_channel(update, context)
    elif context.user_data.get("awaiting_summary_id"):
        context.user_data["awaiting_summary_id"] = False
        try:
            int(text)
        except ValueError:
            with db_session() as db:
                user = _get_or_create_user(db, update.effective_user)
                lang = lang_of(user)
            await update.message.reply_text(t("prompt_sum_id_retry", lang))
            return
        context.args = [text]
        await cmd_summary(update, context)
