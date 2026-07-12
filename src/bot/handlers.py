"""Bot command handlers."""
import asyncio
import html
import re
import secrets
from datetime import datetime, timedelta, timezone

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import ContextTypes

from src.bot.keyboards import (
    SEP,
    TIER_ICON,
    TIER_LABEL,
    digest_keyboard,
    main_menu,
    start_keyboard,
    subscribe_keyboard,
    subscription_active_keyboard,
    user_channels_keyboard,
)
from src.bot.payments import price_label, send_invoice
from src.config import config
from src.database import db_session
from src.models import Bookmark, Channel, Post, Subscription, User, UserChannel
from src.services.summarizer import summarize


# ── Helpers ───────────────────────────────────────────────────────────────────

_FREE_COMMANDS = [
    BotCommand("start",       "Начало работы"),
    BotCommand("channels",    "Мои каналы"),
    BotCommand("add_channel", "Добавить канал"),
    BotCommand("subscribe",   "Тарифы и подписка"),
    BotCommand("help",        "Все команды"),
]

_BASIC_EXTRA = [
    BotCommand("summary", "Саммари поста по ID"),
    BotCommand("filter",  "Фильтр для канала"),
]

_PRO_EXTRA = [
    BotCommand("digest", "AI-режим: дайджест и авто-саммари"),
]


async def _sync_menu_commands(bot, chat_id: int, user) -> None:
    """Per-chat command menu: free users see only base commands."""
    cmds = list(_FREE_COMMANDS)
    if user.can_summary:
        cmds = cmds[:3] + _BASIC_EXTRA + cmds[3:]
    if user.can_auto_summary:
        cmds = cmds[:5] + _PRO_EXTRA + cmds[5:]
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id))
    except Exception:
        pass  # menu sync is cosmetic — never break the main flow


async def _guard_pro(query, user) -> bool:
    """Return True if user has Pro; send an alert and return False otherwise."""
    if not user.can_auto_summary:
        await query.answer("❌ Доступно только на Pro 💎", show_alert=True)
        return False
    return True


