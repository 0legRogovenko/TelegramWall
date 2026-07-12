"""Tests for SQLAlchemy model properties.

Each test uses a unique telegram_id to avoid UNIQUE constraint conflicts
between tests that share a single in-memory SQLite database.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.models import Subscription
from tests.conftest import (
    create_channel,
    create_subscription,
    create_user,
    subscribe_user_to_channel,
)

# Telegram IDs for this module: 8001–8100 (no overlap with other test files)
_TG = iter(range(8001, 8101))


def _uid():
    """Return the next unique telegram_id."""
    return next(_TG)


# ── Subscription tier & properties ───────────────────────────────────────────

class TestUserSubscriptionProperties:
    def test_tier_is_free_by_default(self, db):
        user = create_user(db, telegram_id=_uid())
        assert user.subscription_tier == "free"

    def test_tier_basic(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        assert user.subscription_tier == "basic"

    def test_tier_pro(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="pro")
        db.refresh(user)
        assert user.subscription_tier == "pro"

    def test_tier_annual_basic(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="annual_basic")
        db.refresh(user)
        assert user.subscription_tier == "annual_basic"

    def test_expired_subscription_falls_back_to_free(self, db):
        user = create_user(db, telegram_id=_uid())
        expired = Subscription(
            user_id=user.id,
            tier="basic",
            stars_paid=100,
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add(expired)
        db.commit()
        db.refresh(user)
        assert user.subscription_tier == "free"

    def test_has_subscription_false_for_free(self, db):
        user = create_user(db, telegram_id=_uid())
        assert user.has_subscription is False

    def test_has_subscription_true_when_active(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        assert user.has_subscription is True

    def test_channel_limit_free(self, db):
        from src.config import config
        user = create_user(db, telegram_id=_uid())
        assert user.channel_limit == config.CHANNEL_LIMIT_FREE

    def test_channel_limit_basic(self, db):
        from src.config import config
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        assert user.channel_limit == config.CHANNEL_LIMIT_BASIC

    def test_channel_limit_annual_basic(self, db):
        from src.config import config
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="annual_basic")
        db.refresh(user)
        assert user.channel_limit == config.CHANNEL_LIMIT_BASIC

    def test_channel_limit_pro_is_unlimited(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="pro")
        db.refresh(user)
        assert user.channel_limit is None

    def test_channel_limit_annual_pro_is_unlimited(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="annual_pro")
        db.refresh(user)
        assert user.channel_limit is None

    def test_can_summary_free_is_false(self, db):
        user = create_user(db, telegram_id=_uid())
        assert user.can_summary is False

    def test_can_summary_basic_is_true(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        assert user.can_summary is True

    def test_can_summary_pro_is_true(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="pro")
        db.refresh(user)
        assert user.can_summary is True

    def test_can_auto_summary_free_is_false(self, db):
        user = create_user(db, telegram_id=_uid())
        assert user.can_auto_summary is False

    def test_can_auto_summary_basic_is_false(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        assert user.can_auto_summary is False

    def test_can_auto_summary_pro_is_true(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="pro")
        db.refresh(user)
        assert user.can_auto_summary is True

    def test_can_auto_summary_annual_pro_is_true(self, db):
        user = create_user(db, telegram_id=_uid())
        create_subscription(db, user, tier="annual_pro")
        db.refresh(user)
        assert user.can_auto_summary is True


# ── Quiet mode ────────────────────────────────────────────────────────────────

class TestUserQuietMode:
    def test_is_quiet_now_false_when_no_quiet_set(self, db):
        user = create_user(db, telegram_id=_uid())
        assert user.is_quiet_now is False

    def test_is_quiet_now_false_when_only_start_set(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 22
        # quiet_end not set → should return False
        db.commit()
        assert user.is_quiet_now is False

    def test_is_quiet_now_inside_simple_range(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 9
        user.quiet_end = 18
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 14  # inside [9, 18)
            assert user.is_quiet_now is True

    def test_is_quiet_now_outside_simple_range(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 9
        user.quiet_end = 18
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 20  # outside [9, 18)
            assert user.is_quiet_now is False

    def test_is_quiet_now_at_start_boundary(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 9
        user.quiet_end = 18
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 9  # on left boundary (included)
            assert user.is_quiet_now is True

    def test_is_quiet_now_at_end_boundary(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 9
        user.quiet_end = 18
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 18  # on right boundary (excluded)
            assert user.is_quiet_now is False

    def test_is_quiet_now_overnight_inside_range(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 22
        user.quiet_end = 8
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 2  # 02:00 is inside overnight [22, 8)
            assert user.is_quiet_now is True

    def test_is_quiet_now_overnight_start_side(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 22
        user.quiet_end = 8
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 23  # 23:00 is inside overnight
            assert user.is_quiet_now is True

    def test_is_quiet_now_overnight_outside_range(self, db):
        user = create_user(db, telegram_id=_uid())
        user.quiet_start = 22
        user.quiet_end = 8
        db.commit()
        with patch("src.models.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12  # 12:00 is outside overnight range
            assert user.is_quiet_now is False


# ── UserChannel.keyword_list ──────────────────────────────────────────────────

class TestUserChannelKeywordList:
    def test_keyword_list_empty_when_no_keywords(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_empty_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel)
        assert uc.keyword_list == []

    def test_keyword_list_empty_for_none(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_none_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel, keywords=None)
        assert uc.keyword_list == []

    def test_keyword_list_single_word(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_single_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel, keywords="python")
        assert uc.keyword_list == ["python"]

    def test_keyword_list_multiple_words(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_multi_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel, keywords="python, django, flask")
        assert set(uc.keyword_list) == {"python", "django", "flask"}

    def test_keyword_list_lowercased(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_case_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel, keywords="Python, DJANGO")
        assert uc.keyword_list == ["python", "django"]

    def test_keyword_list_strips_whitespace(self, db):
        user = create_user(db, telegram_id=_uid())
        channel = create_channel(db, username=f"kw_spaces_{_uid()}")
        uc = subscribe_user_to_channel(db, user, channel, keywords="  news , tech  ")
        assert uc.keyword_list == ["news", "tech"]
