"""General commands: /start, /help, /status, /subscribe, /trial, /refer, /quiet."""
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import (
    _ensure_referral_code,
    _get_or_create_user,
    _help_text,
    _sync_menu_commands,
)
from src.bot.keyboards import (
    SEP,
    TIER_ICON,
    TIER_LABEL,
    main_menu,
    start_keyboard,
    subscribe_keyboard,
    subscription_active_keyboard,
)
from src.bot.payments import price_label
from src.config import config
from src.database import db_session
from src.models import Subscription, User, UserChannel


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
