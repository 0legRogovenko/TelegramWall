"""Regressions reproduced from delivery failures and missed daily digests."""
import asyncio
import html
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from src.userbot import monitor
from tests.conftest import create_channel, create_post, create_user
from tests.conftest import create_subscription, subscribe_user_to_channel

_DIGEST_IDS = iter(range(12300, 12400))


@pytest.fixture
def delivery_db(db, monkeypatch):
    from src.models import PendingPost
    # The session-wide in-memory engine cannot move its connection to a worker.

    async def inline(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(monitor.asyncio, "to_thread", inline)
    monkeypatch.setattr(monitor, "get_session", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    db.query(PendingPost).delete()
    db.commit()
    yield db
    db.query(PendingPost).delete()
    db.commit()


def plain_text(value):
    return "".join(ET.fromstring(f"<root>{value}</root>").itertext())


@pytest.mark.parametrize("text", ["x" * 4096, "😀" * 2100, "<&>" * 1500],
                         ids=["full-size-post", "emoji", "html-entities"])
async def test_long_post_is_delivered_in_valid_complete_chunks(delivery_db, monkeypatch, text):
    create_user(delivery_db, telegram_id=12100 + len(text))
    sent = []

    async def send_message(**kwargs):
        visible = plain_text(kwargs["text"])
        if len(visible.encode("utf-16-le")) // 2 > 4096:
            raise BadRequest("Message is too long")
        sent.append(kwargs)

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
    ))
    msg = SimpleNamespace(id=1, media=None, date=None)
    await monitor._deliver_to_user(None, 12100 + len(text), msg, "Channel", 42,
                                   text, username="channel")

    assert len(sent) >= 2
    assert text in "".join(plain_text(part["text"]) for part in sent)
    assert sent[-1]["reply_markup"] is not None


def test_splitter_keeps_tags_and_entities_valid():
    text = '<b>' + html.escape("😀<&>" * 1800) + '</b>'
    chunks = monitor._split_message(text)
    decoded = [plain_text(chunk) for chunk in chunks]
    assert "".join(decoded) == "😀<&>" * 1800
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in decoded)


async def test_failed_post_stays_pending_for_retry(delivery_db, monkeypatch):
    from src.models import PendingPost

    channel = create_channel(delivery_db, "retry_long_post")
    post = create_post(delivery_db, channel, "undelivered")
    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        send_message=AsyncMock(side_effect=RuntimeError("network unavailable")),
    )))
    entry = {"posts": [{"post_id": post.id, "text": post.text,
                        "msg": SimpleNamespace(id=1, media=None, date=None)}],
             "label": "Retry", "username": "retry_long_post"}
    await monitor._deliver_entry(None, (12200, channel.id), entry)
    assert delivery_db.query(PendingPost).filter_by(
        telegram_id=12200, post_id=post.id,
    ).count() == 1


async def test_digest_loop_catches_up_after_scheduled_time(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 8, 8, 30, tzinfo=timezone.utc)

    sent = []

    async def daily():
        sent.append("attempt")

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor, "datetime", Clock)
    monkeypatch.setattr(monitor, "_send_daily_digest", daily)
    monkeypatch.setattr(monitor.asyncio, "sleep", stop)
    monkeypatch.setattr(monitor.config, "DIGEST_HOUR_UTC", 8)
    with pytest.raises(asyncio.CancelledError):
        await monitor._digest_loop()
    assert sent == ["attempt"]


async def test_slow_poll_database_does_not_block_event_loop(db, monkeypatch):
    import time
    from sqlalchemy import event
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.database import Base

    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    queried_on = []

    def delay(_conn, _cursor, _statement, _parameters, _context, _many):
        queried_on.append(threading.get_ident())
        time.sleep(0.01)

    event.listen(engine, "before_cursor_execute", delay)
    monkeypatch.setattr(monitor, "get_session", factory)

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", stop)
    try:
        with pytest.raises(asyncio.CancelledError):
            await monitor._poll_channels(None)
        assert queried_on
        assert threading.get_ident() not in queried_on
    finally:
        engine.dispose()


