"""Bookmarks and per-user statistics: /save, /unsave, /saved, /stats."""
import html
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_user
from src.bot.i18n import lang_of, plural_posts, t
from src.bot.keyboards import SEP
from src.database import db_session
from src.models import Bookmark, Channel, Post, UserChannel


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)

        if not context.args:
            await update.message.reply_text(t("save_usage", lang), parse_mode="HTML")
            return
        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("save_bad_id", lang))
            return

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            await update.message.reply_text(
                t("sum_not_found", lang, id=post_id), parse_mode="HTML"
            )
            return
        if db.query(Bookmark).filter_by(user_id=user.id, post_id=post_id).first():
            await update.message.reply_text(
                t("save_already", lang, id=post_id), parse_mode="HTML"
            )
            return
        db.add(Bookmark(user_id=user.id, post_id=post_id))
        db.commit()
        await update.message.reply_text(
            t("save_done", lang, id=post_id), parse_mode="HTML"
        )


async def cmd_unsave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)

        if not context.args:
            await update.message.reply_text(t("unsave_usage", lang), parse_mode="HTML")
            return
        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text(t("save_bad_id", lang))
            return

        bm = db.query(Bookmark).filter_by(user_id=user.id, post_id=post_id).first()
        if not bm:
            await update.message.reply_text(
                t("unsave_not_in", lang, id=post_id), parse_mode="HTML"
            )
            return
        db.delete(bm)
        db.commit()
        await update.message.reply_text(
            t("unsave_done", lang, id=post_id), parse_mode="HTML"
        )


async def cmd_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        bookmarks = (
            db.query(Bookmark)
            .filter_by(user_id=user.id)
            .order_by(Bookmark.created_at.desc())
            .limit(10)
            .all()
        )
        if not bookmarks:
            await update.message.reply_text(t("saved_empty", lang), parse_mode="HTML")
            return

        n = len(bookmarks)
        lines = [t("saved_header", lang, n=n, p=plural_posts(n, lang))]
        for bm in bookmarks:
            post = bm.post
            ch_name = post.channel.username if post.channel else "?"
            date_str = bm.created_at.strftime("%d.%m  %H:%M") if bm.created_at else ""
            if post.text:
                preview = html.escape(post.text[:120])
                if len(post.text) > 120:
                    preview += "…"
            else:
                preview = t("media_stub", lang)
            lines.append(
                f"{SEP}\n"
                f"📢 <b>@{ch_name}</b>  <i>{date_str}</i>\n"
                f"{preview}\n"
                f"/summary_{post.id}  ·  <code>/unsave {post.id}</code>"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


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

        lines = [t("stats_body", lang, channels=len(ucs), total=total_posts,
                   week=week_posts, bookmarks=bookmarks_count)]
        if top:
            lines += ["", SEP, t("stats_top", lang)]
            medals = ["🥇", "🥈", "🥉"]
            for i, (username, cnt) in enumerate(top):
                lines.append(f"  {medals[i]} @{username} — {cnt} {plural_posts(cnt, lang)}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
