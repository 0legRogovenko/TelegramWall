"""Permanent Telegram delivery failures must not poison periodic work."""
from datetime import datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace

import pytest
from telegram.error import Forbidden

from src.bot.update_processor import UserUpdateProcessor
from src.models import PendingPost, UserChannel
from src.services import subscription_notifier, upsell
from src.userbot import monitor
from tests.conftest import (
    create_channel, create_post, create_subscription, create_user,
    subscribe_user_to_channel,
)

_IDS = count(17000)


@pytest.fixture
def recipients(db, monkeypatch):
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(monitor.asyncio, "to_thread", inline)
    monkeypatch.setattr(monitor, "get_session", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    for state in (monitor._batch_buffer, monitor._in_flight, monitor._pending_keys):
        state.clear()
    db.query(PendingPost).delete()
    user = create_user(
        db, telegram_id=next(_IDS), language="ru",
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    healthy = create_user(db, telegram_id=next(_IDS), language="ru")
    channel = create_channel(db, f"recipient_{user.telegram_id}")
    subscribe_user_to_channel(db, user, channel, ai_filter=None)
    subscribe_user_to_channel(db, healthy, channel)
    post = create_post(db, channel, "Original post content. " * 8)
    yield SimpleNamespace(db=db, user=user, healthy=healthy, channel=channel, post=post)
    db.query(PendingPost).delete()
    for state in (monitor._batch_buffer, monitor._in_flight, monitor._pending_keys):
        state.clear()
    db.commit()


def queue(data, user):
    data.db.add(PendingPost(
        telegram_id=user.telegram_id, channel_id=data.channel.id, post_id=data.post.id,
    ))
    data.db.commit()


def install_bot(monkeypatch, send_message, **methods):
    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(
        bot=SimpleNamespace(username="testbot", send_message=send_message, **methods),
    ))


async def test_forbidden_pending_is_not_retried_and_other_users_still_receive(
    recipients, monkeypatch,
):
    data = recipients
    queue(data, data.user)
    queue(data, data.healthy)
    attempts, delivered = [], []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        if kwargs["chat_id"] == data.user.telegram_id:
            raise Forbidden("Forbidden: bot was blocked by the user")
        delivered.append(kwargs["chat_id"])

    install_bot(monkeypatch, send)
    await monitor._flush_pending()
    await monitor._flush_pending()

    assert attempts.count(data.user.telegram_id) == 1
    assert delivered == [data.healthy.telegram_id]
    assert data.db.query(PendingPost).count() == 0
    data.db.refresh(data.user)
    assert getattr(data.user, "delivery_paused", False) is True
    assert data.db.query(UserChannel).filter_by(user_id=data.user.id, is_active=True).count() == 1


async def test_temporary_failure_remains_retryable(recipients, monkeypatch):
    data = recipients
    queue(data, data.user)
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise RuntimeError("temporary connection failure")

    install_bot(monkeypatch, send)
    await monitor._flush_pending()
    await monitor._flush_pending()
    assert attempts == [data.user.telegram_id, data.user.telegram_id]
    assert data.db.query(PendingPost).count() == 1
    assert getattr(data.user, "delivery_paused", False) is False


async def test_forbidden_media_does_not_fall_back_to_another_send(recipients, monkeypatch):
    data = recipients
    photos, texts = [], []

    async def photo(**kwargs):
        photos.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    async def text(**kwargs):
        texts.append(kwargs["chat_id"])

    async def download(*args):
        return b"test-image"

    install_bot(monkeypatch, text, send_photo=photo)
    monkeypatch.setattr(monitor, "_get_media_type", lambda _message: "photo")
    monkeypatch.setattr(monitor, "_download_media_bytes", download)
    message = SimpleNamespace(id=1, date=None, media=None, file=None)
    entry = {"label": "Source", "username": data.channel.username, "posts": [
        {"post_id": data.post.id, "text": data.post.text, "msg": message},
    ]}
    await monitor._deliver_entry(None, (data.user.telegram_id, data.channel.id), entry)
    assert photos == [data.user.telegram_id]
    assert texts == []
    data.db.refresh(data.user)
    assert getattr(data.user, "delivery_paused", False) is True


async def test_forbidden_auto_summary_does_not_retry_as_full_post(recipients, monkeypatch):
    data = recipients
    create_subscription(data.db, data.user, tier="pro")
    data.user.auto_summary = True
    data.post.summary = "A cached summary."
    data.db.commit()
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    entry = {"label": "Source", "username": data.channel.username, "posts": [
        {"post_id": data.post.id, "text": data.post.text,
         "msg": SimpleNamespace(id=1, date=None, media=None)},
    ]}
    await monitor._deliver_entry(None, (data.user.telegram_id, data.channel.id), entry)
    assert attempts == [data.user.telegram_id]
    assert data.db.query(PendingPost).count() == 0
    data.db.refresh(data.user)
    assert data.user.auto_summary is True
    assert data.user.subscription_tier == "pro"


async def test_forbidden_upsell_is_not_selected_again(recipients, monkeypatch):
    data = recipients
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    monkeypatch.setattr(upsell, "_load_due", lambda: upsell.due_upsells(
        data.db, user_id=data.user.id,
    ))
    assert await upsell.send_due_upsells() == 0
    assert await upsell.send_due_upsells() == 0
    assert attempts == [data.user.telegram_id]
    assert data.user.upsell_last_sent_at is None


async def test_forbidden_subscription_notice_is_not_selected_again(recipients, monkeypatch):
    data = recipients
    subscription = create_subscription(data.db, data.user, days=1)
    subscription.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    data.db.commit()
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    monkeypatch.setattr(subscription_notifier, "_load_due", lambda: [
        item for item in subscription_notifier.due_notifications(data.db)
        if item.telegram_id == data.user.telegram_id
    ])
    assert await subscription_notifier.send_due_notifications() == 0
    assert await subscription_notifier.send_due_notifications() == 0
    assert attempts == [data.user.telegram_id]
    assert subscription.expiry_warning_sent_at is None


def test_paused_user_is_not_eligible_for_posts(recipients):
    data = recipients
    data.user.delivery_paused = True
    data.db.commit()
    selected = monitor._get_eligible_subscribers(data.db, data.channel.id, "News")
    assert [telegram_id for telegram_id, _ in selected] == [data.healthy.telegram_id]


@pytest.mark.parametrize("chat_type", ["private", "group"])
async def test_only_private_interaction_resumes_delivery(recipients, chat_type):
    data = recipients
    data.user.delivery_paused = True
    data.db.commit()
    observed = []

    async def command():
        data.db.refresh(data.user)
        observed.append(data.user.delivery_paused)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=data.user.telegram_id),
        effective_chat=SimpleNamespace(type=chat_type, id=data.user.telegram_id),
        effective_message=SimpleNamespace(text="/start"),
    )
    await UserUpdateProcessor().process_update(update, command())
    assert observed == [chat_type != "private"]
    assert data.db.query(UserChannel).filter_by(user_id=data.user.id, is_active=True).count() == 1


