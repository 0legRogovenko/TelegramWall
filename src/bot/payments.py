"""Telegram payment flow — YooKassa (RUB) or Telegram Stars (XTR) fallback.

Active mode is determined by config.YOOKASSA_PROVIDER_TOKEN:
  • Set  → invoices in RUB via YooKassa (real bank-card / SBP payments)
  • Empty → invoices in XTR (Telegram Stars, no external provider needed)

Pre-checkout and successful_payment handling is identical for both modes.
"""
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.config import config
from src.database import db_session
from src.models import Subscription, User

logger = logging.getLogger(__name__)

_TIER_META: dict[str, dict] = {
    "basic": {
        "label":       "Basic ⭐",
        "days":        30,
        "description": "10 каналов + саммари по запросу — 30 дней.",
        "stars":       lambda: config.SUBSCRIPTION_PRICE_BASIC_STARS,
        "rub":         lambda: config.SUBSCRIPTION_PRICE_BASIC_RUB,
    },
    "pro": {
        "label":       "Pro 💎",
        "days":        30,
        "description": "∞ каналов + авто-саммари — 30 дней.",
        "stars":       lambda: config.SUBSCRIPTION_PRICE_PRO_STARS,
        "rub":         lambda: config.SUBSCRIPTION_PRICE_PRO_RUB,
    },
    "annual_basic": {
        "label":       "Basic Годовой ⭐",
        "days":        365,
        "description": "10 каналов + саммари по запросу — 365 дней (скидка 20%).",
        "stars":       lambda: config.SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS,
        "rub":         lambda: config.SUBSCRIPTION_PRICE_ANNUAL_BASIC_RUB,
    },
    "annual_pro": {
        "label":       "Pro Годовой 💎",
        "days":        365,
        "description": "∞ каналов + авто-саммари — 365 дней (скидка 20%).",
        "stars":       lambda: config.SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS,
        "rub":         lambda: config.SUBSCRIPTION_PRICE_ANNUAL_PRO_RUB,
    },
}


def price_label(tier: str) -> str:
    """Human-readable price for a tier in the active payment currency."""
    meta = _TIER_META.get(tier)
    if meta is None:
        raise ValueError(f"Unknown subscription tier: {tier}")
    if config.YOOKASSA_PROVIDER_TOKEN:
        return f"{meta['rub']() // 100} ₽"
    return f"{meta['stars']()} ⭐"


async def send_invoice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tier: str = "basic",
) -> None:
    meta = _TIER_META.get(tier)
    if meta is None:
        raise ValueError(f"Unknown subscription tier: {tier}")

    if config.YOOKASSA_PROVIDER_TOKEN:
        provider_token = config.YOOKASSA_PROVIDER_TOKEN
        currency = "RUB"
        amount = meta["rub"]()
        extra = {
            "need_email": True,           # Telegram collects email before payment
            "send_email_to_provider": True,  # passes it to YooKassa for 54-ФЗ receipts
        }
    else:
        provider_token = ""
        currency = "XTR"
        amount = meta["stars"]()
        extra = {}

    await context.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"Подписка TelegramWall {meta['label']}",
        description=meta["description"],
        payload=f"subscribe:{tier}",  # noqa: E231
        provider_token=provider_token,
        currency=currency,
        prices=[{"label": meta["label"], "amount": amount}],
        **extra,
    )


async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    payload = query.invoice_payload or ""
    tier = payload.removeprefix("subscribe:") if payload.startswith("subscribe:") else ""
    meta = _TIER_META.get(tier)
    expected_currency = "RUB" if config.YOOKASSA_PROVIDER_TOKEN else "XTR"
    expected_amount = (
        meta["rub"]() if meta and expected_currency == "RUB"
        else meta["stars"]() if meta else None
    )
    if (
        meta is None
        or query.currency != expected_currency
        or query.total_amount != expected_amount
    ):
        logger.warning(
            "Rejected invalid pre-checkout: payload=%r currency=%r amount=%r",
            payload, query.currency, query.total_amount,
        )
        await query.answer(ok=False, error_message="Платёж устарел. Откройте тарифы заново.")
        return
    await query.answer(ok=True)


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payment = update.message.successful_payment
    telegram_id = update.effective_user.id

    payload = payment.invoice_payload
    tier = payload.removeprefix("subscribe:") if payload.startswith("subscribe:") else ""
    meta = _TIER_META.get(tier)
    if meta is None:
        logger.error("Ignoring successful payment with invalid payload %r", payload)
        return

    expected_currency = "RUB" if config.YOOKASSA_PROVIDER_TOKEN else "XTR"
    expected_amount = meta["rub"]() if expected_currency == "RUB" else meta["stars"]()
    if payment.currency != expected_currency or payment.total_amount != expected_amount:
        logger.error("Ignoring successful payment with mismatched amount or currency")
        return

    charge_id = payment.telegram_payment_charge_id
    if not charge_id:
        logger.error("Ignoring successful payment without telegram charge id")
        return

    with db_session() as db:
        existing = db.query(Subscription).filter_by(payment_charge_id=charge_id).first()
        if existing:
            logger.info("Duplicate successful_payment ignored: %s", charge_id)
            return

        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            return

        expires_at = datetime.now(timezone.utc) + timedelta(days=meta["days"])
        db.add(Subscription(
            user_id=user.id,
            tier=tier,
            stars_paid=payment.total_amount,
            payment_currency=payment.currency,
            payment_charge_id=charge_id,
            expires_at=expires_at,
        ))
        db.commit()

        perks = (
            "✅ Саммари по запросу и авто-саммари"
            if tier in ("pro", "annual_pro")
            else "✅ Саммари по запросу (/summary)"
        )
        date_str = expires_at.strftime("%d.%m.%Y")
        from src.bot.handlers import _sync_menu_commands  # local: avoids circular import
        from src.bot.keyboards import main_menu
        db.refresh(user)
        await update.message.reply_text(
            f"✅ Подписка <b>{meta['label']}</b> активирована до {date_str}!\n\n{perks}",
            parse_mode="HTML",
            reply_markup=main_menu(paid=True),
        )
        await _sync_menu_commands(context.bot, update.effective_chat.id, user)
