"""Channel management: /add_channel, /channels, /remove_channel, /filter."""
import asyncio
import re

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import _get_or_create_channel, _get_or_create_user
from src.bot.i18n import lang_of, t, tier_label
from src.bot.keyboards import subscribe_keyboard, user_channels_keyboard
from src.database import db_session
from src.models import Channel, UserChannel

_LINK_RE = re.compile(r"^(?:https?://)?t(?:elegram)?\.me/(@?[A-Za-z0-9_]{3,})/?$", re.I)


def _normalize_channel(raw: str) -> str | None:
    """Accept '@name' or a t.me link; return the bare username or None."""
    raw = raw.strip()
    m = _LINK_RE.match(raw)
    if m:
        raw = "@" + m.group(1).lstrip("@")
    if not raw.startswith("@"):
        return None
    return raw.lstrip("@").lower()


async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)

        if not context.args:
            await update.message.reply_text(t("add_usage", lang), parse_mode="HTML")
            return

        username = _normalize_channel(context.args[0])
        if not username:
            await update.message.reply_text(t("add_need_at", lang), parse_mode="HTML")
            return

        active_count = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
        limit = user.channel_limit
        if limit is not None and active_count >= limit:
            tier = user.subscription_tier
            upgrade = t("add_upgrade_free" if tier == "free" else "add_upgrade_pro", lang)
            await update.message.reply_text(
                t("add_limit", lang, label=tier_label(tier, lang),
                  limit=limit, upgrade=upgrade),
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return

        channel = _get_or_create_channel(db, username)
        existing = db.query(UserChannel).filter_by(
            user_id=user.id, channel_id=channel.id
        ).first()

        if existing:
            if existing.is_active:
                await update.message.reply_text(
                    t("add_already", lang, username=username), parse_mode="HTML"
                )
            else:
                existing.is_active = True
                db.commit()
                await update.message.reply_text(
                    t("add_reenabled", lang, username=username), parse_mode="HTML"
                )
            return

        db.add(UserChannel(user_id=user.id, channel_id=channel.id))
        db.commit()
        await update.message.reply_text(
            t("add_done", lang, username=username), parse_mode="HTML"
        )

        from src.bot.app import _loop
        from src.userbot.monitor import refresh_channels
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(refresh_channels(), _loop)


async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)
        ucs = (
            db.query(UserChannel)
            .filter_by(user_id=user.id)
            .join(UserChannel.channel)
            .all()
        )
        if not ucs:
            await update.message.reply_text(t("ch_none", lang), parse_mode="HTML")
            return

        limit = user.channel_limit
        limit_str = "∞" if limit is None else str(limit)
        active = sum(1 for uc in ucs if uc.is_active)
        filter_lines = "\n".join(
            f"  /filter_@{uc.channel.username}" for uc in ucs
        )
        await update.message.reply_text(
            t("ch_list", lang, active=active, limit=limit_str, filters=filter_lines),
            parse_mode="HTML",
            reply_markup=user_channels_keyboard(ucs),
        )


async def cmd_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)

        if not context.args:
            await update.message.reply_text(t("rm_usage", lang), parse_mode="HTML")
            return

        username = context.args[0].lstrip("@").lower()
        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(
                t("rm_not_found", lang, username=username), parse_mode="HTML"
            )
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(
                t("rm_not_in_list", lang, username=username), parse_mode="HTML"
            )
            return
        db.delete(uc)
        db.commit()
        await update.message.reply_text(
            t("rm_done", lang, username=username), parse_mode="HTML"
        )


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """AI relevance filter (Basic/Pro).

    /filter @channel        — show the current filter
    /filter @channel topic  — deliver only posts matching the topic
    /filter @channel off    — remove the filter
    """
    with db_session() as db:
        user = _get_or_create_user(db, update.effective_user)
        lang = lang_of(user)

        if not context.args:
            await update.message.reply_text(t("flt_help", lang), parse_mode="HTML")
            return

        username = context.args[0].lstrip("@").lower()
        rest = context.args[1:]

        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(
                t("flt_ch_not_found", lang, username=username), parse_mode="HTML"
            )
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(
                t("rm_not_in_list", lang, username=username), parse_mode="HTML"
            )
            return

        # Legacy form "/filter @ch ai ..." — drop the "ai" word
        if rest and rest[0].lower() == "ai":
            rest = rest[1:]

        # Show current filter
        if not rest:
            ai = f"<code>{uc.ai_filter}</code>" if uc.ai_filter else "—"
            await update.message.reply_text(
                t("flt_show", lang, username=username, ai=ai),
                parse_mode="HTML",
            )
            return

        # Removing a filter never requires a subscription
        if rest[0].lower() == "off":
            uc.ai_filter = None
            db.commit()
            await update.message.reply_text(
                t("flt_ai_removed", lang, username=username), parse_mode="HTML"
            )
            return

        if not user.can_summary:
            await update.message.reply_text(
                t("flt_ai_pro", lang), parse_mode="HTML",
                reply_markup=subscribe_keyboard(lang),
            )
            return

        uc.ai_filter = " ".join(rest)
        db.commit()
        await update.message.reply_text(
            t("flt_ai_set", lang, username=username, ai=uc.ai_filter),
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
