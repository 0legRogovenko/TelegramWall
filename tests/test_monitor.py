"""Tests for _get_eligible_subscribers logic in userbot/monitor.py."""
from src.userbot.monitor import _get_eligible_subscribers
from tests.conftest import (
    create_post,
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


class TestCleanup:
    def test_old_posts_purged_recent_kept(self, db):
        from datetime import datetime, timedelta, timezone

        from src.config import config
        from src.models import Post
        from src.userbot.monitor import _cleanup_old_posts

        user = create_user(db, telegram_id=9101)
        channel = create_channel(db, username="cleanchan9101")
        subscribe_user_to_channel(db, user, channel)
        old = create_post(db, channel, text="old", msg_id=1)
        old.created_at = datetime.now(timezone.utc) - timedelta(
            days=config.POST_RETENTION_DAYS + 1
        )
        create_post(db, channel, text="fresh", msg_id=2)
        db.commit()

        deleted = _cleanup_old_posts(db)

        assert deleted == 1
        remaining = [p.text for p in db.query(Post).filter_by(channel_id=channel.id).all()]
        assert remaining == ["fresh"]

    def test_cleanup_noop_when_nothing_old(self, db):
        from src.userbot.monitor import _cleanup_old_posts

        channel = create_channel(db, username="cleanchan9102")
        create_post(db, channel, text="fresh", msg_id=1)
        db.commit()
        assert _cleanup_old_posts(db) == 0

    def test_cleanup_keeps_undelivered_pending_posts(self, db):
        from datetime import datetime, timedelta, timezone

        from src.config import config
        from src.models import Post, PendingPost
        from src.userbot.monitor import _cleanup_old_posts

        channel = create_channel(db, username="cleanchan9103")
        old = create_post(db, channel, text="old-but-undelivered", msg_id=1)
        old.created_at = datetime.now(timezone.utc) - timedelta(
            days=config.POST_RETENTION_DAYS + 5
        )
        # This old post is still queued for delivery — must survive cleanup
        db.add(PendingPost(telegram_id=555, channel_id=channel.id, post_id=old.id))
        db.commit()

        deleted = _cleanup_old_posts(db)

        assert deleted == 0
        assert db.query(Post).filter_by(id=old.id).first() is not None
        assert db.query(PendingPost).filter_by(post_id=old.id).first() is not None


class TestMediaHelpers:
    def test_size_gate_allows_under_limit(self):
        from src.userbot.monitor import _media_size_ok

        class F:
            size = 5 * 1024 * 1024

        class M:
            file = F()

        assert _media_size_ok(M())

    def test_size_gate_blocks_over_limit(self):
        from src.config import config
        from src.userbot.monitor import _media_size_ok

        class F:
            size = (config.MEDIA_MAX_MB + 1) * 1024 * 1024

        class M:
            file = F()

        assert not _media_size_ok(M())

    def test_size_gate_allows_unknown_size(self):
        # Photos may not report a size — they are small, let them through
        from src.userbot.monitor import _media_size_ok

        class M:
            file = None

        assert _media_size_ok(M())

    def test_file_id_cache_extracts_photo_id(self):
        from unittest.mock import MagicMock
        from src.userbot.monitor import _cache_file_id, _media_file_ids

        _media_file_ids.clear()
        sent = MagicMock()
        sent.photo = [MagicMock(file_id="small"), MagicMock(file_id="big")]
        _cache_file_id(42, sent)
        assert _media_file_ids[42] == "big"  # largest PhotoSize wins
        _media_file_ids.clear()

    def test_file_id_cache_is_bounded(self):
        from unittest.mock import MagicMock
        from src.userbot.monitor import _MEDIA_CACHE_MAX, _cache_file_id, _media_file_ids

        _media_file_ids.clear()
        for i in range(_MEDIA_CACHE_MAX + 10):
            sent = MagicMock()
            sent.photo = [MagicMock(file_id=f"f{i}")]
            _cache_file_id(i, sent)
        assert len(_media_file_ids) <= _MEDIA_CACHE_MAX
        _media_file_ids.clear()

    def test_file_id_cache_never_raises(self):
        from src.userbot.monitor import _cache_file_id

        _cache_file_id(1, object())  # no photo/video attrs at all — must not raise


class TestAlbumCollapsing:
    """An album (grouped_id) must become ONE post, not N."""

    def _msg(self, msg_id, grouped_id=None, text=""):
        from unittest.mock import MagicMock
        from telethon.tl.types import Message
        m = MagicMock(spec=Message)
        m.id = msg_id
        m.message = text
        m.grouped_id = grouped_id
        from telethon.tl.types import MessageMediaPhoto
        m.media = MagicMock(spec=MessageMediaPhoto)
        return m

    def test_album_creates_single_post(self, db, monkeypatch):
        import asyncio
        from src.userbot import monitor

        channel = create_channel(db, username="album_chan")
        monkeypatch.setattr(monitor, "get_session", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)  # keep shared session alive
        monkeypatch.setattr(monitor, "_get_eligible_subscribers", lambda *a: [])

        for i, text in ((1, "подпись альбома"), (2, ""), (3, "")):
            m = self._msg(i, grouped_id=777, text=text)
            asyncio.run(monitor._process_message(None, channel, m))

        from src.models import Post
        posts = db.query(Post).filter_by(channel_id=channel.id).all()
        assert len(posts) == 1
        assert posts[0].text == "подпись альбома"

    def test_album_caption_on_later_item_is_attached(self, db, monkeypatch):
        import asyncio
        from src.userbot import monitor

        channel = create_channel(db, username="album_chan2")
        monkeypatch.setattr(monitor, "get_session", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(monitor, "_get_eligible_subscribers", lambda *a: [])

        # caption arrives on the SECOND album item
        first = self._msg(10, grouped_id=888, text="")
        second = self._msg(11, grouped_id=888, text="поздняя подпись")
        asyncio.run(monitor._process_message(None, channel, first))
        asyncio.run(monitor._process_message(None, channel, second))

        from src.models import Post
        posts = db.query(Post).filter_by(channel_id=channel.id).all()
        assert len(posts) == 1
        assert posts[0].text == "поздняя подпись"

    def test_album_siblings_advance_polling_cursor(self, db, monkeypatch):
        import asyncio
        from src.userbot import monitor

        channel = create_channel(db, username="album_chan3")
        monkeypatch.setattr(monitor, "get_session", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(monitor, "_get_eligible_subscribers", lambda *a: [])

        for i in (21, 22, 23):
            asyncio.run(monitor._process_message(None, channel, self._msg(i, grouped_id=999)))

        db.refresh(channel)
        # Cursor must pass the absorbed siblings or they would be re-fetched forever
        assert channel.last_message_id == 23

    def test_non_album_messages_unaffected(self, db, monkeypatch):
        import asyncio
        from src.userbot import monitor

        channel = create_channel(db, username="plain_chan")
        monkeypatch.setattr(monitor, "get_session", lambda: db)
        monkeypatch.setattr(db, "close", lambda: None)
        monkeypatch.setattr(monitor, "_get_eligible_subscribers", lambda *a: [])

        for i in (31, 32):
            m = self._msg(i, grouped_id=None, text=f"post {i}")
            asyncio.run(monitor._process_message(None, channel, m))

        from src.models import Post
        assert db.query(Post).filter_by(channel_id=channel.id).count() == 2


class TestMediaClassification:
    """_get_media_type must describe the post's OWN attachment.

    Telethon's .photo/.video/.document helpers deliberately fall through to a
    link's web preview, so classifying by them turned every text post with a
    link into a photo post carrying the linked article's og:image.
    """

    def _msg(self, media):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.media = media
        return m

    def test_link_preview_is_not_media(self):
        from unittest.mock import MagicMock
        from telethon.tl.types import MessageMediaWebPage
        from src.userbot.monitor import _get_media_type

        assert _get_media_type(self._msg(MagicMock(spec=MessageMediaWebPage))) is None

    def test_plain_text_post_is_not_media(self):
        from src.userbot.monitor import _get_media_type
        assert _get_media_type(self._msg(None)) is None

    def test_photo_detected(self):
        from unittest.mock import MagicMock
        from telethon.tl.types import MessageMediaPhoto
        from src.userbot.monitor import _get_media_type

        assert _get_media_type(self._msg(MagicMock(spec=MessageMediaPhoto))) == "photo"

    def test_video_detected_by_document_attribute(self):
        from unittest.mock import MagicMock
        from telethon.tl.types import DocumentAttributeVideo, MessageMediaDocument
        from src.userbot.monitor import _get_media_type

        media = MagicMock(spec=MessageMediaDocument)
        media.document = MagicMock(attributes=[MagicMock(spec=DocumentAttributeVideo)])
        assert _get_media_type(self._msg(media)) == "video"

    def test_plain_document_detected(self):
        from unittest.mock import MagicMock
        from telethon.tl.types import MessageMediaDocument
        from src.userbot.monitor import _get_media_type

        media = MagicMock(spec=MessageMediaDocument)
        media.document = MagicMock(attributes=[])
        assert _get_media_type(self._msg(media)) == "document"

    def test_original_filename_recovered(self):
        from unittest.mock import MagicMock
        from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument
        from src.userbot.monitor import _media_filename

        attr = MagicMock(spec=DocumentAttributeFilename)
        attr.file_name = "report.pdf"
        media = MagicMock(spec=MessageMediaDocument)
        media.document = MagicMock(attributes=[attr])
        # Without this PTB names raw-bytes uploads 'application.octet-stream'
        assert _media_filename(self._msg(media)) == "report.pdf"


class TestFileIdCacheInvalidation:
    def test_per_user_error_keeps_the_cached_file(self):
        from src.userbot.monitor import _is_file_error
        # A blocked recipient says nothing about the file — keep it, or every
        # remaining subscriber re-downloads and re-uploads the same MBs.
        assert not _is_file_error(Exception("Forbidden: bot was blocked by the user"))
        assert not _is_file_error(Exception("Chat not found"))

    def test_file_error_drops_the_cache(self):
        from src.userbot.monitor import _is_file_error
        assert _is_file_error(Exception("Bad Request: wrong file identifier"))
        assert _is_file_error(Exception("Bad Request: MEDIA_CAPTION_TOO_LONG"))