async def test_block_membership_update_does_not_resume_delivery(recipients):
    data = recipients
    data.user.delivery_paused = True
    data.db.commit()
    observed = []

    async def handle_membership():
        data.db.refresh(data.user)
        observed.append(data.user.delivery_paused)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=data.user.telegram_id),
        effective_chat=SimpleNamespace(type="private", id=data.user.telegram_id),
        effective_message=None,
    )
    await UserUpdateProcessor().process_update(update, handle_membership())
    assert observed == [True]


async def test_failed_pause_write_preserves_undelivered_tail(recipients, monkeypatch):
    data = recipients

    async def send(**kwargs):
        raise Forbidden("Forbidden: bot was blocked by the user")

    def cannot_pause(*_args):
        raise RuntimeError("temporary state-write failure")

    install_bot(monkeypatch, send)
    monkeypatch.setattr(monitor, "pause_delivery", cannot_pause)
    entry = {"label": "Source", "username": data.channel.username, "posts": [
        {"post_id": data.post.id, "text": data.post.text,
         "msg": SimpleNamespace(id=1, date=None, media=None)},
    ]}
    await monitor._deliver_entry(None, (data.user.telegram_id, data.channel.id), entry)
    assert data.db.query(PendingPost).filter_by(telegram_id=data.user.telegram_id).count() == 1


