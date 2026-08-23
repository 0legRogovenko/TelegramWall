from datetime import datetime, timezone

from src.services.ai_access import authorized_post, claim_summary_request
from src.bot.handlers.ai import resume_pending_summary

from .conftest import create_channel, create_post, create_user, subscribe_user_to_channel


def test_post_requires_active_subscription_to_its_channel(db):
    user = create_user(db, telegram_id=9301)
    own = create_channel(db, "ai_own")
    other = create_channel(db, "ai_other")
    own_post = create_post(db, own, msg_id=9301)
    other_post = create_post(db, other, msg_id=9302)
    subscribe_user_to_channel(db, user, own)

    assert authorized_post(db, user, own_post.id).id == own_post.id
    assert authorized_post(db, user, other_post.id) is None


def test_inactive_channel_does_not_authorize_post(db):
    user = create_user(db, telegram_id=9302)
    channel = create_channel(db, "ai_inactive")
    post = create_post(db, channel, msg_id=9303)
    subscribe_user_to_channel(db, user, channel, is_active=False)

    assert authorized_post(db, user, post.id) is None


def test_daily_quota_is_enforced_and_resets(db, monkeypatch):
    user = create_user(db, telegram_id=9303)
    monkeypatch.setattr("src.config.config.AI_DAILY_SUMMARY_LIMIT", 2)

    assert claim_summary_request(db, user) is True
    assert claim_summary_request(db, user) is True
    assert claim_summary_request(db, user) is False

    user.ai_usage_date = "2000-01-01"
    db.commit()
    assert claim_summary_request(db, user) is True
    assert user.ai_usage_date == datetime.now(timezone.utc).date().isoformat()


async def test_cached_pending_summary_is_sent_after_access_activates(db):
    from unittest.mock import AsyncMock
    from .conftest import create_subscription

    user = create_user(db, telegram_id=9304, language="ru")
    channel = create_channel(db, "ai_pending")
    post = create_post(db, channel, text="x" * 100, msg_id=9304)
    post.summary = "готовое саммари"
    user.pending_summary_post_id = post.id
    subscribe_user_to_channel(db, user, channel)
    create_subscription(db, user, tier="basic")
    db.commit()
    bot = AsyncMock()

    assert await resume_pending_summary(user.telegram_id, bot) is True
    db.refresh(user)
    assert user.pending_summary_post_id is None
    bot.send_message.assert_awaited_once()


async def test_failed_resume_send_keeps_pending_request(db):
    from unittest.mock import AsyncMock
    from .conftest import create_subscription

    user = create_user(db, telegram_id=9305, language="ru")
    channel = create_channel(db, "ai_pending_retry")
    post = create_post(db, channel, text="x" * 100, msg_id=9305)
    post.summary = "готовое саммари"
    user.pending_summary_post_id = post.id
    subscribe_user_to_channel(db, user, channel)
    create_subscription(db, user, tier="basic")
    db.commit()
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("temporary Telegram error")

    assert await resume_pending_summary(user.telegram_id, bot) is False
    db.refresh(user)
    assert user.pending_summary_post_id == post.id
