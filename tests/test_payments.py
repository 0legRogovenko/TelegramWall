"""Regression tests for the money-sensitive Telegram payment path."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.payments import handle_pre_checkout, handle_successful_payment
from src.models import Subscription

from .conftest import create_user, make_context, make_update


def _payment_update(user_id: int, charge_id: str = "charge-1"):
    update = make_update(user_id=user_id)
    payment = MagicMock()
    payment.invoice_payload = "subscribe:basic"
    payment.currency = "XTR"
    payment.total_amount = 149
    payment.telegram_payment_charge_id = charge_id
    update.message.successful_payment = payment
    return update


class TestPreCheckout:
    @pytest.mark.asyncio
    async def test_accepts_known_tier_and_exact_amount(self):
        update = MagicMock()
        update.pre_checkout_query.invoice_payload = "subscribe:basic"
        update.pre_checkout_query.currency = "XTR"
        update.pre_checkout_query.total_amount = 149
        update.pre_checkout_query.answer = AsyncMock()

        await handle_pre_checkout(update, make_context())

        update.pre_checkout_query.answer.assert_awaited_once_with(ok=True)

    @pytest.mark.asyncio
    async def test_rejects_unknown_tier(self):
        update = MagicMock()
        update.pre_checkout_query.invoice_payload = "subscribe:vip"
        update.pre_checkout_query.currency = "XTR"
        update.pre_checkout_query.total_amount = 149
        update.pre_checkout_query.answer = AsyncMock()

        await handle_pre_checkout(update, make_context())

        assert update.pre_checkout_query.answer.await_args.kwargs["ok"] is False


class TestSuccessfulPayment:
    @pytest.fixture(autouse=True)
    def _clean_charges(self, db):
        db.query(Subscription).filter(Subscription.payment_charge_id.is_not(None)).delete()
        db.commit()

    @pytest.mark.asyncio
    async def test_duplicate_charge_is_applied_once(self, db):
        user = create_user(db, telegram_id=9201, language="ru")
        update = _payment_update(user.telegram_id, "dedupe-charge")
        context = make_context()

        with patch("src.bot.handlers._sync_menu_commands", new=AsyncMock()):
            await handle_successful_payment(update, context)
            await handle_successful_payment(update, context)

        assert db.query(Subscription).filter_by(
            payment_charge_id="dedupe-charge"
        ).count() == 1
        assert update.message.reply_text.await_count == 1

    @pytest.mark.asyncio
    async def test_mismatched_amount_is_ignored(self, db):
        user = create_user(db, telegram_id=9202, language="ru")
        update = _payment_update(user.telegram_id, "wrong-amount")
        update.message.successful_payment.total_amount = 1

        await handle_successful_payment(update, make_context())

        assert db.query(Subscription).filter_by(
            payment_charge_id="wrong-amount"
        ).count() == 0
