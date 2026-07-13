"""Shared helpers for all handler modules."""
import secrets

from telegram import BotCommand, BotCommandScopeChat

from src.bot.i18n import lang_of, t
from src.bot.keyboards import SEP
from src.bot.payments import price_label
from src.config import config
from src.models import Channel, User


def _free_commands(lang: str) -> list[BotCommand]:
    return [
        BotCommand("start",       t("cmd_start", lang)),
        BotCommand("channels",    t("cmd_channels", lang)),
        BotCommand("add_channel", t("cmd_add", lang)),
        BotCommand("subscribe",   t("cmd_subscribe", lang)),
        BotCommand("stats",       t("cmd_stats", lang)),
        BotCommand("refer",       t("cmd_refer", lang)),
        BotCommand("language",    t("cmd_language", lang)),
        BotCommand("help",        t("cmd_help", lang)),
    ]


async def _sync_menu_commands(bot, chat_id: int, user) -> None:
    """Per-chat command menu in the user's language; free users see base commands."""
    lang = lang_of(user)
    cmds = _free_commands(lang)
    if user.can_summary:
        cmds[3:3] = [
            BotCommand("summary", t("cmd_summary", lang)),
            BotCommand("filter",  t("cmd_filter", lang)),
        ]
    if user.can_auto_summary:
        cmds[5:5] = [BotCommand("digest", t("cmd_digest", lang))]
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id))
    except Exception:
        pass  # menu sync is cosmetic — never break the main flow


async def _guard_pro(query, user) -> bool:
    """Return True if user has Pro; send an alert and return False otherwise."""
    if not user.can_auto_summary:
        await query.answer(t("pro_only_alert", lang_of(user)), show_alert=True)
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


def _help_text(lang: str = "ru") -> str:
    return t(
        "help", lang,
        sep=SEP,
        free_limit=config.CHANNEL_LIMIT_FREE,
        basic_limit=config.CHANNEL_LIMIT_BASIC,
        basic_price=price_label("basic"),
        pro_price=price_label("pro"),
        ref_days=config.REFERRAL_BONUS_DAYS,
    )