def _get_or_create_user(db, tg_user) -> User:
    user = db.query(User).filter_by(telegram_id=tg_user.id).first()
    if not user:
        user = User(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _get_or_create_channel(db, username: str) -> Channel:
    channel = db.query(Channel).filter_by(username=username).first()
    if not channel:
        channel = Channel(username=username)
        db.add(channel)
        db.commit()
        db.refresh(channel)
    return channel


def _ensure_referral_code(db, user: User) -> str:
    if not user.referral_code:
        user.referral_code = secrets.token_hex(4)
        db.commit()
    return user.referral_code


def _help_text() -> str:
    return (
        "📖 <b>TelegramWall — быстрый старт</b>\n\n"

        "<b>Каналы</b>\n"
        "  <code>/add_channel @username</code> — добавить\n"
        "  <code>/channels</code> — список и управление\n\n"

        "<b>Фильтры</b>\n"
        "  <code>/filter @channel слово</code> — по ключевым словам\n"
        "  <code>/filter @channel ai тема</code> — по смыслу <i>(Basic+)</i>\n\n"

        "<b>AI-саммари</b>\n"
        "  <code>/summary_ID</code> — краткий пересказ поста\n"
        "  <code>/digest</code> — авто-саммари и дайджест <i>(Pro)</i>\n\n"

        "<b>Закладки</b>\n"
        "  <code>/save ID</code> — сохранить  ·  <code>/saved</code> — список\n\n"

        "<b>Комфорт</b>\n"
        "  <code>/quiet 23 9</code> — тишина с 23:00 до 09:00 UTC\n"
        "  <code>/stats</code> — ваша статистика\n\n"

        f"{SEP}\n"
        "<b>Тарифы</b>\n"
        f"  Free — до {config.CHANNEL_LIMIT_FREE} каналов\n"
        f"  ⭐ Basic — до {config.CHANNEL_LIMIT_BASIC} каналов + саммари\n"
        f"    {price_label('basic')} / мес\n"
        "  💎 Pro — ∞ каналов + авто-саммари + дайджест\n"
        f"    {price_label('pro')} / мес\n\n"
        "<code>/trial</code> — 3 дня Pro бесплатно\n"
        f"<code>/refer</code> — пригласить → +{config.REFERRAL_BONUS_DAYS} дней"
    )


# ── General commands ──────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_help_text(), parse_mode="HTML")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        name = update.effective_user.first_name
        tier = user.subscription_tier

        # Handle referral link (?start=ref_XXXX)
        if context.args and context.args[0].startswith("ref_") and not user.referred_by:
            ref_code = context.args[0][4:]
            referrer = db.query(User).filter_by(referral_code=ref_code).first()
            if referrer and referrer.id != user.id:
                user.referred_by = referrer.id
                bonus_expires = (
                    datetime.now(timezone.utc) + timedelta(days=config.REFERRAL_BONUS_DAYS)
                )
                db.add(Subscription(
                    user_id=referrer.id, tier="basic",
                    stars_paid=0, expires_at=bonus_expires,
                ))
                db.commit()
                try:
                    from src.bot.app import ptb_app
                    await ptb_app.bot.send_message(
                        chat_id=referrer.telegram_id,
                        text=(
                            f"🎁 <b>+{config.REFERRAL_BONUS_DAYS} дней Basic!</b>\n\n"
                            "По вашей реферальной ссылке зарегистрировался новый пользователь."
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        if tier != "free":
            expires = user.active_subscription.expires_at.strftime("%d.%m.%Y")
            icon = TIER_ICON.get(tier, "")
            label = TIER_LABEL.get(tier, tier)
            text = (
                f"👋 <b>Привет, {name}!</b>\n\n"
                f"{icon} <b>{label}</b> активна до <b>{expires}</b>\n\n"
                f"{SEP}\n"
                "<code>/channels</code> — мои каналы\n"
                "<code>/add_channel @username</code> — добавить\n"
                "<code>/summary_ID</code> — саммари поста"
            )
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=main_menu(paid=True)
            )
        else:
            text = (
                f"👋 <b>Привет, {name}!</b>\n\n"
                "<b>TelegramWall</b> следит за Telegram-каналами вместо вас — "
                "новые посты приходят прямо в этот чат.\n\n"
                f"{SEP}\n"
                "Начните с добавления канала:\n"
                "<code>/add_channel @username</code>"
            )
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=start_keyboard(),
            )
            await update.message.reply_text(
                "Кнопки управления внизу 👇", reply_markup=main_menu(paid=False),
            )
        await _sync_menu_commands(context.bot, update.effective_chat.id, user)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        tier = user.subscription_tier

        if tier != "free":
            expires = user.active_subscription.expires_at.strftime("%d.%m.%Y")
            icon = TIER_ICON.get(tier, "")
            label = TIER_LABEL.get(tier, tier)
            limit = user.channel_limit
            limit_str = "∞" if limit is None else str(limit)

            lines = [
                "📋 <b>Статус аккаунта</b>\n",
                f"{icon} <b>{label}</b>",
                f"  📅 Активна до <b>{expires}</b>",
                f"  📢 Каналов: до {limit_str}",
                SEP,
            ]
            if user.can_summary:
                lines.append("  📝 Саммари по запросу ✅")
            if user.can_auto_summary:
                lines.append("  🤖 Авто-саммари ✅")
                lines.append("  📰 Дайджест ✅")
            else:
                lines.append("  🤖 Авто-саммари — <i>только Pro</i>")
                lines.append("  📰 Дайджест — <i>только Pro</i>")

            if user.quiet_start is not None:
                lines += [
                    SEP,
                    f"  🔕 Тихий режим: {user.quiet_start:02d}:00–{user.quiet_end:02d}:00 UTC",
                ]

            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=subscription_active_keyboard(tier),
            )
        else:
            active_count = (
                db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
            )
            await update.message.reply_text(
                "📋 <b>Статус аккаунта</b>\n\n"
                "  Тариф: Free\n"
                f"  📢 Каналов: <b>{active_count} / {config.CHANNEL_LIMIT_FREE}</b>\n"
                "  📝 Саммари: недоступно\n\n"
                f"{SEP}\n"
                "🎁 Попробуйте Pro бесплатно: <code>/trial</code>",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💳 <b>Тарифы TelegramWall</b>\n\n"
        f"  Free — до {config.CHANNEL_LIMIT_FREE} каналов, без AI\n\n"
        f"{SEP}\n"
        f"  ⭐ <b>Basic</b> — {price_label('basic')} / 30 дней\n"
        f"    до {config.CHANNEL_LIMIT_BASIC} каналов · саммари по запросу\n\n"
        f"  💎 <b>Pro</b> — {price_label('pro')} / 30 дней\n"
        "    ∞ каналов · авто-саммари · дайджест\n\n"
        "  📅 Годовые тарифы — скидка 20%\n\n"
        f"{SEP}\n"
        "🆓 Попробовать бесплатно: <code>/trial</code>",
        parse_mode="HTML",
        reply_markup=subscribe_keyboard(),
    )