async def test_forbidden_daily_digest_is_not_retried(recipients, monkeypatch):
    data = recipients
    data.user.digest_enabled = True
    create_subscription(data.db, data.user, tier="pro")
    data.db.commit()
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    monkeypatch.setattr(monitor, "build_digest", lambda *_args: "A complete digest.")
    original = monitor._daily_digest_users
    monkeypatch.setattr(monitor, "_daily_digest_users", lambda day: [
        telegram_id for telegram_id in original(day) if telegram_id == data.user.telegram_id
    ])
    await monitor._send_daily_digest()
    await monitor._send_daily_digest()
    assert attempts == [data.user.telegram_id]


async def test_cleared_pending_key_does_not_block_returning_user(recipients, monkeypatch):
    data = recipients
    key = data.user.telegram_id, data.channel.id
    monitor._pending_keys.add(key)

    async def send(**kwargs):
        pytest.fail("No pending post should be sent")

    install_bot(monkeypatch, send)
    await monitor._flush_pending()
    assert key not in monitor._pending_keys


async def test_newly_queued_key_survives_an_older_empty_snapshot(recipients, monkeypatch):
    data = recipients
    key = data.user.telegram_id, data.channel.id

    async def snapshot_then_queue(function, *args, **kwargs):
        result = function(*args, **kwargs)
        if function is monitor._pending_snapshot:
            queue(data, data.user)
            monitor._pending_keys.add(key)
        return result

    async def send(**kwargs):
        pytest.fail("The new row was not present in this snapshot")

    monkeypatch.setattr(monitor.asyncio, "to_thread", snapshot_then_queue)
    install_bot(monkeypatch, send)
    await monitor._flush_pending()
    assert key in monitor._pending_keys
    assert data.db.query(PendingPost).filter_by(telegram_id=data.user.telegram_id).count() == 1


def test_shutdown_does_not_recreate_a_paused_users_queue(recipients):
    data = recipients
    data.user.delivery_paused = True
    data.db.commit()
    assert monitor._queue_pending(data.user.telegram_id, data.channel.id, [
        {"post_id": data.post.id},
    ]) is True
    assert data.db.query(PendingPost).count() == 0


async def private_interaction(data, command):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=data.user.telegram_id),
        effective_chat=SimpleNamespace(type="private", id=data.user.telegram_id),
        effective_message=SimpleNamespace(text="/start"),
    )
    await UserUpdateProcessor().process_update(update, command())


async def test_late_forbidden_does_not_pause_a_returning_user(recipients, monkeypatch):
    data = recipients

    async def start():
        pass

    async def send(**kwargs):
        await private_interaction(data, start)
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    entry = {"label": "Source", "username": data.channel.username, "posts": [
        {"post_id": data.post.id, "text": data.post.text,
         "msg": SimpleNamespace(id=1, date=None, media=None)},
    ]}
    await monitor._deliver_entry(None, (data.user.telegram_id, data.channel.id), entry)
    data.db.refresh(data.user)
    assert data.user.delivery_paused is False
    assert data.db.query(PendingPost).filter_by(telegram_id=data.user.telegram_id).count() == 1


async def test_pause_completion_does_not_delete_new_posts_after_resume(recipients, monkeypatch):
    data = recipients
    key = data.user.telegram_id, data.channel.id
    new_post = create_post(data.db, data.channel, "A newly collected post", msg_id=2)

    async def start_and_collect():
        monitor._batch_buffer[key] = {"label": "Source", "posts": [
            {"post_id": new_post.id, "text": new_post.text},
        ], "first_at": 0}

    async def commit_then_resume(function, *args, **kwargs):
        result = function(*args, **kwargs)
        if function is monitor.pause_delivery:
            await private_interaction(data, start_and_collect)
        return result

    async def send(**kwargs):
        raise Forbidden("Forbidden: bot was blocked by the user")

    monkeypatch.setattr(monitor.asyncio, "to_thread", commit_then_resume)
    install_bot(monkeypatch, send)
    entry = {"label": "Source", "username": data.channel.username, "posts": [
        {"post_id": data.post.id, "text": data.post.text,
         "msg": SimpleNamespace(id=1, date=None, media=None)},
    ]}
    await monitor._deliver_entry(None, key, entry)
    data.db.refresh(data.user)
    assert data.user.delivery_paused is False
    buffered = monitor._batch_buffer.get(key, {}).get("posts", [])
    assert new_post.id in {post["post_id"] for post in buffered}