@pytest.fixture
def digest_user(delivery_db):
    from src.models import User
    # Other tests commit rows into this shared SQLite DB.
    enabled = [u.id for u in delivery_db.query(User).filter_by(digest_enabled=True).all()]
    delivery_db.query(User).update({User.digest_enabled: False})
    tg_id = next(_DIGEST_IDS)
    user = create_user(delivery_db, telegram_id=tg_id, digest_enabled=True)
    create_subscription(delivery_db, user, tier="pro")
    channel = create_channel(delivery_db, f"daily_regression{tg_id}")
    subscribe_user_to_channel(delivery_db, user, channel)
    create_post(delivery_db, channel, "News from today")
    yield user
    delivery_db.query(User).filter_by(id=user.id).update({User.digest_enabled: False})
    delivery_db.query(User).filter(User.id.in_(enabled)).update({User.digest_enabled: True})
    delivery_db.commit()


async def test_daily_digest_survives_restart_without_duplicate(digest_user, monkeypatch):
    sent = []

    async def send(**kwargs):
        sent.append(kwargs["text"])

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        username="testbot", send_message=send,
    )))
    monkeypatch.setattr(monitor, "build_digest", lambda *args: "📢 @daily_regression\nNews.")
    await monitor._send_daily_digest()
    await monitor._send_daily_digest()
    assert len(sent) == 1


async def test_digest_api_failure_is_not_reported_as_no_posts(delivery_db, monkeypatch):
    from src.bot.handlers.callbacks import callback_handler
    from tests.conftest import make_context, make_update

    update = make_update(999)
    update.callback_query = SimpleNamespace(
        data="dgo", answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock()),
    )
    context = make_context()
    context.user_data = {"dsel": {1}, "dsel_pairs": [(1, "source")], "dsel_lang": "ru"}
    monkeypatch.setattr(monitor, "send_digest_now", AsyncMock(side_effect=RuntimeError("401")))
    await callback_handler(update, context)
    text = update.callback_query.message.edit_text.call_args.args[0]
    assert "Нет новых постов" not in text
    assert "попроб" in text.lower() or "недоступ" in text.lower()


async def test_partial_daily_digest_resumes_without_regenerating(digest_user, monkeypatch):
    sent = []
    attempts = []
    generations = []

    async def send(**kwargs):
        attempts.append(kwargs["text"])
        if len(attempts) == 2:
            raise RuntimeError("temporary Telegram failure")
        sent.append(kwargs["text"])

    def generate(*args):
        generations.append(args)
        return "📢 @source\n" + "News. " * 1500

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        username="testbot", send_message=send,
    )))
    monkeypatch.setattr(monitor, "build_digest", generate)
    await monitor._send_daily_digest()
    first = sent[0]
    await monitor._send_daily_digest()
    assert len(generations) == 1
    assert sent.count(first) == 1
    assert len(sent) >= 3
    assert "News. " * 1500 in "".join(plain_text(part) for part in sent)


async def test_one_users_digest_does_not_block_other_users_commands(monkeypatch):
    from src.bot import app as bot_app
    from src.bot.app import build_ptb_app

    # build_ptb_app changes runtime globals; restore them before other tests.
    monkeypatch.setattr(bot_app, "_loop", bot_app._loop)
    monkeypatch.setattr(bot_app, "ptb_app", bot_app.ptb_app)
    processor = build_ptb_app(asyncio.get_running_loop()).update_processor
    started = asyncio.Event()
    release = asyncio.Event()
    other_done = asyncio.Event()
    same_user_done = asyncio.Event()

    async def slow_digest():
        started.set()
        await release.wait()

    async def other_command():
        other_done.set()

    async def same_user_command():
        same_user_done.set()

    first = asyncio.create_task(processor.process_update(
        SimpleNamespace(effective_user=SimpleNamespace(id=1)), slow_digest(),
    ))
    await started.wait()
    second = asyncio.create_task(processor.process_update(
        SimpleNamespace(effective_user=SimpleNamespace(id=2)), other_command(),
    ))
    third = asyncio.create_task(processor.process_update(
        SimpleNamespace(effective_user=SimpleNamespace(id=1)), same_user_command(),
    ))
    try:
        await asyncio.wait_for(other_done.wait(), timeout=1)
        assert not same_user_done.is_set()
    finally:
        release.set()
        await asyncio.gather(first, second, third)


