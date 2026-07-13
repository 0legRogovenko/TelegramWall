"""Admin-only commands: /admin (Russian only — for the operator)."""
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import SEP
from src.config import config
from src.database import db_session
from src.models import Channel, PendingPost, Post, Subscription, User, UserChannel


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in config.ADMIN_IDS:
        return
    from sqlalchemy import func as sqlfunc
    with db_session() as db:
        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        # Users
        total_users = db.query(User).count()
        new_week = db.query(User).filter(User.created_at >= week_ago).count()
        langs = dict(
            db.query(User.language, sqlfunc.count(User.id)).group_by(User.language).all()
        )
        lang_line = "  ".join(
            f"{code or '—'}: {cnt}" for code, cnt in sorted(langs.items(), key=lambda x: -x[1])
        )
        active_users = (
            db.query(UserChannel.user_id).filter_by(is_active=True).distinct().count()
        )

        # Subscriptions & money
        subs_by_tier = dict(
            db.query(Subscription.tier, sqlfunc.count(Subscription.id))
            .filter(Subscription.expires_at > now, Subscription.stars_paid > 0)
            .group_by(Subscription.tier)
            .all()
        )
        trial_subs = db.query(Subscription).filter(
            Subscription.stars_paid == 0, Subscription.expires_at > now
        ).count()
        revenue = dict(
            db.query(Subscription.payment_currency, sqlfunc.sum(Subscription.stars_paid))
            .filter(Subscription.stars_paid > 0)
            .group_by(Subscription.payment_currency)
            .all()
        )
        rev_parts = []
        for cur, amount in revenue.items():
            if cur == "RUB":
                rev_parts.append(f"{(amount or 0) / 100:.0f} ₽")
            else:
                rev_parts.append(f"{amount or 0} ⭐")
        rev_line = "  ·  ".join(rev_parts) if rev_parts else "0"

        # Content
        total_channels = db.query(Channel).count()
        active_ucs = db.query(UserChannel).filter_by(is_active=True).count()
        total_posts = db.query(Post).count()
        posts_24h = db.query(Post).filter(Post.created_at >= day_ago).count()
        posts_7d = db.query(Post).filter(Post.created_at >= week_ago).count()
        pending = db.query(PendingPost).count()

        top_channels = (
            db.query(Channel.username, sqlfunc.count(UserChannel.id).label("cnt"))
            .join(UserChannel, UserChannel.channel_id == Channel.id)
            .filter(UserChannel.is_active.is_(True))
            .group_by(Channel.username)
            .order_by(sqlfunc.count(UserChannel.id).desc())
            .limit(5)
            .all()
        )
        top_lines = "\n".join(
            f"  {i}. @{name} — {cnt} подписч." for i, (name, cnt) in enumerate(top_channels, 1)
        ) or "  —"

        subs_line = "  ".join(
            f"{tier}: {cnt}" for tier, cnt in subs_by_tier.items()
        ) or "нет"

        await update.message.reply_text(
            "📊 <b>Админ-панель</b>\n\n"
            "<b>Пользователи</b>\n"
            f"  👤 Всего: <b>{total_users}</b>  (+{new_week} за 7 дней)\n"
            f"  ✅ С каналами: <b>{active_users}</b>\n"
            f"  🌐 Языки: {lang_line or '—'}\n\n"
            "<b>Подписки</b>\n"
            f"  💳 Платные: {subs_line}\n"
            f"  🆓 Триалы/бонусы: {trial_subs}\n"
            f"  💰 Выручка: {rev_line}\n"
            f"{SEP}\n"
            "<b>Контент</b>\n"
            f"  📢 Каналов: <b>{total_channels}</b>  ·  подписок: {active_ucs}\n"
            f"  📝 Постов: <b>{total_posts}</b>  (24ч: {posts_24h}, 7д: {posts_7d})\n"
            f"  📦 В очереди на доставку: {pending}\n\n"
            "<b>Топ каналов</b>\n"
            f"{top_lines}",
            parse_mode="HTML",
        )
