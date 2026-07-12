"""Shared helpers for all handler modules."""
import secrets

from telegram import BotCommand, BotCommandScopeChat

from src.bot.keyboards import SEP
from src.bot.payments import price_label
from src.config import config
from src.models import Channel, User

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
