"""General commands: /start, /language, /help, /status, /subscribe, /trial, /refer."""
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import (
    _ensure_referral_code,
    _get_or_create_user,
    _help_text,
    _sync_menu_commands,
)
from src.bot.i18n import lang_of, t, tier_label
from src.bot.keyboards import (
    SEP,
    TIER_ICON,
    language_keyboard,
    main_menu,
    start_keyboard,
    subscribe_keyboard,
    subscription_active_keyboard,
)
from src.config import config
from src.database import db_session
from src.models import Subscription, User, UserChannel


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        await update.message.reply_text(_help_text(lang_of(user)), parse_mode="HTML")


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        t("lang_choose", "ru"), reply_markup=language_keyboard()
    )


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
                        text=t("ref_bonus", lang_of(referrer),
                               days=config.REFERRAL_BONUS_DAYS),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        # First contact: ask for the language before anything else
        if not user.language:
            await update.message.reply_text(
                t("lang_choose", "ru"), reply_markup=language_keyboard()
            )
            return

        lang = lang_of(user)
        if tier != "free":
            expires = user.active_subscription.expires_at.strftime("%d.%m.%Y")
            await update.message.reply_text(
                t("start_paid", lang, name=name, icon=TIER_ICON.get(tier, ""),
                  label=tier_label(tier, lang), expires=expires, sep=SEP),
                parse_mode="HTML", reply_markup=main_menu(paid=True, lang=lang),
            )
        else:
            await update.message.reply_text(
                t("start_free", lang, name=name, sep=SEP),
                parse_mode="HTML", reply_markup=start_keyboard(lang),
            )
            await update.message.reply_text(
                t("start_hint", lang), reply_markup=main_menu(paid=False, lang=lang),
            )
        await _sync_menu_commands(context.bot, update.effective_chat.id, user)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        tier = user.subscription_tier
        lang = lang_of(user)

        if tier != "free":
            expires = user.active_subscription.expires_at.strftime("%d.%m.%Y")
            limit = user.channel_limit
            limit_str = "∞" if limit is None else str(limit)

            features = ""
            if user.can_summary:
                features += t("status_f_summary", lang)
            features += t(
                "status_f_auto_ok" if user.can_auto_summary else "status_f_auto_pro", lang
            )
            await update.message.reply_text(
                t("status_paid", lang, icon=TIER_ICON.get(tier, ""),
                  label=tier_label(tier, lang), expires=expires, limit=limit_str,
                  sep=SEP, features=features),
                parse_mode="HTML",
                reply_markup=subscription_active_keyboard(tier, lang),
            )
        else:
            active_count = (
                db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
            )
            await update.message.reply_text(
                t("status_free", lang, count=active_count,
                  limit=config.CHANNEL_LIMIT_FREE, sep=SEP),
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from src.bot.payments import price_label
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
    await update.message.reply_text(
        t("subscribe", lang, sep=SEP,
          free_limit=config.CHANNEL_LIMIT_FREE, basic_limit=config.CHANNEL_LIMIT_BASIC,
          basic_price=price_label("basic"), pro_price=price_label("pro")),
        parse_mode="HTML",
        reply_markup=subscribe_keyboard(lang),
    )


async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        if user.trial_used:
            await update.message.reply_text(
                t("trial_used", lang), parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return
        if user.has_subscription:
            await update.message.reply_text(
                t("trial_have_sub", lang), parse_mode="HTML",
            )
            return

        expires_at = datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS)
        db.add(Subscription(user_id=user.id, tier="pro", stars_paid=0, expires_at=expires_at))
        user.trial_used = True
        db.commit()
        await update.message.reply_text(
            t("trial_ok", lang, days=config.TRIAL_DAYS,
              date=expires_at.strftime("%d.%m.%Y"), sep=SEP),
            parse_mode="HTML",
            reply_markup=main_menu(paid=True, lang=lang),
        )
        await _sync_menu_commands(context.bot, update.effective_chat.id, user)


async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        code = _ensure_referral_code(db, user)
        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{code}"
        await update.message.reply_text(
            t("refer", lang, days=config.REFERRAL_BONUS_DAYS, link=link, sep=SEP),
            parse_mode="HTML",
        )
