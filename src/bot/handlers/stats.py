"""Per-user statistics: /stats."""
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user
from src.bot.i18n import lang_of, plural_posts, t
from src.bot.keyboards import SEP
from src.database import db_session
from src.models import Channel, Post, UserChannel


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from sqlalchemy import func as sqlfunc
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        ucs = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).all()

        if not ucs:
            await update.message.reply_text(t("stats_empty", lang), parse_mode="HTML")
            return

        channel_ids = [uc.channel_id for uc in ucs]
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        total_posts = db.query(Post).filter(Post.channel_id.in_(channel_ids)).count()
        week_posts = db.query(Post).filter(
            Post.channel_id.in_(channel_ids), Post.created_at >= week_ago
        ).count()

        top = (
            db.query(Channel.username, sqlfunc.count(Post.id).label("cnt"))
            .join(Post, Post.channel_id == Channel.id)
            .filter(Post.channel_id.in_(channel_ids), Post.created_at >= week_ago)
            .group_by(Channel.username)
            .order_by(sqlfunc.count(Post.id).desc())
            .limit(3)
            .all()
        )

        lines = [t("stats_body", lang, channels=len(ucs), total=total_posts, week=week_posts)]
        if top:
            lines += ["", SEP, t("stats_top", lang)]
            medals = ["🥇", "🥈", "🥉"]
            for i, (username, cnt) in enumerate(top):
                lines.append(f"  {medals[i]} @{username} — {cnt} {plural_posts(cnt, lang)}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
