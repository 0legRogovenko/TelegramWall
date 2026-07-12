"""Inline keyboard callback handler."""
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user, _guard_pro, _help_text
from src.bot.handlers.general import cmd_trial
from src.bot.keyboards import digest_keyboard, user_channels_keyboard
from src.bot.payments import send_invoice
from src.database import db_session
from src.models import UserChannel


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_help":
        await query.message.reply_text(_help_text(), parse_mode="HTML")

    elif data == "start_trial":
        await cmd_trial(update, context)

    elif data.startswith("subscribe:"):
        tier = data.split(":")[1]
        await send_invoice(update, context, tier=tier)

    elif data == "toggle_auto_summary":
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            if not await _guard_pro(query, user):
                return
            user.auto_summary = not user.auto_summary
            db.commit()
            await query.message.edit_reply_markup(
                reply_markup=digest_keyboard(user.digest_enabled, user.auto_summary)
            )

    elif data == "toggle_digest":
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            if not await _guard_pro(query, user):
                return
            user.digest_enabled = not user.digest_enabled
            db.commit()
            await query.message.edit_reply_markup(
                reply_markup=digest_keyboard(user.digest_enabled, user.auto_summary)
            )

    elif data == "request_digest":
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            if not await _guard_pro(query, user):
                return
        await query.answer("Формирую дайджест…")
        from src.userbot.monitor import send_digest_now
        sent = await send_digest_now(update.effective_user.id)
        if not sent:
            await query.message.reply_text(
                "📰 <b>Нет новых постов</b>\n\nЗа последние 24 часа ничего не поступало.",
                parse_mode="HTML",
            )

    elif data.startswith("toggle_uc:"):
        uc_id = int(data.split(":")[1])
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            uc = db.query(UserChannel).filter_by(id=uc_id, user_id=user.id).first()
            if uc:
                uc.is_active = not uc.is_active
                db.commit()
                ucs = (
                    db.query(UserChannel).filter_by(user_id=user.id)
                    .join(UserChannel.channel).all()
                )
                await query.message.edit_reply_markup(
                    reply_markup=user_channels_keyboard(ucs)
                )

    elif data.startswith("del_uc:"):
        uc_id = int(data.split(":")[1])
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            uc = db.query(UserChannel).filter_by(id=uc_id, user_id=user.id).first()
            if uc:
                db.delete(uc)
                db.commit()
                ucs = (
                    db.query(UserChannel).filter_by(user_id=user.id)
                    .join(UserChannel.channel).all()
                )
                if ucs:
                    await query.message.edit_reply_markup(
                        reply_markup=user_channels_keyboard(ucs)
                    )
                else:
                    await query.message.edit_text(
                        "📋 <b>Каналы удалены</b>\n\n"
                        "Добавьте первый: <code>/add_channel @username</code>",
                        parse_mode="HTML",
                    )
