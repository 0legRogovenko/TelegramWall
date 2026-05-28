"""Tests for bot command handlers (async, using mocked Telegram)."""
import pytest

from src.bot.handlers import (
    cmd_add_channel,
    cmd_filter,
    cmd_save,
    cmd_saved,
    cmd_start,
    cmd_stats,
    cmd_trial,
    cmd_unsave,
)
from src.models import Bookmark, Subscription, User, UserChannel
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
        await cmd_add_channel(update, make_context(args=["newchan3002"]))
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

    async def test_duplicate_channel_shows_already_added_message(self, db):
        user = create_user(db, telegram_id=3004)
        channel = create_channel(db, username="dupechan3004")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=3004)
        await cmd_add_channel(update, make_context(args=["dupechan3004"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "уже" in reply

    async def test_reactivates_disabled_channel(self, db):
        user = create_user(db, telegram_id=3005)
        channel = create_channel(db, username="pausedchan3005")
        subscribe_user_to_channel(db, user, channel, is_active=False)
        update = make_update(user_id=3005)
        await cmd_add_channel(update, make_context(args=["pausedchan3005"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.is_active is True

    async def test_channel_limit_blocks_free_user(self, db):
        from src.config import config
        user = create_user(db, telegram_id=3006)
        for i in range(config.CHANNEL_LIMIT_FREE):
            ch = create_channel(db, username=f"limitchan3006_{i}")
            subscribe_user_to_channel(db, user, ch)
        update = make_update(user_id=3006)
        await cmd_add_channel(update, make_context(args=["extrachan3006"]))
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
        await cmd_filter(update, make_context(args=["notexist4002", "kw"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "не найден" in reply

    async def test_set_keyword_filter(self, db):
        user = create_user(db, telegram_id=4003)
        channel = create_channel(db, username="filterchan4003")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4003)
        await cmd_filter(update, make_context(args=["filterchan4003", "python", "news"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.keywords is not None
        assert "python" in uc.keywords

    async def test_remove_keyword_filter_with_off(self, db):
        user = create_user(db, telegram_id=4004)
        channel = create_channel(db, username="filterchan4004")
        subscribe_user_to_channel(db, user, channel, keywords="old_keyword")
        update = make_update(user_id=4004)
        await cmd_filter(update, make_context(args=["filterchan4004", "off"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.keywords is None

    async def test_show_current_filters_when_no_subcommand(self, db):
        user = create_user(db, telegram_id=4005)
        channel = create_channel(db, username="filterchan4005")
        subscribe_user_to_channel(db, user, channel, keywords="bitcoin")
        update = make_update(user_id=4005)
        await cmd_filter(update, make_context(args=["filterchan4005"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "bitcoin" in reply

    async def test_ai_filter_blocked_for_free_user(self, db):
        user = create_user(db, telegram_id=4006)
        channel = create_channel(db, username="filterchan4006")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4006)
        await cmd_filter(update, make_context(args=["filterchan4006", "ai", "politics"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "недоступен" in reply or "Basic" in reply

    async def test_set_ai_filter_for_basic_user(self, db):
        user = create_user(db, telegram_id=4007)
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        channel = create_channel(db, username="filterchan4007")
        subscribe_user_to_channel(db, user, channel)
        update = make_update(user_id=4007)
        await cmd_filter(update, make_context(args=["filterchan4007", "ai", "crypto", "only"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter == "crypto only"

    async def test_remove_ai_filter_with_off(self, db):
        user = create_user(db, telegram_id=4008)
        create_subscription(db, user, tier="basic")
        db.refresh(user)
        channel = create_channel(db, username="filterchan4008")
        subscribe_user_to_channel(db, user, channel, ai_filter="economics")
        update = make_update(user_id=4008)
        await cmd_filter(update, make_context(args=["filterchan4008", "ai", "off"]))
        uc = db.query(UserChannel).filter_by(user_id=user.id, channel_id=channel.id).first()
        assert uc.ai_filter is None


# ── /save, /unsave, /saved ────────────────────────────────────────────────────

class TestCmdBookmarks:
    async def test_save_creates_bookmark(self, db):
        user = create_user(db, telegram_id=5001)
        channel = create_channel(db, username="savechan5001")
        post = create_post(db, channel, msg_id=1)
        update = make_update(user_id=5001)
        await cmd_save(update, make_context(args=[str(post.id)]))
        bm = db.query(Bookmark).filter_by(user_id=user.id, post_id=post.id).first()
        assert bm is not None

    async def test_save_no_args_shows_usage(self, db):
        create_user(db, telegram_id=5002)
        update = make_update(user_id=5002)
        await cmd_save(update, make_context(args=[]))
        assert update.message.reply_text.called

    async def test_save_nonexistent_post_shows_error(self, db):
        create_user(db, telegram_id=5003)
        update = make_update(user_id=5003)
        await cmd_save(update, make_context(args=["99999"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "не найден" in reply

    async def test_save_duplicate_shows_already_saved_message(self, db):
        user = create_user(db, telegram_id=5004)
        channel = create_channel(db, username="savechan5004")
        post = create_post(db, channel, msg_id=2)
        db.add(Bookmark(user_id=user.id, post_id=post.id))
        db.commit()
        update = make_update(user_id=5004)
        await cmd_save(update, make_context(args=[str(post.id)]))
        reply = update.message.reply_text.call_args[0][0]
        assert "уже" in reply

    async def test_save_invalid_id_shows_error(self, db):
        create_user(db, telegram_id=5005)
        update = make_update(user_id=5005)
        await cmd_save(update, make_context(args=["abc"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "числом" in reply or "ID" in reply

    async def test_unsave_removes_bookmark(self, db):
        user = create_user(db, telegram_id=5006)
        channel = create_channel(db, username="savechan5006")
        post = create_post(db, channel, msg_id=3)
        db.add(Bookmark(user_id=user.id, post_id=post.id))
        db.commit()
        update = make_update(user_id=5006)
        await cmd_unsave(update, make_context(args=[str(post.id)]))
        bm = db.query(Bookmark).filter_by(user_id=user.id, post_id=post.id).first()
        assert bm is None

    async def test_unsave_nonexistent_shows_error(self, db):
        create_user(db, telegram_id=5007)
        update = make_update(user_id=5007)
        await cmd_unsave(update, make_context(args=["99999"]))
        reply = update.message.reply_text.call_args[0][0]
        assert "не в закладках" in reply

    async def test_saved_empty_shows_empty_message(self, db):
        create_user(db, telegram_id=5008)
        update = make_update(user_id=5008)
        await cmd_saved(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "пуст" in reply.lower()

    async def test_saved_lists_bookmarks(self, db):
        user = create_user(db, telegram_id=5009)
        channel = create_channel(db, username="savechan5009")
        post = create_post(db, channel, text="Important crypto news", msg_id=4)
        db.add(Bookmark(user_id=user.id, post_id=post.id))
        db.commit()
        update = make_update(user_id=5009)
        await cmd_saved(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "Закладки" in reply


# ── /stats ────────────────────────────────────────────────────────────────────

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

    async def test_stats_shows_bookmarks_count(self, db):
        user = create_user(db, telegram_id=6004)
        channel = create_channel(db, username="statschan6004")
        subscribe_user_to_channel(db, user, channel)
        post1 = create_post(db, channel, text="Post A", msg_id=10)
        post2 = create_post(db, channel, text="Post B", msg_id=11)
        db.add(Bookmark(user_id=user.id, post_id=post1.id))
        db.add(Bookmark(user_id=user.id, post_id=post2.id))
        db.commit()
        update = make_update(user_id=6004)
        await cmd_stats(update, make_context())
        reply = update.message.reply_text.call_args[0][0]
        assert "Закладок" in reply
