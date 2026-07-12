"""Channel management: /add_channel, /channels, /remove_channel, /filter."""
import asyncio
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_channel, _get_or_create_user
from src.bot.keyboards import SEP, TIER_LABEL, subscribe_keyboard, user_channels_keyboard
from src.database import db_session
from src.models import Channel, UserChannel


async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "📢 <b>Добавление канала</b>\n\n"
            "Пример: <code>/add_channel @durov</code>",
            parse_mode="HTML",
        )
        return

    raw = context.args[0]
    if not raw.startswith("@"):
        await update.message.reply_text(
            "❌ <b>Канал указывается через @</b>\n\n"
            "Пример: <code>/add_channel @durov</code>",
            parse_mode="HTML",
        )
        return

    username = raw.lstrip("@").lower()
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)

        active_count = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
        limit = user.channel_limit
        if limit is not None and active_count >= limit:
            tier = user.subscription_tier
            upgrade = "Оформите подписку:" if tier == "free" else "Перейдите на Pro 💎:"
            await update.message.reply_text(
                f"❌ <b>Лимит каналов</b>\n\n"
                f"  {TIER_LABEL.get(tier, tier)} — до <b>{limit}</b> каналов.\n\n"
                f"{upgrade}",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return

        channel = _get_or_create_channel(db, username)
        existing = db.query(UserChannel).filter_by(
            user_id=user.id, channel_id=channel.id
        ).first()

        if existing:
            if existing.is_active:
                await update.message.reply_text(
                    f"ℹ️ Канал <b>@{username}</b> уже в вашем списке.", parse_mode="HTML"
                )
            else:
                existing.is_active = True
                db.commit()
                await update.message.reply_text(
                    f"✅ <b>@{username} включён</b>\n\nПосты снова будут приходить сюда.",
                    parse_mode="HTML",
                )
            return

        db.add(UserChannel(user_id=user.id, channel_id=channel.id))
        db.commit()
        await update.message.reply_text(
            f"✅ <b>@{username} добавлен</b>\n\n"
            "Новые посты будут приходить сюда.\n\n"
            f"💡 Настроить фильтр: <code>/filter @{username}</code>",
            parse_mode="HTML",
        )

        from src.bot.app import _loop
        from src.userbot.monitor import refresh_channels
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(refresh_channels(), _loop)


async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        ucs = (
            db.query(UserChannel)
            .filter_by(user_id=user.id)
            .join(UserChannel.channel)
            .all()
        )
        if not ucs:
            await update.message.reply_text(
                "📋 <b>Каналы не добавлены</b>\n\n"
                "Добавьте первый:\n<code>/add_channel @username</code>",
                parse_mode="HTML",
            )
            return

        limit = user.channel_limit
        limit_str = "∞" if limit is None else str(limit)
        active = sum(1 for uc in ucs if uc.is_active)
        filter_lines = "\n".join(
            f"  /filter_@{uc.channel.username}" for uc in ucs
        )
        await update.message.reply_text(
            f"📋 <b>Мои каналы</b>  <i>{active} / {limit_str}</i>\n\n"
            "<b>Как управлять:</b>\n"
            "  • Нажмите на канал в списке ниже — "
            "включить ✅ / выключить ⏸ доставку постов\n"
            "  • Нажмите 🗑 рядом с каналом — удалить его\n"
            "  • 🔍 — стоит у каналов с фильтром слов\n"
            "  • 🤖 — стоит у каналов с AI-фильтром\n\n"
            "<b>Настроить фильтр канала:</b>\n"
            f"{filter_lines}",
            parse_mode="HTML",
            reply_markup=user_channels_keyboard(ucs),
        )


async def cmd_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/remove_channel @username</code>", parse_mode="HTML"
        )
        return

    username = context.args[0].lstrip("@").lower()
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(
                f"❌ Канал <b>@{username}</b> не найден.", parse_mode="HTML"
            )
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(
                f"ℹ️ Канал <b>@{username}</b> не в вашем списке.", parse_mode="HTML"
            )
            return
        db.delete(uc)
        db.commit()
        await update.message.reply_text(f"🗑 <b>@{username} удалён</b>", parse_mode="HTML")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unified filter: keyword and AI.

    /filter @channel              — show current filters
    /filter @channel word1 word2  — set keyword filter
    /filter @channel ai topic     — set AI relevance filter (Basic/Pro)
    /filter @channel off          — remove keyword filter
    /filter @channel ai off       — remove AI filter
    """
    if not context.args:
        await update.message.reply_text(
            "🔍 <b>Фильтр постов</b>\n\n"
            "<b>Ключевые слова</b> — пропускать только нужные:\n"
            "  <code>/filter @channel слово1 слово2</code>\n"
            "  <code>/filter @channel off</code> — убрать\n\n"
            "<b>AI по теме</b> <i>(Basic / Pro)</i> — фильтр по смыслу:\n"
            "  <code>/filter @channel ai только про экономику</code>\n"
            "  <code>/filter @channel ai off</code> — убрать\n\n"
            "Посмотреть: <code>/filter @channel</code>",
            parse_mode="HTML",
        )
        return

    username = context.args[0].lstrip("@").lower()
    rest = context.args[1:]

    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(
                f"❌ Канал <b>@{username}</b> не найден. Сначала добавьте его.",
                parse_mode="HTML",
            )
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(
                f"ℹ️ Канал <b>@{username}</b> не в вашем списке.", parse_mode="HTML"
            )
            return

        # Show current filters
        if not rest:
            kw_line = (
                f"  📝 Слова: <code>{uc.keywords}</code>"
                if uc.keywords else "  📝 Слова: —"
            )
            ai_line = (
                f"  🤖 AI: <code>{uc.ai_filter}</code>"
                if uc.ai_filter else "  🤖 AI: —"
            )
            await update.message.reply_text(
                f"🔍 <b>Фильтры @{username}</b>\n\n{kw_line}\n{ai_line}\n\n"
                f"{SEP}\n"
                "<b>Настроить:</b>\n"
                f"<code>/filter @{username} слово1 слово2</code> — фильтр слов\n"
                f"<code>/filter @{username} ai тема</code> — AI-фильтр <i>(Basic+)</i>\n\n"
                "<b>Убрать:</b>\n"
                f"<code>/filter @{username} off</code> — слова\n"
                f"<code>/filter @{username} ai off</code> — AI",
                parse_mode="HTML",
            )
            return

        # AI filter branch
        if rest[0].lower() == "ai":
            if not user.can_summary:
                await update.message.reply_text(
                    "🤖 <b>AI-фильтр</b>\n\n"
                    "Доступен на тарифах Basic ⭐ и Pro 💎.",
                    parse_mode="HTML",
                    reply_markup=subscribe_keyboard(),
                )
                return
            ai_args = rest[1:]
            if not ai_args:
                if uc.ai_filter:
                    await update.message.reply_text(
                        f"🤖 <b>AI-фильтр @{username}</b>\n\n"
                        f"  <code>{uc.ai_filter}</code>\n\n"
                        f"<code>/filter @{username} ai off</code> — удалить",
                        parse_mode="HTML",
                    )
                else:
                    await update.message.reply_text(
                        f"🤖 AI-фильтр для <b>@{username}</b> не задан.\n\n"
                        f"Пример: <code>/filter @{username} ai только про экономику</code>",
                        parse_mode="HTML",
                    )
                return
            if ai_args[0].lower() == "off":
                uc.ai_filter = None
                db.commit()
                await update.message.reply_text(
                    f"✅ AI-фильтр <b>@{username}</b> удалён.", parse_mode="HTML"
                )
            else:
                uc.ai_filter = " ".join(ai_args)
                db.commit()
                await update.message.reply_text(
                    f"✅ <b>AI-фильтр установлен</b>\n\n"
                    f"  @{username} → <code>{uc.ai_filter}</code>",
                    parse_mode="HTML",
                )
            return

        # Keyword filter branch
        if rest[0].lower() == "off":
            uc.keywords = None
            db.commit()
            await update.message.reply_text(
                f"✅ Фильтр слов <b>@{username}</b> удалён.", parse_mode="HTML"
            )
        else:
            uc.keywords = ", ".join(rest)
            db.commit()
            await update.message.reply_text(
                f"✅ <b>Фильтр установлен</b>\n\n"
                f"  @{username} → <code>{uc.keywords}</code>\n\n"
                "Приходят только посты с этими словами.",
                parse_mode="HTML",
            )


async def cmd_aifilter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias: /aifilter @ch topic → /filter @ch ai topic."""
    if context.args:
        context.args = [context.args[0], "ai"] + context.args[1:]
    await cmd_filter(update, context)


async def cmd_filter_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /filter_@<channel> — shows the channel's filters and how to set them."""
    m = re.match(r"^/filter_@?(\w+)", update.message.text.strip())
    if not m:
        return
    context.args = [m.group(1)]
    await cmd_filter(update, context)