async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        if user.trial_used:
            await update.message.reply_text(
                "⚠️ <b>Пробный период уже использован</b>\n\n"
                "Оформите подписку, чтобы продолжить:",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
        if user.has_subscription:
            await update.message.reply_text(
                "ℹ️ У вас уже есть активная подписка.\n\n"
                "Используйте <code>/status</code> для просмотра.",
                parse_mode="HTML",
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS)
        db.add(Subscription(user_id=user.id, tier="pro", stars_paid=0, expires_at=expires_at))
        user.trial_used = True
        db.commit()
        await update.message.reply_text(
            f"🎉 <b>Pro 💎 активирован!</b>\n\n"
            f"  Срок: {config.TRIAL_DAYS} дня\n"
            f"  До: <b>{expires_at.strftime('%d.%m.%Y')}</b>\n\n"
            f"{SEP}\n"
            "Теперь доступно:\n"
            "  📰 Дайджест и авто-саммари — <code>/digest</code>\n"
            "  📢 Неограниченно каналов",
            parse_mode="HTML",
            reply_markup=main_menu(paid=True),
        )
        await _sync_menu_commands(context.bot, update.effective_chat.id, user)


async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        code = _ensure_referral_code(db, user)
        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{code}"
        await update.message.reply_text(
            "🔗 <b>Реферальная программа</b>\n\n"
            f"За каждого нового пользователя по вашей ссылке — "
            f"<b>+{config.REFERRAL_BONUS_DAYS} дней</b> Basic.\n\n"
            f"{SEP}\n"
            "Ваша ссылка:\n"
            f"<code>{link}</code>",
            parse_mode="HTML",
        )


# ── Channel management ────────────────────────────────────────────────────────

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
            await update.message.reply_text(f"❌ Канал <b>@{username}</b> не найден.", parse_mode="HTML")
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(f"ℹ️ Канал <b>@{username}</b> не в вашем списке.", parse_mode="HTML")
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


# ── Quiet mode ────────────────────────────────────────────────────────────────

async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)

        if not context.args:
            if user.quiet_start is not None:
                await update.message.reply_text(
                    f"🔕 <b>Тихий режим активен</b>\n\n"
                    f"  С {user.quiet_start:02d}:00 до {user.quiet_end:02d}:00 UTC\n\n"
                    "<code>/quiet off</code> — выключить",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    "🔔 <b>Тихий режим выключен</b>\n\n"
                    "Включить: <code>/quiet ЧЧ ЧЧ</code>\n"
                    "Пример: <code>/quiet 23 9</code> — тишина с 23:00 до 09:00 UTC",
                    parse_mode="HTML",
                )
            return

        if context.args[0].lower() == "off":
            user.quiet_start = None
            user.quiet_end = None
            db.commit()
            await update.message.reply_text(
                "🔔 <b>Тихий режим выключен</b>\n\nПосты приходят в любое время.",
                parse_mode="HTML",
            )
            return

        if len(context.args) < 2:
            await update.message.reply_text(
                "Укажите два часа: <code>/quiet ЧЧ ЧЧ</code>\n"
                "Пример: <code>/quiet 23 9</code>",
                parse_mode="HTML",
            )
            return

        try:
            qs, qe = int(context.args[0]), int(context.args[1])
            if not (0 <= qs <= 23 and 0 <= qe <= 23):
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Часы должны быть числами от 0 до 23.")
            return

        user.quiet_start, user.quiet_end = qs, qe
        db.commit()
        await update.message.reply_text(
            f"🔕 <b>Тихий режим включён</b>\n\n"
            f"  С {qs:02d}:00 до {qe:02d}:00 UTC\n\n"
            "<code>/quiet off</code> — выключить",
            parse_mode="HTML",
        )


