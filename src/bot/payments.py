"""Telegram Stars payment flow."""
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.config import config
from src.database import db_session
from src.models import Subscription, User

_TIER_META = {
    "basic": {
        "label": "Basic ⭐",
        "price": lambda: config.SUBSCRIPTION_PRICE_BASIC_STARS,
        "days": 30,
        "description": "10 каналов + саммари по запросу — 30 дней.",
    },
    "pro": {
        "label": "Pro 💎",
        "price": lambda: config.SUBSCRIPTION_PRICE_PRO_STARS,
        "days": 30,
        "description": "∞ каналов + авто-саммари — 30 дней.",
    },
    "annual_basic": {
        "label": "Basic Годовой ⭐",
        "price": lambda: config.SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS,
        "days": 365,
        "description": "10 каналов + саммари по запросу — 365 дней (скидка 20%).",
    },
    "annual_pro": {
        "label": "Pro Годовой 💎",
        "price": lambda: config.SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS,
        "days": 365,
        "description": "∞ каналов + авто-саммари — 365 дней (скидка 20%).",
    },
}


async def send_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tier: str = "basic",
) -> None:
    meta = _TIER_META.get(tier, _TIER_META["basic"])
    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"Подписка TelegramWall {meta['label']}",
        description=meta["description"],
        payload=f"subscribe:{tier}",  # noqa: E231
        provider_token="",
        currency="XTR",
        prices=[{"label": meta["label"], "amount": meta["price"]()}],  # noqa: E231
    )


async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.pre_checkout_query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    telegram_id = update.effective_user.id

    payload = payment.invoice_payload
    tier = payload.split(":")[1] if payload.startswith("subscribe:") else "basic"
    meta = _TIER_META.get(tier, _TIER_META["basic"])

    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            return

        expires_at = datetime.now(timezone.utc) + timedelta(days=meta["days"])
        db.add(Subscription(
            user_id=user.id,
            tier=tier,
            stars_paid=payment.total_amount,
            payment_charge_id=payment.telegram_payment_charge_id,
            expires_at=expires_at,
        ))
        db.commit()

        perks = (
            "✅ Саммари по запросу и авто-саммари"
            if tier in ("pro", "annual_pro")
            else "✅ Саммари по запросу (/summary)"
        )
        date_str = expires_at.strftime("%d.%m.%Y")
        await update.message.reply_text(
            f"✅ Подписка <b>{meta['label']}</b> активирована до {date_str}!\n\n{perks}",
            parse_mode="HTML",
        )
