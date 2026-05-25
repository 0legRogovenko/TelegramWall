"""Bot command handlers."""
import secrets
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.bot.keyboards import (
    BUTTON_ADD_CHANNEL,
    BUTTON_CHANNELS,
    BUTTON_DIGEST,
    BUTTON_SUMMARY,
    SEP,
    TIER_ICON,
    TIER_LABEL,
    auto_summary_keyboard,
    digest_keyboard,
    main_menu,
    start_keyboard,
    subscribe_keyboard,
    subscription_active_keyboard,
    user_channels_keyboard,
)
from src.bot.payments import send_invoice
from src.config import config
from src.database import get_session
from src.models import Channel, Post, Subscription, User, UserChannel
from src.services.summarizer import summarize


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
        f"<b>📖 Как пользоваться TelegramWall</b>\n\n"

        f"<b>1. Добавьте каналы</b>\n"
        f"/add_channel @username\n"
        f"Новые посты начнут приходить сюда автоматически.\n\n"

        f"<b>2. Управляйте каналами</b>\n"
        f"/channels — список с кнопками вкл/выкл/удалить\n"
        f"/filter @channel слово — фильтр по ключевым словам\n\n"

        f"<b>3. AI-саммари</b>\n"
        f"/summary ID — краткое изложение конкретного поста\n"
        f"/autosummary — авто-саммари каждого нового поста <i>(Pro)</i>\n"
        f"/digest — ежедневная сводка постов <i>(Pro)</i>\n\n"

        f"<b>4. Комфорт</b>\n"
        f"/quiet 23 9 — не беспокоить с 23:00 до 09:00 UTC\n\n"

        f"{SEP}\n"
        f"<b>Тарифы</b>\n\n"
        f"  Free — до {config.CHANNEL_LIMIT_FREE} каналов\n"
        f"⭐ Basic — до {config.CHANNEL_LIMIT_BASIC} каналов + саммари\n"
        f"     {config.SUBSCRIPTION_PRICE_BASIC_STARS} Stars/мес\n"
        f"💎 Pro — ∞ каналов + авто-саммари + дайджест\n"
        f"     {config.SUBSCRIPTION_PRICE_PRO_STARS} Stars/мес\n\n"
        f"/trial — 3 дня Pro бесплатно\n"
        f"/refer — пригласить друга (+{config.REFERRAL_BONUS_DAYS} дней за каждого)"
    )


