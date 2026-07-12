"""Reply keyboard buttons and free-text input flow."""
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.ai import cmd_digest, cmd_summary
from src.bot.handlers.base import _get_or_create_user
from src.bot.handlers.channels import cmd_add_channel, cmd_channels
from src.bot.keyboards import subscribe_keyboard
from src.database import db_session


async def btn_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_channels(update, context)


async def btn_add_channel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_channel"] = True
    await update.message.reply_text(
        "📢 Введите username канала через @:\n<i>Например: @durov</i>",
        parse_mode="HTML",
    )


async def btn_summary_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_summary:
            await update.message.reply_text(
                "📝 <b>Саммари недоступно</b>\n\nДоступно на тарифах Basic ⭐ и Pro 💎.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
    context.user_data["awaiting_summary_id"] = True
    await update.message.reply_text(
        "📝 Введите ID поста:\n<i>ID указан под каждым сообщением от бота.</i>",
        parse_mode="HTML",
    )


async def btn_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_digest(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if context.user_data.get("awaiting_channel"):
        if not text.startswith("@"):
            await update.message.reply_text(
                "❌ <b>Канал указывается через @</b>\n\n"
                "Попробуйте ещё раз. Например: <code>@durov</code>",
                parse_mode="HTML",
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
            await update.message.reply_text(
                "❌ ID поста должен быть числом. Попробуйте ещё раз."
            )
            return
        context.args = [text]
        await cmd_summary(update, context)
