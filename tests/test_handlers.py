"""Tests for bot command handlers (async, using mocked Telegram)."""
from src.bot.handlers import (
    cmd_add_channel,
    cmd_filter,
    cmd_start,
    cmd_stats,
    cmd_trial,
)
from src.models import Subscription, User, UserChannel
from tests.conftest import (
    create_channel,
    create_post,
    create_subscription,
    create_user,
    make_context,
    make_update,
    subscribe_user_to_channel,
)


# ── /start ───────────────────────────────────────────────────────────────────

class TestCmdStart:
    async def test_new_user_is_created_in_db(self, db):
        update = make_update(user_id=1001, first_name="Alice")
        await cmd_start(update, make_context())
        user = db.query(User).filter_by(telegram_id=1001).first()
        assert user is not None
        assert user.first_name == "Alice"

    async def test_sends_greeting_message(self, db):
        update = make_update(user_id=1002, first_name="Bob")
        await cmd_start(update, make_context())
        assert update.message.reply_text.called

    async def test_existing_user_not_duplicated(self, db):
        create_user(db, telegram_id=1003)
        update = make_update(user_id=1003, first_name="Carol")
        await cmd_start(update, make_context())
        count = db.query(User).filter_by(telegram_id=1003).count()
        assert count == 1

    async def test_subscribed_user_sees_subscription_info(self, db):
        user = create_user(db, telegram_id=1004)
        create_subscription(db, user, tier="basic")
        update = make_update(user_id=1004, first_name="Dave")
        await cmd_start(update, make_context())
        assert update.message.reply_text.called


# ── Referral ─────────────────────────────────────────────────────────────────

class TestReferral:
    async def test_ref_link_grants_bonus_to_both_sides(self, db):
        referrer = create_user(db, telegram_id=1501, referral_code="abc123")
        update = make_update(user_id=1502)
        await cmd_start(update, make_context(args=["ref_abc123"]))

        new_user = db.query(User).filter_by(telegram_id=1502).first()
        assert new_user.referred_by == referrer.id
        for uid in (referrer.id, new_user.id):
            sub = db.query(Subscription).filter_by(user_id=uid, stars_paid=0).first()
            assert sub is not None and sub.tier == "basic"

    async def test_ref_link_not_claimable_twice(self, db):
        create_user(db, telegram_id=1503, referral_code="xyz789")
        update = make_update(user_id=1504)
        await cmd_start(update, make_context(args=["ref_xyz789"]))
        await cmd_start(update, make_context(args=["ref_xyz789"]))
        new_user = db.query(User).filter_by(telegram_id=1504).first()
        subs = db.query(Subscription).filter_by(user_id=new_user.id).count()
        assert subs == 1


# ── /trial ────────────────────────────────────────────────────────────────────