# ── Summary & digest ──────────────────────────────────────────────────────────

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
            summary_text = summarize(post.text)
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


# ── Admin ─────────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in config.ADMIN_IDS:
        return
    with db_session() as db:
        now = datetime.now(timezone.utc)
        total_users = db.query(User).count()
        active_subs = db.query(Subscription).filter(Subscription.expires_at > now).count()
        trial_subs = db.query(Subscription).filter(
            Subscription.stars_paid == 0, Subscription.expires_at > now
        ).count()
        total_channels = db.query(Channel).count()
        total_posts = db.query(Post).count()
        active_ucs = db.query(UserChannel).filter_by(is_active=True).count()

        await update.message.reply_text(
            "📊 <b>Статистика</b>\n\n"
            f"  👤 Пользователей: <b>{total_users}</b>\n"
            f"  ⭐ Активных подписок: <b>{active_subs}</b> (триал: {trial_subs})\n\n"
            f"{SEP}\n"
            f"  📢 Каналов в базе: <b>{total_channels}</b>\n"
            f"  🔗 Подписок на каналы: <b>{active_ucs}</b>\n"
            f"  📝 Постов: <b>{total_posts}</b>",
            parse_mode="HTML",
        )


# ── Callback query handler ────────────────────────────────────────────────────

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


# ── Bookmarks ─────────────────────────────────────────────────────────────────

async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "📌 Использование: <code>/save &lt;ID поста&gt;</code>",
            parse_mode="HTML",
        )
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом.")
        return

    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            await update.message.reply_text(
                f"❌ Пост <b>#{post_id}</b> не найден.", parse_mode="HTML"
            )
            return
        if db.query(Bookmark).filter_by(user_id=user.id, post_id=post_id).first():
            await update.message.reply_text(
                f"ℹ️ Пост <b>#{post_id}</b> уже в закладках.", parse_mode="HTML"
            )
            return
        db.add(Bookmark(user_id=user.id, post_id=post_id))
        db.commit()
        await update.message.reply_text(
            f"📌 <b>Пост #{post_id} сохранён</b>\n\n"
            "Посмотреть все: <code>/saved</code>",
            parse_mode="HTML",
        )


async def cmd_unsave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Использование: <code>/unsave &lt;ID поста&gt;</code>", parse_mode="HTML"
        )
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID должен быть числом.")
        return

    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        bm = db.query(Bookmark).filter_by(user_id=user.id, post_id=post_id).first()
        if not bm:
            await update.message.reply_text(
                f"ℹ️ Пост <b>#{post_id}</b> не в закладках.", parse_mode="HTML"
            )
            return
        db.delete(bm)
        db.commit()
        await update.message.reply_text(
            f"🗑 Пост <b>#{post_id}</b> удалён из закладок.", parse_mode="HTML"
        )


