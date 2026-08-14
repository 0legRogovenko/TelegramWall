from datetime import datetime, timezone

from src.services.ai_access import authorized_post, claim_summary_request

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
