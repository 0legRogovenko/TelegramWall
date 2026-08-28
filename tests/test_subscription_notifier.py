from datetime import datetime, timedelta, timezone

from src.models import Subscription
from src.services.subscription_notifier import due_notifications, send_due_notifications

from .conftest import create_user


def _subscription(db, user, expires_at, **kwargs):
    sub = Subscription(
        user_id=user.id,
        tier=kwargs.pop("tier", "pro"),
        stars_paid=kwargs.pop("stars_paid", 0),
        expires_at=expires_at,
        **kwargs,
    )
    db.add(sub)
    db.commit()
    return sub


def test_trial_warning_is_due_during_last_24_hours(db):
    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9501, language="ru")
    sub = _subscription(db, user, now + timedelta(hours=12))

    notices = due_notifications(db, now)
    notice = next(n for n in notices if n.subscription_id == sub.id)

    assert notice.kind == "warning"
    assert notice.is_trial is True


def test_expired_notice_is_due_once(db):
    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9502, language="en")
    sub = _subscription(db, user, now - timedelta(hours=1))

    notices = due_notifications(db, now)
    notice = next(n for n in notices if n.subscription_id == sub.id)
    assert notice.kind == "expired"
    assert notice.lang == "en"

    sub.expired_notice_sent_at = now
    db.commit()
    assert all(n.subscription_id != sub.id for n in due_notifications(db, now))


def test_old_subscription_is_not_notified_when_newer_one_exists(db):
    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9503, language="ru")
    old = _subscription(db, user, now + timedelta(hours=2))
    _subscription(db, user, now + timedelta(days=30), tier="basic", stars_paid=149)

    assert all(n.subscription_id != old.id for n in due_notifications(db, now))


async def test_successful_send_marks_warning(db, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9504, language="ru")
    sub = _subscription(db, user, now + timedelta(hours=12))
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    async def inline(func, *args):
        return func(*args)

    monkeypatch.setattr("src.services.subscription_notifier.asyncio.to_thread", inline)

    assert await send_due_notifications() >= 1
    db.refresh(sub)
    assert sub.expiry_warning_sent_at is not None


async def test_failed_send_is_retried_later(db, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9505, language="ru")
    sub = _subscription(db, user, now + timedelta(hours=12))
    app = MagicMock()
    app.bot.send_message = AsyncMock(side_effect=RuntimeError("Telegram unavailable"))
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    async def inline(func, *args):
        return func(*args)

    monkeypatch.setattr("src.services.subscription_notifier.asyncio.to_thread", inline)

    await send_due_notifications()
    db.refresh(sub)
    assert sub.expiry_warning_sent_at is None


async def test_successful_expired_notice_advances_upsell_cooldown(db, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    now = datetime.now(timezone.utc)
    user = create_user(db, telegram_id=9506, language="ru")
    sub = _subscription(db, user, now - timedelta(hours=1))
    app = MagicMock()
    app.bot.send_message = AsyncMock()
    monkeypatch.setattr("src.bot.app.ptb_app", app)

    async def inline(func, *args):
        return func(*args)

    monkeypatch.setattr("src.services.subscription_notifier.asyncio.to_thread", inline)
    monkeypatch.setattr("src.services.upsell.asyncio.to_thread", inline)

    assert await send_due_notifications() >= 1
    db.refresh(sub)
    db.refresh(user)
    assert sub.expired_notice_sent_at is not None
    assert user.upsell_last_sent_at is not None
