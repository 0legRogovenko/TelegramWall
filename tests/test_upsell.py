"""Paid feature offer eligibility and delivery tests."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot import keyboards
from src.bot.i18n import T
from src.config import config
from src.models import Subscription, User, UserChannel
from src.services.upsell import CAMPAIGN_KEYS, UpsellCandidate, render_upsell
from src.services import upsell as upsell_module
from tests.conftest import create_channel, create_subscription, create_user


def _due_upsells():
    try:
        from src.services.upsell import due_upsells
    except ImportError:
        pytest.fail("upsell service is not implemented")
    return due_upsells


@pytest.fixture(autouse=True)
def _clean_upsell_users(db):
    user_ids = [
        row[0]
        for row in db.query(User.id)
        .filter(User.telegram_id.between(9600, 9699))
        .all()
    ]
    if user_ids:
        db.query(UserChannel).filter(UserChannel.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        db.query(Subscription).filter(Subscription.user_id.in_(user_ids)).delete(
            synchronize_session=False
        )
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.commit()


def _active_channel(db, user, suffix: str):
    channel = create_channel(db, f"upsell_{suffix}")
    db.add(UserChannel(user_id=user.id, channel_id=channel.id, is_active=True))
    db.commit()


def test_selects_old_active_free_user(db):
    now = datetime.now(timezone.utc)
    user = create_user(
        db,
        telegram_id=9601,
        language="ru",
        created_at=now - timedelta(days=config.UPSELL_INTERVAL_DAYS + 1),
    )
    _active_channel(db, user, "eligible")

    result = _due_upsells()(db, now=now)

    assert [item.telegram_id for item in result if item.telegram_id == 9601] == [9601]


def test_excludes_new_inactive_paid_and_admin_users(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "ADMIN_IDS", [9605])
    new_user = create_user(db, telegram_id=9602, language="ru", created_at=now)
    inactive = create_user(
        db,
        telegram_id=9603,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    paid = create_user(
        db,
        telegram_id=9604,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    admin = create_user(
        db,
        telegram_id=9605,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    for candidate, suffix in (
        (new_user, "new"),
        (paid, "paid"),
        (admin, "admin"),
    ):
        _active_channel(db, candidate, suffix)
    create_subscription(db, paid, tier="basic")

    ids = {item.telegram_id for item in _due_upsells()(db, now=now)}

    assert new_user.telegram_id not in ids
    assert inactive.telegram_id not in ids
    assert paid.telegram_id not in ids
    assert admin.telegram_id not in ids


def test_respects_cooldown_and_batch_limit(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(config, "UPSELL_BATCH_SIZE", 2)
    for index in range(3):
        user = create_user(
            db,
            telegram_id=9610 + index,
            language="en",
            created_at=now - timedelta(days=10),
        )
        _active_channel(db, user, f"batch_{index}")
    cooling = create_user(
        db,
        telegram_id=9613,
        language="en",
        created_at=now - timedelta(days=10),
        upsell_last_sent_at=now - timedelta(days=1),
    )
    _active_channel(db, cooling, "cooling")

    result = _due_upsells()(db, now=now)

    assert len(result) == 2
    assert cooling.telegram_id not in {item.telegram_id for item in result}


@pytest.mark.parametrize("lang", ["ru", "en", "es"])
@pytest.mark.parametrize("variant", [0, 1, 2])
def test_every_campaign_renders_in_every_language(lang, variant):
    if CAMPAIGN_KEYS[variant] not in T:
        pytest.fail(f"missing translation key: {CAMPAIGN_KEYS[variant]}")
    candidate = UpsellCandidate(1, 1, lang, False, variant)
    text = render_upsell(candidate)
    assert text
    assert "{" not in text
    assert "Basic" in text or "Pro" in text


def test_trial_keyboard_adds_trial_before_monthly_plans():
    assert hasattr(keyboards, "upsell_keyboard")
    rows = keyboards.upsell_keyboard(trial_used=False, lang="ru").inline_keyboard
    assert rows[0][0].callback_data == "start_trial"
    assert [row[0].callback_data for row in rows[1:]] == [
        "subscribe:basic",
        "subscribe:pro",
    ]


def test_used_trial_keyboard_has_only_monthly_plans():
    assert hasattr(keyboards, "upsell_keyboard")
    rows = keyboards.upsell_keyboard(trial_used=True, lang="en").inline_keyboard
    assert [row[0].callback_data for row in rows] == [
        "subscribe:basic",
        "subscribe:pro",
    ]


@pytest.fixture
def inline_to_thread(monkeypatch):
    async def inline(func, *args):
        return func(*args)

    monkeypatch.setattr("src.services.upsell.asyncio.to_thread", inline)


async def test_successful_send_records_cooldown(db, monkeypatch, inline_to_thread):
    now = datetime.now(timezone.utc)
    user = create_user(
        db,
        telegram_id=9620,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "send_success")
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    assert hasattr(upsell_module, "send_due_upsells")
    assert await upsell_module.send_due_upsells() >= 1
    db.refresh(user)
    assert user.upsell_last_sent_at is not None


async def test_contextual_offer_advances_cooldown(db, inline_to_thread):
    user = create_user(db, telegram_id=9630, language="ru")

    assert hasattr(upsell_module, "mark_contextual_upsell")
    assert await upsell_module.mark_contextual_upsell(user.telegram_id) is True
    db.refresh(user)
    assert user.upsell_last_sent_at is not None


async def test_failed_send_does_not_consume_cooldown(
    db, monkeypatch, inline_to_thread
):
    now = datetime.now(timezone.utc)
    user = create_user(
        db,
        telegram_id=9621,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "send_failure")
    app = MagicMock()
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram down"))
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    assert hasattr(upsell_module, "send_due_upsells")
    await upsell_module.send_due_upsells()
    db.refresh(user)
    assert user.upsell_last_sent_at is None


async def test_candidate_that_subscribed_before_send_is_skipped(
    db, monkeypatch, inline_to_thread
):
    now = datetime.now(timezone.utc)
    user = create_user(
        db,
        telegram_id=9622,
        language="ru",
        created_at=now - timedelta(days=10),
    )
    _active_channel(db, user, "revalidate")
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)
    assert hasattr(upsell_module, "_load_due")
    candidates = upsell_module._load_due()
    create_subscription(db, user, tier="basic")
    monkeypatch.setattr("src.services.upsell._load_due", lambda: candidates)

    assert hasattr(upsell_module, "send_due_upsells")
    await upsell_module.send_due_upsells()

    assert all(
        call.kwargs["chat_id"] != user.telegram_id
        for call in app.bot.send_message.await_args_list
    )


async def test_upsell_loop_runs_sender_before_sleep(monkeypatch):
    from src.userbot import monitor

    sender = AsyncMock(return_value=2)
    monkeypatch.setattr("src.services.upsell.send_due_upsells", sender)

    async def stop_after_first_pass(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", stop_after_first_pass)
    with pytest.raises(asyncio.CancelledError):
        await monitor._upsell_loop()
    sender.assert_awaited_once()
