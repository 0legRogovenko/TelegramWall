"""Inline keyboard callback handler."""
from telegram import Update
from telegram.ext import ContextTypes

from src.bot.handlers.base import (
    _get_or_create_user,
    _guard_pro,
    _help_text,
    _sync_menu_commands,
)
from src.bot.handlers.general import cmd_trial
from src.bot.i18n import LANGS, lang_of, t
from src.bot.keyboards import (
    digest_channels_keyboard,
    digest_keyboard,
    main_menu,
    user_channels_keyboard,
)
from src.bot.payments import send_invoice
from src.database import db_session
from src.models import Post, UserChannel


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("lang:"):
        code = data.split(":")[1]
        if code not in LANGS:
            return
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            user.language = code
            db.commit()
            paid = user.subscription_tier != "free"
            await query.message.edit_text(t("lang_set", code))
            await query.message.reply_text(
                t("start_hint", code), reply_markup=main_menu(paid=paid, lang=code),
            )
            await _sync_menu_commands(context.bot, update.effective_chat.id, user)

    elif data == "show_help":
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            lang = lang_of(user)
        await query.message.reply_text(_help_text(lang), parse_mode="HTML")

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
                reply_markup=digest_keyboard(
                    user.digest_enabled, user.auto_summary, lang_of(user)
                )
            )

    elif data == "toggle_digest":
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            if not await _guard_pro(query, user):
                return
            user.digest_enabled = not user.digest_enabled
            db.commit()
            await query.message.edit_reply_markup(
                reply_markup=digest_keyboard(
                    user.digest_enabled, user.auto_summary, lang_of(user)
                )
            )

    elif data == "request_digest":
        # Step 1 of the AI digest: let the user pick the sources
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            if not await _guard_pro(query, user):
                return
            lang = lang_of(user)
            ucs = (
                db.query(UserChannel)
                .filter_by(user_id=user.id, is_active=True)
                .join(UserChannel.channel)
                .all()
            )
            if not ucs:
                await query.message.reply_text(t("ch_none", lang), parse_mode="HTML")
                return
            pairs = [(uc.channel_id, uc.channel.username) for uc in ucs]
        selected = {cid for cid, _ in pairs}
        context.user_data["dsel"] = selected
        context.user_data["dsel_pairs"] = pairs
        context.user_data["dsel_lang"] = lang
        await query.message.reply_text(
            t("digest_choose", lang), parse_mode="HTML",
            reply_markup=digest_channels_keyboard(pairs, selected, lang),
        )

    elif data.startswith("dsel:"):
        pairs = context.user_data.get("dsel_pairs")
        if not pairs:
            return  # selection expired (restart) — user taps the digest button again
        lang = context.user_data.get("dsel_lang", "ru")
        selected = context.user_data.setdefault("dsel", set())
        cid = int(data.split(":")[1])
        if cid in selected:
            selected.discard(cid)
        else:
            selected.add(cid)
        await query.message.edit_reply_markup(
            reply_markup=digest_channels_keyboard(pairs, selected, lang)
        )

    elif data == "dall":
        pairs = context.user_data.get("dsel_pairs")
        if not pairs:
            return
        lang = context.user_data.get("dsel_lang", "ru")
        selected = context.user_data.setdefault("dsel", set())
        all_ids = {cid for cid, _ in pairs}
        context.user_data["dsel"] = set() if selected == all_ids else set(all_ids)
        await query.message.edit_reply_markup(
            reply_markup=digest_channels_keyboard(pairs, context.user_data["dsel"], lang)
        )

    elif data == "dgo":
        pairs = context.user_data.get("dsel_pairs")
        lang = context.user_data.get("dsel_lang", "ru")
        selected = context.user_data.get("dsel") or set()
        if not pairs:
            return
        if not selected:
            await query.answer(t("digest_no_selection", lang), show_alert=True)
            return
        await query.message.edit_text(t("digest_generating", lang))
        from src.userbot.monitor import send_digest_now
        sent = await send_digest_now(
            update.effective_user.id, channel_ids=list(selected)
        )
        if sent:
            await query.message.delete()
        else:
            await query.message.edit_text(t("digest_empty", lang), parse_mode="HTML")

    elif data.startswith("sum:"):
        # Inline "Summary" button under a delivered post
        post_id = int(data.split(":")[1])
        import asyncio
        from src.services.summarizer import summarize
        with db_session() as db:
            user = _get_or_create_user(db, update.effective_user)
            lang = lang_of(user)
            if not user.can_summary:
                await query.answer(t("pro_only_alert", lang), show_alert=True)
                return
            post = db.query(Post).filter_by(id=post_id).first()
            if not post or not post.text:
                await query.answer(t("sum_no_text", lang), show_alert=True)
                return
            if post.summary:
                await query.message.reply_text(
                    t("sum_header", lang, id=post_id, text=post.summary),
                    parse_mode="HTML",
                )
                return
            msg = await query.message.reply_text(t("sum_generating", lang))
            try:
                summary_text = await asyncio.to_thread(summarize, post.text, lang)
                post.summary = summary_text
                db.commit()
                await msg.edit_text(
                    t("sum_header", lang, id=post_id, text=summary_text),
                    parse_mode="HTML",
                )
            except Exception as exc:
                await msg.edit_text(t("sum_error", lang, err=exc), parse_mode="HTML")

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
            lang = lang_of(user)
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
                        t("ch_deleted_all", lang), parse_mode="HTML",
                    )
