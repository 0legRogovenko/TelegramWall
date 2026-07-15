"""Tests for SQLAlchemy model properties.

Each test uses a unique telegram_id to avoid UNIQUE constraint conflicts
between tests that share a single in-memory SQLite database.
"""
from datetime import datetime, timedelta, timezone

from src.models import Subscription
from tests.conftest import create_subscription, create_user

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