# ── General commands ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        name = update.effective_user.first_name
        tier = user.subscription_tier

        # Handle referral
        if context.args and context.args[0].startswith("ref_") and not user.referred_by:
            ref_code = context.args[0][4:]
            referrer = db.query(User).filter_by(referral_code=ref_code).first()
            if referrer and referrer.id != user.id:
                user.referred_by = referrer.id
                bonus_expires = datetime.now(timezone.utc) + timedelta(days=config.REFERRAL_BONUS_DAYS)
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
                            f"🎁 <b>Новый реферал!</b>\n\n"
                            f"По вашей ссылке зарегистрировался пользователь.\n"
                            f"+{config.REFERRAL_BONUS_DAYS} дней Basic начислено."
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
                f"👋 Привет, {name}!\n\n"
                f"{icon} <b>{label}</b> активна до {expires}\n\n"
                f"{SEP}\n"
                f"Ваши каналы: /channels\n"
                f"Добавить канал: /add_channel @username\n"
                f"Саммари поста: /summary ID\n"
                f"Статус: /status"
            )
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())
        else:
            text = (
                f"👋 Привет, {name}!\n\n"
                f"<b>TelegramWall</b> — агрегатор Telegram-каналов.\n"
                f"Добавляйте каналы, и новые посты будут приходить прямо сюда.\n\n"
                f"{SEP}\n"
                f"Начните с команды:\n"
                f"/add_channel @username"
            )
            await update.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=start_keyboard(),
            )
            await update.message.reply_text(
                reply_markup=main_menu(),
                text="Кнопки управления внизу 👇",
            )
    finally:
        db.close()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        tier = user.subscription_tier

        if tier != "free":
            expires = user.active_subscription.expires_at.strftime("%d.%m.%Y")
            icon = TIER_ICON.get(tier, "")
            label = TIER_LABEL.get(tier, tier)
            limit = user.channel_limit
            limit_str = "∞" if limit is None else str(limit)

            lines = [
                f"{icon} <b>{label}</b>",
                f"",
                f"📅 Активна до <b>{expires}</b>",
                f"{SEP}",
                f"📢 Каналов: до {limit_str}",
            ]
            if user.can_summary:
                lines.append("📝 Саммари по запросу ✅")
            if user.can_auto_summary:
                lines.append("🤖 Авто-саммари ✅")
                lines.append("📰 Дайджест ✅")
            else:
                lines.append("🤖 Авто-саммари — <i>только Pro</i>")
                lines.append("📰 Дайджест — <i>только Pro</i>")

            if user.quiet_start is not None:
                lines += [SEP, f"🔕 Тихий режим: {user.quiet_start:02d}:00–{user.quiet_end:02d}:00 UTC"]

            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="HTML",
                reply_markup=subscription_active_keyboard(tier),
            )
        else:
            active_count = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
            await update.message.reply_text(
                f"<b>Статус подписки</b>\n\n"
                f"Тариф: Free\n"
                f"📢 Каналов: {active_count} / {config.CHANNEL_LIMIT_FREE}\n"
                f"📝 Саммари: недоступно\n\n"
                f"{SEP}\n"
                f"🆓 Попробуйте Pro бесплатно — /trial",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
    finally:
        db.close()


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"<b>Тарифы TelegramWall</b>\n\n"
        f"  <b>Free</b> — до {config.CHANNEL_LIMIT_FREE} каналов, без AI\n\n"
        f"⭐ <b>Basic</b> — {config.SUBSCRIPTION_PRICE_BASIC_STARS} Stars / 30 дней\n"
        f"   до {config.CHANNEL_LIMIT_BASIC} каналов + саммари по запросу\n\n"
        f"💎 <b>Pro</b> — {config.SUBSCRIPTION_PRICE_PRO_STARS} Stars / 30 дней\n"
        f"   ∞ каналов + авто-саммари + дайджест\n\n"
        f"📅 Годовые тарифы со скидкой 20% тоже доступны.\n\n"
        f"🆓 Начните бесплатно: /trial",
        parse_mode="HTML",
        reply_markup=subscribe_keyboard(),
    )