async def test_forbidden_skips_other_channels_in_the_same_pending_snapshot(
    recipients, monkeypatch,
):
    data = recipients
    queue(data, data.user)
    other = create_channel(data.db, f"other_recipient_{data.user.telegram_id}")
    post = create_post(data.db, other, "Another source")
    data.db.add(PendingPost(
        telegram_id=data.user.telegram_id, channel_id=other.id, post_id=post.id,
    ))
    data.db.commit()
    attempts = []

    async def send(**kwargs):
        attempts.append(kwargs["chat_id"])
        raise Forbidden("Forbidden: bot was blocked by the user")

    install_bot(monkeypatch, send)
    await monitor._flush_pending()
    assert attempts == [data.user.telegram_id]
    assert data.db.query(PendingPost).count() == 0


@pytest.mark.parametrize("kind", ["upsell", "subscription"])
async def test_pause_write_failure_does_not_abort_other_notifications(
    recipients, monkeypatch, kind,
):
    data = recipients
    data.healthy.created_at = data.user.created_at
    data.db.commit()
    if kind == "upsell":
        service = upsell
        monkeypatch.setattr(service, "_load_due", lambda: [
            candidate for user in (data.user, data.healthy)
            for candidate in service.due_upsells(data.db, user_id=user.id)
        ])
        send_due = service.send_due_upsells
    else:
        service = subscription_notifier
        for user in (data.user, data.healthy):
            subscription = create_subscription(data.db, user, days=1)
            subscription.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        data.db.commit()
        monkeypatch.setattr(service, "_load_due", lambda: [
            notice for notice in service.due_notifications(data.db)
            if notice.telegram_id in (data.user.telegram_id, data.healthy.telegram_id)
        ])
        send_due = service.send_due_notifications

    def cannot_pause(*_args):
        raise RuntimeError("temporary pause-state failure")

    delivered = []

    async def send(**kwargs):
        if kwargs["chat_id"] == data.user.telegram_id:
            raise Forbidden("Forbidden: bot was blocked by the user")
        delivered.append(kwargs["chat_id"])

    monkeypatch.setattr(service, "pause_delivery", cannot_pause)
    install_bot(monkeypatch, send)
    assert await send_due() == 1
    assert delivered == [data.healthy.telegram_id]


async def test_private_resume_database_work_stays_off_event_loop(monkeypatch):
    import threading
    from contextlib import contextmanager
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from src.database import Base
    from src.models import User
    from src.services import recipients as service

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        session.add(User(telegram_id=1, delivery_paused=True))
        session.commit()

    @contextmanager
    def sessions():
        with factory() as session:
            yield session

    queried_on = []

    def capture(*_args):
        queried_on.append(threading.get_ident())

    monkeypatch.setattr(service, "db_session", sessions)
    event.listen(engine, "before_cursor_execute", capture)
    ran = []

    async def command():
        ran.append(True)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(type="private", id=1),
        effective_message=SimpleNamespace(text="/start"),
    )
    try:
        await UserUpdateProcessor().process_update(update, command())
        event.remove(engine, "before_cursor_execute", capture)
        assert ran == [True]
        assert queried_on and threading.get_ident() not in queried_on
        with factory() as session:
            assert session.query(User).one().delivery_paused is False
    finally:
        engine.dispose()


def test_migration_defaults_existing_users_to_reachable(monkeypatch):
    from sqlalchemy import create_engine, text
    from src import database

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, telegram_id BIGINT NOT NULL UNIQUE)"
        )
        connection.exec_driver_sql("INSERT INTO users (id, telegram_id) VALUES (1, 1)")
    monkeypatch.setattr(database, "engine", engine)
    try:
        database.init_db()
        database.init_db()
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT id, delivery_paused, delivery_generation FROM users"
            )).one()
            assert tuple(row) == (1, 0, 0)
    finally:
        engine.dispose()