class TestCmdTrial:
    async def test_trial_creates_pro_subscription(self, db):
        create_user(db, telegram_id=2001)
        update = make_update(user_id=2001)
        await cmd_trial(update, make_context())
        sub = (
            db.query(Subscription)
            .join(User)
            .filter(User.telegram_id == 2001)
            .first()
        )
        assert sub is not None
        assert sub.tier == "pro"

    async def test_trial_marks_trial_used(self, db):
        create_user(db, telegram_id=2002)
        update = make_update(user_id=2002)
        await cmd_trial(update, make_context())
        user = db.query(User).filter_by(telegram_id=2002).first()
        assert user.trial_used is True

    async def test_trial_blocked_when_already_used(self, db):
        create_user(db, telegram_id=2003, trial_used=True)
        update = make_update(user_id=2003)
        await cmd_trial(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "использован" in reply

    async def test_trial_blocked_when_subscription_active(self, db):
        user = create_user(db, telegram_id=2004)
        create_subscription(db, user, tier="basic")
        update = make_update(user_id=2004)
        await cmd_trial(update, make_context())
        # Should still only be one subscription
        count = (
            db.query(Subscription)
            .join(User)
            .filter(User.telegram_id == 2004)
            .count()
        )
        assert count == 1


# ── /add_channel ─────────────────────────────────────────────────────────────

class TestCmdAddChannel:
    async def test_no_args_shows_usage(self, db):
        update = make_update(user_id=3001)
        await cmd_add_channel(update, make_context(args=[]))
        assert update.message.reply_text.called

    async def test_adds_channel_and_creates_user_channel(self, db):
        create_user(db, telegram_id=3002)
        update = make_update(user_id=3002)
        await cmd_add_channel(update, make_context(args=["@newchan3002"]))
        user = db.query(User).filter_by(telegram_id=3002).first()
        uc = db.query(UserChannel).filter_by(user_id=user.id).first()
        assert uc is not None

    async def test_strips_at_sign_from_username(self, db):
        from src.models import Channel
        create_user(db, telegram_id=3003)
        update = make_update(user_id=3003)
        await cmd_add_channel(update, make_context(args=["@atchannel3003"]))
        ch = db.query(Channel).filter_by(username="atchannel3003").first()
        assert ch is not None

    async def test_rejects_username_without_at_sign(self, db):
        from src.models import Channel
        create_user(db, telegram_id=3007)
        update = make_update(user_id=3007)
        await cmd_add_channel(update, make_context(args=["noatchan3007"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "@" in reply
        assert db.query(Channel).filter_by(username="noatchan3007").first() is None

    async def test_accepts_tme_link(self, db):
        from src.models import Channel
        create_user(db, telegram_id=3008)
        update = make_update(user_id=3008)
        await cmd_add_channel(update, make_context(args=["https://t.me/linkchan3008"]))
        assert db.query(Channel).filter_by(username="linkchan3008").first() is not None

    async def test_accepts_bare_tme_link(self, db):
        from src.models import Channel
        create_user(db, telegram_id=3009)
        update = make_update(user_id=3009)
        await cmd_add_channel(update, make_context(args=["t.me/LinkChan3009"]))
        assert db.query(Channel).filter_by(username="linkchan3009").first() is not None

    async def test_duplicate_channel_shows_already_added_message(self, db):
        user = create_user(db, telegram_id=3004)
        channel = create_channel(db, username="dupechan3004")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=3004)
        await cmd_add_channel(update, make_context(args=["@dupechan3004"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "уже" in reply

    async def test_reactivates_disabled_channel(self, db):
        user = create_user(db, telegram_id=3005)
        channel = create_channel(db, username="pausedchan3005")
        subscribe_user_to_channel(db, user, channel, is_active=False)
        update = make_update(user_id=3005)
        await cmd_add_channel(update, make_context(args=["@pausedchan3005"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.is_active is True

    async def test_channel_limit_blocks_free_user(self, db):
        from src.config import config
        user = create_user(db, telegram_id=3006)
        for i in range(config.CHANNEL_LIMIT_FREE):
            ch = create_channel(db, username=f"limitchan3006_{i}")
            subscribe_user_to_channel(db, user, ch)
        update = make_update(user_id=3006)
        await cmd_add_channel(update, make_context(args=["@extrachan3006"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "Лимит" in reply or "лимит" in reply


# ── /filter ───────────────────────────────────────────────────────────────────

class TestCmdFilter:
    async def test_no_args_shows_help(self, db):
        update = make_update(user_id=4001)
        await cmd_filter(update, make_context(args=[]))
        assert update.message.reply_text.called

    async def test_unknown_channel_shows_error(self, db):
        create_user(db, telegram_id=4002)
        update = make_update(user_id=4002)
        await cmd_filter(update, make_context(args=["notexist4002", "тема"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "не найден" in reply

    async def test_set_ai_filter_blocked_for_free_user(self, db):
        user = create_user(db, telegram_id=4003)
        channel = create_channel(db, username="filterchan4003")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4003)
        await cmd_filter(update, make_context(args=["filterchan4003", "экономика"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter is None

    async def test_set_ai_filter_for_basic_user(self, db):
        user = create_user(db, telegram_id=4004)
        create_subscription(db, user, tier="basic")
        channel = create_channel(db, username="filterchan4004")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4004)
        await cmd_filter(update, make_context(args=["filterchan4004", "только", "крипта"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter == "только крипта"

    async def test_legacy_ai_prefix_still_works(self, db):
        user = create_user(db, telegram_id=4005)
        create_subscription(db, user, tier="basic")
        channel = create_channel(db, username="filterchan4005")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4005)
        await cmd_filter(update, make_context(args=["filterchan4005", "ai", "спорт"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter == "спорт"

    async def test_off_removes_filter_without_subscription(self, db):
        user = create_user(db, telegram_id=4006)
        channel = create_channel(db, username="filterchan4006")
        subscribe_user_to_channel(db, user, channel, ai_filter="старая тема")
        update = make_update(user_id=4006)
        await cmd_filter(update, make_context(args=["filterchan4006", "off"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter is None

    async def test_show_current_filter(self, db):
        user = create_user(db, telegram_id=4007)
        channel = create_channel(db, username="filterchan4007")
        subscribe_user_to_channel(db, user, channel, ai_filter="новости AI")
        update = make_update(user_id=4007)
        await cmd_filter(update, make_context(args=["filterchan4007"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "новости AI" in reply


class TestCmdStats:
    async def test_stats_no_channels_shows_empty_message(self, db):
        create_user(db, telegram_id=6001)
        update = make_update(user_id=6001)
        await cmd_stats(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "не добавлен" in reply or "нет каналов" in reply

    async def test_stats_with_channel_shows_stats(self, db):
        user = create_user(db, telegram_id=6002)
        channel = create_channel(db, username="statschan6002")
        subscribe_user_to_channel(db, user, channel)
        create_post(db, channel, text="Post 1", msg_id=1)
        create_post(db, channel, text="Post 2", msg_id=2)
        update = make_update(user_id=6002)
        await cmd_stats(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "Статистика" in reply or "статистика" in reply.lower()

    async def test_stats_shows_channel_count(self, db):
        user = create_user(db, telegram_id=6003)
        for i in range(2):
            ch = create_channel(db, username=f"statschan6003_{i}")
            subscribe_user_to_channel(db, user, ch)
        update = make_update(user_id=6003)
        await cmd_stats(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "2" in reply  # 2 channels