async def cmd_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        bookmarks = (
            db.query(Bookmark)
            .filter_by(user_id=user.id)
            .order_by(Bookmark.created_at.desc())
            .limit(10)
            .all()
        )
        if not bookmarks:
            await update.message.reply_text(
                "📌 <b>Закладки пусты</b>\n\n"
                "Сохраняйте посты командой <code>/save ID</code>",
                parse_mode="HTML",
            )
            return

        n = len(bookmarks)
        lines = [f"📌 <b>Закладки</b> — {n} {'пост' if n == 1 else 'постов'}"]
        for bm in bookmarks:
            post = bm.post
            ch_name = post.channel.username if post.channel else "?"
            date_str = bm.created_at.strftime("%d.%m  %H:%M") if bm.created_at else ""
            if post.text:
                preview = html.escape(post.text[:120])
                if len(post.text) > 120:
                    preview += "…"
            else:
                preview = "<i>[медиа]</i>"
            lines.append(
                f"{SEP}\n"
                f"📢 <b>@{ch_name}</b>  <i>{date_str}</i>\n"
                f"{preview}\n"
                f"/summary_{post.id}  ·  <code>/unsave {post.id}</code>"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


# ── Statistics ────────────────────────────────────────────────────────────────

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import func as sqlfunc
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        ucs = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).all()

        if not ucs:
            await update.message.reply_text(
                "📊 <b>Статистика</b>\n\n"
                "Каналы ещё не добавлены.\n\n"
                "<code>/add_channel @username</code> — начать",
                parse_mode="HTML",
            )
            return

        channel_ids = [uc.channel_id for uc in ucs]
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        total_posts = db.query(Post).filter(Post.channel_id.in_(channel_ids)).count()
        week_posts = db.query(Post).filter(
            Post.channel_id.in_(channel_ids), Post.created_at >= week_ago
        ).count()
        bookmarks_count = db.query(Bookmark).filter_by(user_id=user.id).count()

        top = (
            db.query(Channel.username, sqlfunc.count(Post.id).label("cnt"))
            .join(Post, Post.channel_id == Channel.id)
            .filter(Post.channel_id.in_(channel_ids), Post.created_at >= week_ago)
            .group_by(Channel.username)
            .order_by(sqlfunc.count(Post.id).desc())
            .limit(3)
            .all()
        )

        lines = [
            "📊 <b>Ваша статистика</b>",
            "",
            f"  📢 Каналов: <b>{len(ucs)}</b>",
            f"  📝 Постов всего: <b>{total_posts}</b>",
            f"  📅 За 7 дней: <b>{week_posts}</b>",
            f"  📌 Закладок: <b>{bookmarks_count}</b>",
        ]
        if top:
            lines += ["", SEP, "<b>Топ каналов за неделю:</b>"]
            medals = ["🥇", "🥈", "🥉"]
            for i, (username, cnt) in enumerate(top):
                lines.append(f"  {medals[i]} @{username} — {cnt} постов")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── Deprecated aliases (kept for backward compatibility) ─────────────────────

async def cmd_aifilter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Alias: /aifilter @ch topic → /filter @ch ai topic."""
    if context.args:
        context.args = [context.args[0], "ai"] + context.args[1:]
    await cmd_filter(update, context)


# ── Clickable command links (/summary_21, /filter_@channel) ─────────────────

async def cmd_summary_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /summary_<id> — clickable single-token form of /summary <id>."""
    m = re.match(r"^/summary_(\d+)", update.message.text.strip())
    if not m:
        return
    context.args = [m.group(1)]
    await cmd_summary(update, context)


async def cmd_filter_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /filter_@<channel> — shows the channel's filters and how to set them."""
    m = re.match(r"^/filter_@?(\w+)", update.message.text.strip())
    if not m:
        return
    context.args = [m.group(1)]
    await cmd_filter(update, context)


# ── Reply keyboard button handlers ───────────────────────────────────────────

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