async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        if user.trial_used:
            await update.message.reply_text(
                f"<b>Пробный период недоступен</b>\n\n"
                f"Вы уже использовали бесплатный период.\n"
                f"Оформите подписку, чтобы продолжить:",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
        if user.has_subscription:
            await update.message.reply_text(
                "У вас уже есть активная подписка.",
                parse_mode="HTML",
            )
            return
        expires_at = datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS)
        db.add(Subscription(user_id=user.id, tier="pro", stars_paid=0, expires_at=expires_at))
        user.trial_used = True
        db.commit()
        await update.message.reply_text(
            f"🎉 <b>Pro 💎 активирован!</b>\n\n"
            f"Пробный период: {config.TRIAL_DAYS} дня\n"
            f"Действует до: {expires_at.strftime('%d.%m.%Y')}\n\n"
            f"{SEP}\n"
            f"Теперь доступно:\n"
            f"🤖 Авто-саммари — /autosummary\n"
            f"📰 Дайджест — /digest\n"
            f"📢 Неограниченно каналов",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
    finally:
        db.close()


async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        code = _ensure_referral_code(db, user)
        bot_info = await context.bot.get_me()
        link = f"https://t.me/{bot_info.username}?start=ref_{code}"
        await update.message.reply_text(
            f"<b>🔗 Реферальная программа</b>\n\n"
            f"Поделитесь ссылкой — за каждого нового пользователя "
            f"вы получите <b>+{config.REFERRAL_BONUS_DAYS} дней</b> Basic подписки.\n\n"
            f"{SEP}\n"
            f"Ваша ссылка:\n"
            f"<code>{link}</code>",
            parse_mode="HTML",
        )
    finally:
        db.close()


# ── Channel management ────────────────────────────────────────────────────────

async def cmd_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "<b>Добавление канала</b>\n\n"
            "Использование: /add_channel @username\n"
            "Пример: /add_channel @durov",
            parse_mode="HTML",
        )
        return

    username = context.args[0].lstrip("@").lower()

    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)

        active_count = db.query(UserChannel).filter_by(user_id=user.id, is_active=True).count()
        limit = user.channel_limit
        if limit is not None and active_count >= limit:
            tier = user.subscription_tier
            upgrade = "Оформите подписку:" if tier == "free" else "Перейдите на Pro 💎:"
            await update.message.reply_text(
                f"❌ <b>Лимит каналов достигнут</b>\n\n"
                f"Ваш тариф: {TIER_LABEL.get(tier, tier)} — до {limit} каналов\n\n"
                f"{upgrade}",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return

        channel = _get_or_create_channel(db, username)
        existing = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()

        if existing:
            if existing.is_active:
                await update.message.reply_text(f"Канал @{username} уже в вашем списке.")
            else:
                existing.is_active = True
                db.commit()
                await update.message.reply_text(
                    f"✅ <b>@{username} включён</b>\n\nПосты снова будут приходить сюда.",
                    parse_mode="HTML",
                )
            return

        uc = UserChannel(user_id=user.id, channel_id=channel.id)
        db.add(uc)
        db.commit()
        await update.message.reply_text(
            f"✅ <b>@{username} добавлен</b>\n\n"
            f"Новые посты будут приходить сюда.\n"
            f"Фильтр по словам: /filter @{username} слово",
            parse_mode="HTML",
        )

        from src.userbot.monitor import refresh_channels
        from src.bot.app import _loop
        import asyncio
        if _loop is not None:
            asyncio.run_coroutine_threadsafe(refresh_channels(), _loop)

    finally:
        db.close()


async def cmd_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        ucs = (
            db.query(UserChannel)
            .filter_by(user_id=user.id)
            .join(UserChannel.channel)
            .all()
        )
        limit = user.channel_limit
        limit_str = "∞" if limit is None else str(limit)
        if not ucs:
            await update.message.reply_text(
                "<b>Каналы не добавлены</b>\n\n"
                "Добавьте первый канал:\n/add_channel @username",
                parse_mode="HTML",
            )
            return
        active = sum(1 for uc in ucs if uc.is_active)
        await update.message.reply_text(
            f"<b>📋 Мои каналы</b>\n\n"
            f"Активных: {active} / {limit_str}\n\n"
            f"✅ вкл  ⏸ выкл  🔍 фильтр  🗑 удалить",
            parse_mode="HTML",
            reply_markup=user_channels_keyboard(ucs),
        )
    finally:
        db.close()


async def cmd_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Использование: /remove_channel @username",
        )
        return

    username = context.args[0].lstrip("@").lower()
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(f"Канал @{username} не найден.")
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(f"Канал @{username} не в вашем списке.")
            return
        db.delete(uc)
        db.commit()
        await update.message.reply_text(
            f"🗑 <b>@{username} удалён</b>",
            parse_mode="HTML",
        )
    finally:
        db.close()


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "<b>🔍 Фильтр по ключевым словам</b>\n\n"
            "Установить: /filter @channel слово1 слово2\n"
            "Посмотреть: /filter @channel\n"
            "Убрать: /filter @channel off",
            parse_mode="HTML",
        )
        return

    username = context.args[0].lstrip("@").lower()
    keywords_args = context.args[1:]

    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        channel = db.query(Channel).filter_by(username=username).first()
        if not channel:
            await update.message.reply_text(f"Канал @{username} не найден. Сначала добавьте его.")
            return
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        if not uc:
            await update.message.reply_text(f"Канал @{username} не в вашем списке.")
            return

        if not keywords_args:
            if uc.keywords:
                await update.message.reply_text(
                    f"<b>🔍 Фильтр @{username}</b>\n\n"
                    f"<code>{uc.keywords}</code>\n\n"
                    f"Убрать: /filter @{username} off",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    f"<b>🔍 Фильтр @{username}</b>\n\nНе установлен — приходят все посты.",
                    parse_mode="HTML",
                )
            return

        if keywords_args[0].lower() == "off":
            uc.keywords = None
            db.commit()
            await update.message.reply_text(
                f"✅ <b>Фильтр @{username} удалён</b>\n\nТеперь приходят все посты.",
                parse_mode="HTML",
            )
        else:
            uc.keywords = ", ".join(keywords_args)
            db.commit()
            await update.message.reply_text(
                f"✅ <b>Фильтр @{username} установлен</b>\n\n"
                f"<code>{uc.keywords}</code>\n\n"
                f"Приходят только посты с этими словами.",
                parse_mode="HTML",
            )
    finally:
        db.close()