async def test_pending_posts_retry_in_order_after_temporary_failure(delivery_db, monkeypatch):
    from src.models import PendingPost

    channel = create_channel(delivery_db, "ordered_retry_regression")
    posts = [create_post(delivery_db, channel, f"Retry post {index}", msg_id=index)
             for index in (1, 2)]
    for post in posts:
        delivery_db.add(PendingPost(telegram_id=12500, channel_id=channel.id, post_id=post.id))
    delivery_db.commit()
    attempts = []
    sent = []

    async def send(**kwargs):
        attempts.append(kwargs["text"])
        if len(attempts) == 1:
            raise RuntimeError("temporary send failure")
        sent.append(kwargs["text"])

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        send_message=send,
    )))
    await monitor._flush_pending()
    assert sent == []
    assert delivery_db.query(PendingPost).filter_by(telegram_id=12500).count() == 2
    await monitor._flush_pending()
    assert "Retry post 1" in sent[0]
    assert "Retry post 2" in sent[1]
    assert delivery_db.query(PendingPost).filter_by(telegram_id=12500).count() == 0


async def test_failed_queue_write_keeps_post_in_memory(delivery_db, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("send unavailable")

    key = (12501, 987)
    entry = {"posts": [{"post_id": 1, "text": "preserve me", "msg": None}],
             "label": "Source", "username": "source"}
    monkeypatch.setattr(monitor, "_deliver_to_user", fail)
    monkeypatch.setattr(monitor, "_queue_pending", lambda *args: False)
    try:
        await monitor._deliver_entry(None, key, entry)
        assert monitor._batch_buffer[key]["posts"][0]["text"] == "preserve me"
    finally:
        monitor._batch_buffer.pop(key, None)
        monitor._pending_keys.discard(key)


async def test_long_summary_keeps_photo(delivery_db, monkeypatch):
    from telethon.tl.types import MessageMediaPhoto

    channel = create_channel(delivery_db, "long_media_summary")
    post = create_post(delivery_db, channel, "Original post " * 20)
    post.summary = "Summary " * 180
    delivery_db.commit()
    photos = []
    messages = []

    async def photo(**kwargs):
        assert len(plain_text(kwargs["caption"]).encode("utf-16-le")) // 2 <= 1024
        photos.append(kwargs)

    async def message(**kwargs):
        messages.append(kwargs["text"])

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        send_photo=photo, send_message=message,
    )))
    monkeypatch.setitem(monitor._media_file_ids, post.id, "synthetic-photo-id")
    msg = SimpleNamespace(media=MessageMediaPhoto(photo=None), id=1)
    assert await monitor._send_summary(None, 12502, post.id, post.text, "Source",
                                       username="source", msg_id=1, msg=msg)
    assert len(photos) == 1
    assert post.summary in "".join(plain_text(text) for text in messages)


async def test_startup_and_periodic_retry_do_not_duplicate_posts(delivery_db, monkeypatch):
    from src.models import PendingPost

    channel = create_channel(delivery_db, "concurrent_retry_regression")
    post = create_post(delivery_db, channel, "Deliver once")
    for tg_id in (12503, 12504):
        delivery_db.add(PendingPost(telegram_id=tg_id, channel_id=channel.id, post_id=post.id))
    delivery_db.commit()
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def send(**kwargs):
        if kwargs["chat_id"] == 12503:
            started.set()
            await release.wait()
        sent.append(kwargs["chat_id"])

    monkeypatch.setattr("src.bot.app.ptb_app", SimpleNamespace(bot=SimpleNamespace(
        send_message=send,
    )))
    first = asyncio.create_task(monitor._flush_pending())
    await started.wait()
    second = asyncio.create_task(monitor._flush_pending())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert sent == [12503, 12504]
