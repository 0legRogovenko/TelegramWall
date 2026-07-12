"""Admin-only commands: /admin."""
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import SEP
from src.config import config
from src.database import db_session
from src.models import Channel, Post, Subscription, User, UserChannel


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