# ── Quiet mode ────────────────────────────────────────────────────────────────

async def cmd_quiet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)

        if not context.args:
            if user.quiet_start is not None:
                await update.message.reply_text(
                    f"<b>🔕 Тихий режим активен</b>\n\n"
                    f"Посты не приходят с {user.quiet_start:02d}:00 до {user.quiet_end:02d}:00 UTC\n\n"
                    f"Выключить: /quiet off",
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(
                    "<b>🔔 Тихий режим выключен</b>\n\n"
                    "Включить: /quiet ЧЧ ЧЧ\n"
                    "Пример: /quiet 23 9 — тишина с 23:00 до 09:00 UTC",
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
            await update.message.reply_text("Укажите два часа: /quiet ЧЧ ЧЧ\nПример: /quiet 23 9")
            return

        try:
            qs, qe = int(context.args[0]), int(context.args[1])
            if not (0 <= qs <= 23 and 0 <= qe <= 23):
                raise ValueError
        except ValueError:
            await update.message.reply_text("Часы должны быть числами от 0 до 23.")
            return

        user.quiet_start, user.quiet_end = qs, qe
        db.commit()
        await update.message.reply_text(
            f"🔕 <b>Тихий режим включён</b>\n\n"
            f"Посты не будут приходить с {qs:02d}:00 до {qe:02d}:00 UTC\n\n"
            f"Выключить: /quiet off",
            parse_mode="HTML",
        )
    finally:
        db.close()


# ── Summary commands ──────────────────────────────────────────────────────────

async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_summary:
            await update.message.reply_text(
                "<b>📝 Саммари недоступно</b>\n\n"
                "Функция доступна на тарифах Basic и Pro.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return

        if not context.args:
            await update.message.reply_text(
                "Использование: /summary &lt;ID поста&gt;\n"
                "ID поста указан в каждом сообщении от бота.",
                parse_mode="HTML",
            )
            return

        try:
            post_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❌ ID поста должен быть числом.")
            return

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            await update.message.reply_text(f"❌ Пост #{post_id} не найден.")
            return
        if not post.text:
            await update.message.reply_text("❌ В этом посте нет текста для саммари.")
            return

        if post.summary:
            await update.message.reply_text(
                f"📝 <b>Саммари #{post_id}</b>\n\n{post.summary}",
                parse_mode="HTML",
            )
            return

        msg = await update.message.reply_text("⏳ Генерирую саммари…")
        try:
            summary_text = summarize(post.text)
            post.summary = summary_text
            db.commit()
            await msg.edit_text(
                f"📝 <b>Саммари #{post_id}</b>\n\n{summary_text}",
                parse_mode="HTML",
            )
        except Exception as exc:
            await msg.edit_text(f"❌ <b>Ошибка генерации</b>\n\n{exc}", parse_mode="HTML")
    finally:
        db.close()


async def cmd_autosummary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_auto_summary:
            note = (
                "\n\nВаш тариф Basic включает саммари только по запросу (/summary ID)."
                if user.can_summary else ""
            )
            await update.message.reply_text(
                f"<b>🤖 Авто-саммари недоступно</b>\n\n"
                f"Функция доступна на тарифе Pro 💎.{note}",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
        status = "включено ✅" if user.auto_summary else "выключено ❌"
        await update.message.reply_text(
            f"<b>🤖 Авто-саммари</b>\n\n"
            f"Статус: {status}\n\n"
            f"При включении каждый новый пост сопровождается кратким изложением.",
            parse_mode="HTML",
            reply_markup=auto_summary_keyboard(user.auto_summary),
        )
    finally:
        db.close()


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_auto_summary:
            await update.message.reply_text(
                "<b>📰 Дайджест недоступен</b>\n\n"
                "Функция доступна на тарифе Pro 💎.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
        status = "включён ✅" if user.digest_enabled else "выключен ❌"
        await update.message.reply_text(
            f"<b>📰 Ежедневный дайджест</b>\n\n"
            f"Статус: {status}\n"
            f"Время: {config.DIGEST_HOUR_UTC:02d}:00 UTC каждый день\n\n"
            f"Или получите прямо сейчас 👇",
            parse_mode="HTML",
            reply_markup=digest_keyboard(user.digest_enabled),
        )
    finally:
        db.close()


# ── Admin ─────────────────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in config.ADMIN_IDS:
        return
    db = get_session()
    try:
        now = datetime.now(timezone.utc)
        total_users    = db.query(User).count()
        active_subs    = db.query(Subscription).filter(Subscription.expires_at > now).count()
        trial_subs     = db.query(Subscription).filter(
            Subscription.stars_paid == 0, Subscription.expires_at > now
        ).count()
        total_channels = db.query(Channel).count()
        total_posts    = db.query(Post).count()
        active_ucs     = db.query(UserChannel).filter_by(is_active=True).count()

        await update.message.reply_text(
            f"<b>📊 Статистика</b>\n\n"
            f"👤 Пользователей: {total_users}\n"
            f"⭐ Активных подписок: {active_subs} (триал: {trial_subs})\n"
            f"{SEP}\n"
            f"📢 Каналов в базе: {total_channels}\n"
            f"🔗 Активных подписок на каналы: {active_ucs}\n"
            f"📝 Постов в базе: {total_posts}",
            parse_mode="HTML",
        )
    finally:
        db.close()


# ── Callback query handler ────────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_help":
        await query.message.reply_text(_help_text(), parse_mode="HTML")

    elif data == "start_trial":
        await cmd_trial(update, context)

    elif data.startswith("subscribe:"):
        tier = data.split(":")[1]
        await send_invoice(update, context, tier=tier)

    elif data == "toggle_auto_summary":
        db = get_session()
        try:
            user = _get_or_create_user(db, update.effective_user)
            if not user.can_auto_summary:
                await query.message.edit_text(
                    "<b>🤖 Авто-саммари недоступно</b>\n\nФункция доступна на тарифе Pro 💎.",
                    parse_mode="HTML",
                    reply_markup=subscribe_keyboard(),
                )
                return
            user.auto_summary = not user.auto_summary
            db.commit()
            status = "включено ✅" if user.auto_summary else "выключено ❌"
            await query.message.edit_text(
                f"<b>🤖 Авто-саммари</b>\n\nСтатус: {status}",
                parse_mode="HTML",
                reply_markup=auto_summary_keyboard(user.auto_summary),
            )
        finally:
            db.close()

    elif data == "toggle_digest":
        db = get_session()
        try:
            user = _get_or_create_user(db, update.effective_user)
            if not user.can_auto_summary:
                await query.message.edit_text(
                    "<b>📰 Дайджест недоступен</b>\n\nФункция доступна на тарифе Pro 💎.",
                    parse_mode="HTML",
                    reply_markup=subscribe_keyboard(),
                )
                return
            user.digest_enabled = not user.digest_enabled
            db.commit()
            status = "включён ✅" if user.digest_enabled else "выключен ❌"
            await query.message.edit_text(
                f"<b>📰 Ежедневный дайджест</b>\n\nСтатус: {status}",
                parse_mode="HTML",
                reply_markup=digest_keyboard(user.digest_enabled),
            )
        finally:
            db.close()

    elif data == "request_digest":
        db = get_session()
        try:
            user = _get_or_create_user(db, update.effective_user)
            if not user.can_auto_summary:
                await query.answer("❌ Дайджест доступен только на Pro 💎", show_alert=True)
                return
        finally:
            db.close()
        await query.answer("Формирую дайджест…")
        from src.userbot.monitor import send_digest_now
        sent = await send_digest_now(update.effective_user.id)
        if not sent:
            await query.message.reply_text(
                "📰 <b>Нет новых постов</b>\n\nЗа последние 24 часа ничего не поступало.",
                parse_mode="HTML",
            )

    elif data.startswith("toggle_uc:"):
        uc_id = int(data.split(":")[1])
        db = get_session()
        try:
            user = _get_or_create_user(db, update.effective_user)
            uc = db.query(UserChannel).filter_by(id=uc_id, user_id=user.id).first()
            if uc:
                uc.is_active = not uc.is_active
                db.commit()
                ucs = (
                    db.query(UserChannel).filter_by(user_id=user.id)
                    .join(UserChannel.channel).all()
                )
                await query.message.edit_reply_markup(reply_markup=user_channels_keyboard(ucs))
        finally:
            db.close()

    elif data.startswith("del_uc:"):
        uc_id = int(data.split(":")[1])
        db = get_session()
        try:
            user = _get_or_create_user(db, update.effective_user)
            uc = db.query(UserChannel).filter_by(id=uc_id, user_id=user.id).first()
            if uc:
                db.delete(uc)
                db.commit()
                ucs = (
                    db.query(UserChannel).filter_by(user_id=user.id)
                    .join(UserChannel.channel).all()
                )
                if ucs:
                    await query.message.edit_reply_markup(reply_markup=user_channels_keyboard(ucs))
                else:
                    await query.message.edit_text(
                        "<b>Каналы не добавлены</b>\n\nДобавьте первый: /add_channel @username",
                        parse_mode="HTML",
                    )
        finally:
            db.close()


# ── Reply keyboard button handlers ───────────────────────────────────────────

async def btn_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_channels(update, context)


async def btn_add_channel_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting_channel"] = True
    await update.message.reply_text(
        "Введите username канала (с @ или без):",
    )


async def btn_summary_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_session()
    try:
        user = _get_or_create_user(db, update.effective_user)
        if not user.can_summary:
            await update.message.reply_text(
                "<b>📝 Саммари недоступно</b>\n\nФункция доступна на тарифах Basic и Pro.",
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(),
            )
            return
    finally:
        db.close()
    context.user_data["awaiting_summary_id"] = True
    await update.message.reply_text("Введите ID поста для саммари:")


async def btn_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_digest(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    if context.user_data.get("awaiting_channel"):
        context.user_data["awaiting_channel"] = False
        context.args = [text.lstrip("@")]
        await cmd_add_channel(update, context)
    elif context.user_data.get("awaiting_summary_id"):
        context.user_data["awaiting_summary_id"] = False
        try:
            int(text)
        except ValueError:
            await update.message.reply_text("❌ ID поста должен быть числом. Попробуйте ещё раз.")
            return
        context.args = [text]
        await cmd_summary(update, context)
