"""Tests for _get_eligible_subscribers logic in userbot/monitor.py."""
from unittest.mock import patch

import pytest

from src.userbot.monitor import _get_eligible_subscribers
from tests.conftest import (
    create_channel,
    create_user,
    subscribe_user_to_channel,
)


class TestGetEligibleSubscribers:
    def test_no_subscribers_returns_empty_list(self, db):
        channel = create_channel(db, username="empty_chan_mon")
        result = _get_eligible_subscribers(db, channel.id, "some text")
        assert result == []

    def test_active_subscriber_is_included(self, db):
        user = create_user(db, telegram_id=7001)
        channel = create_channel(db, username="active_chan_mon")
        subscribe_user_to_channel(db, user, channel)
        result = _get_eligible_subscribers(db, channel.id, "some text")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7001 in tg_ids

    def test_inactive_subscription_is_excluded(self, db):
        user = create_user(db, telegram_id=7002)
        channel = create_channel(db, username="inactive_chan_mon")
        subscribe_user_to_channel(db, user, channel, is_active=False)
        result = _get_eligible_subscribers(db, channel.id, "some text")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7002 not in tg_ids

    def test_keyword_filter_passes_matching_text(self, db):
        user = create_user(db, telegram_id=7003)
        channel = create_channel(db, username="kw_chan_mon1")
        subscribe_user_to_channel(db, user, channel, keywords="bitcoin")
        result = _get_eligible_subscribers(db, channel.id, "Bitcoin surges 20% today")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7003 in tg_ids

    def test_keyword_filter_blocks_non_matching_text(self, db):
        user = create_user(db, telegram_id=7004)
        channel = create_channel(db, username="kw_chan_mon2")
        subscribe_user_to_channel(db, user, channel, keywords="bitcoin")
        result = _get_eligible_subscribers(db, channel.id, "Latest football scores")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7004 not in tg_ids

    def test_keyword_filter_case_insensitive(self, db):
        user = create_user(db, telegram_id=7005)
        channel = create_channel(db, username="kw_chan_mon3")
        subscribe_user_to_channel(db, user, channel, keywords="Bitcoin")
        # keyword is stored as "Bitcoin", lowercased by keyword_list → "bitcoin"
        # text "bitcoin pump" should match
        result = _get_eligible_subscribers(db, channel.id, "bitcoin pump coming")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7005 in tg_ids

    def test_quiet_hours_excludes_user(self, db):
        user = create_user(db, telegram_id=7006)
        user.quiet_start = 0
        user.quiet_end = 23
        db.commit()
        channel = create_channel(db, username="quiet_chan_mon")
        subscribe_user_to_channel(db, user, channel)
        with patch("src.userbot.monitor.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12  # inside [0, 23)
            result = _get_eligible_subscribers(db, channel.id, "some text")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7006 not in tg_ids

    def test_quiet_hours_overnight_excludes_user_at_night(self, db):
        user = create_user(db, telegram_id=7007)
        user.quiet_start = 22
        user.quiet_end = 8
        db.commit()
        channel = create_channel(db, username="quiet_chan_mon2")
        subscribe_user_to_channel(db, user, channel)
        with patch("src.userbot.monitor.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 2  # 02:00 is inside overnight [22, 8)
            result = _get_eligible_subscribers(db, channel.id, "some text")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7007 not in tg_ids

    def test_quiet_hours_does_not_block_user_outside_range(self, db):
        user = create_user(db, telegram_id=7008)
        user.quiet_start = 22
        user.quiet_end = 8
        db.commit()
        channel = create_channel(db, username="quiet_chan_mon3")
        subscribe_user_to_channel(db, user, channel)
        with patch("src.userbot.monitor.datetime") as mock_dt:
            mock_dt.now.return_value.hour = 12  # 12:00 is outside overnight range
            result = _get_eligible_subscribers(db, channel.id, "some text")
        tg_ids = [tg_id for tg_id, _ in result]
        assert 7008 in tg_ids

    def test_ai_filter_value_returned_in_result(self, db):
        user = create_user(db, telegram_id=7009)
        channel = create_channel(db, username="ai_chan_mon")
        subscribe_user_to_channel(db, user, channel, ai_filter="economics only")
        result = _get_eligible_subscribers(db, channel.id, "stock market news")
        assert any(tg_id == 7009 and ai_f == "economics only" for tg_id, ai_f in result)

    def test_multiple_subscribers_all_included(self, db):
        channel = create_channel(db, username="multi_chan_mon")
        for i, tg_id in enumerate([7010, 7011, 7012]):
            user = create_user(db, telegram_id=tg_id)
            subscribe_user_to_channel(db, user, channel)
        result = _get_eligible_subscribers(db, channel.id, "breaking news")
        tg_ids = [tg_id for tg_id, _ in result]
        assert set(tg_ids) == {7010, 7011, 7012}

    def test_no_ai_filter_returns_none_in_tuple(self, db):
        user = create_user(db, telegram_id=7013)
        channel = create_channel(db, username="no_ai_chan_mon")
        subscribe_user_to_channel(db, user, channel)
        result = _get_eligible_subscribers(db, channel.id, "some text")
        assert any(tg_id == 7013 and ai_f is None for tg_id, ai_f in result)


class TestChannelLimitEnforcement:
    """Verify soft channel-limit enforcement: over-limit channels are skipped
    during delivery but UserChannel records are NOT deactivated."""

    def test_pro_user_has_no_limit(self, db):
        """Pro tier (channel_limit=None) — all channels delivered."""
        from tests.conftest import create_subscription
        user = create_user(db, telegram_id=7100)
        create_subscription(db, user, tier="pro")
        db.refresh(user)
        channels = [create_channel(db, username=f"pro_ch_{i}_7100") for i in range(5)]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)
        # Every channel should deliver
        for ch in channels:
            result = _get_eligible_subscribers(db, ch.id, "news")
            assert any(tg_id == 7100 for tg_id, _ in result), \
                f"Pro user should receive from channel {ch.username}"

    def test_free_user_within_limit_is_delivered(self, db):
        """Free user within their limit receives posts normally."""
        from src.config import config
        user = create_user(db, telegram_id=7101)
        # Add exactly CHANNEL_LIMIT_FREE channels
        channels = [
            create_channel(db, username=f"free_ch_{i}_7101")
            for i in range(config.CHANNEL_LIMIT_FREE)
        ]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)
        for ch in channels:
            result = _get_eligible_subscribers(db, ch.id, "news")
            assert any(tg_id == 7101 for tg_id, _ in result)

    def test_over_limit_channel_is_not_delivered(self, db):
        """Channel added when Pro, but subscription expired → not delivered on Free."""
        from src.config import config
        user = create_user(db, telegram_id=7102)
        # Add CHANNEL_LIMIT_FREE + 1 channels (simulating former Pro user)
        channels = [
            create_channel(db, username=f"ol_ch_{i}_7102")
            for i in range(config.CHANNEL_LIMIT_FREE + 1)
        ]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)

        # No active subscription → Free tier → only first CHANNEL_LIMIT_FREE channels delivered
        extra_channel = channels[-1]  # last-added channel is "over limit"
        result = _get_eligible_subscribers(db, extra_channel.id, "news")
        assert not any(tg_id == 7102 for tg_id, _ in result), \
            "Over-limit channel should not be delivered to free user"

    def test_over_limit_user_channel_not_deactivated(self, db):
        """Soft enforcement: UserChannel.is_active is unchanged even when over limit."""
        from src.config import config
        from src.models import UserChannel
        user = create_user(db, telegram_id=7103)
        channels = [
            create_channel(db, username=f"soft_ch_{i}_7103")
            for i in range(config.CHANNEL_LIMIT_FREE + 1)
        ]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)

        extra_channel = channels[-1]
        _get_eligible_subscribers(db, extra_channel.id, "news")  # trigger check

        # UserChannel must remain active (soft enforcement)
        uc = db.query(UserChannel).filter_by(
            user_id=user.id, channel_id=extra_channel.id
        ).first()
        assert uc.is_active is True, "Soft enforcement must not deactivate UserChannel"

    def test_first_channels_within_limit_still_delivered(self, db):
        """When over limit, the first N channels (by creation) are still delivered."""
        from src.config import config
        user = create_user(db, telegram_id=7104)
        channels = [
            create_channel(db, username=f"first_ch_{i}_7104")
            for i in range(config.CHANNEL_LIMIT_FREE + 2)
        ]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)

        # First CHANNEL_LIMIT_FREE channels should deliver
        for ch in channels[:config.CHANNEL_LIMIT_FREE]:
            result = _get_eligible_subscribers(db, ch.id, "news")
            assert any(tg_id == 7104 for tg_id, _ in result), \
                f"First-added channel {ch.username} should still be delivered"

    def test_subscription_restored_delivers_all_channels(self, db):
        """After re-subscribing to Pro, all channels are delivered again."""
        from src.config import config
        from tests.conftest import create_subscription
        user = create_user(db, telegram_id=7105)
        channels = [
            create_channel(db, username=f"restore_ch_{i}_7105")
            for i in range(config.CHANNEL_LIMIT_FREE + 1)
        ]
        for ch in channels:
            subscribe_user_to_channel(db, user, ch)

        # Activate Pro subscription
        create_subscription(db, user, tier="pro")
        db.refresh(user)

        # Now all channels should deliver
        extra_channel = channels[-1]
        result = _get_eligible_subscribers(db, extra_channel.id, "news")
        assert any(tg_id == 7105 for tg_id, _ in result), \
            "After Pro re-subscription, over-limit channel should deliver"
