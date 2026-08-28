"""Stable imports exposed by the userbot runtime facade."""
from src.userbot import monitor


PUBLIC_RUNTIME_API = (
    "start_userbot",
    "refresh_channels",
    "send_digest_now",
    "flush_buffer_on_shutdown",
)

COMPATIBILITY_HELPERS = (
    "MAX_MESSAGE_CHARS",
    "_split_message",
    "_get_eligible_subscribers",
    "_get_eligible_subscriber_details",
    "_get_media_type",
    "_media_filename",
    "_media_size_ok",
    "_cache_file_id",
    "_cleanup_old_posts",
)


def test_monitor_exposes_runtime_api():
    for name in PUBLIC_RUNTIME_API:
        assert callable(getattr(monitor, name))


def test_monitor_keeps_tested_compatibility_helpers():
    for name in COMPATIBILITY_HELPERS:
        assert hasattr(monitor, name)


def test_subscriber_helpers_come_from_focused_module():
    from src.userbot import subscribers

    assert monitor._get_eligible_subscribers is subscribers.get_eligible_subscribers
    assert (
        monitor._get_eligible_subscriber_details
        is subscribers.get_eligible_subscriber_details
    )


def test_media_helpers_come_from_focused_module():
    from src.userbot import media

    assert monitor._get_media_type is media.get_media_type
    assert monitor._media_filename is media.media_filename
    assert monitor._media_size_ok is media.media_size_ok
    assert monitor._cache_file_id is media.cache_file_id
    assert monitor._media_file_ids is media.MEDIA_FILE_IDS


def test_delivery_runtime_comes_from_focused_module():
    from src.userbot import delivery

    assert monitor._process_message is delivery.process_message
    assert monitor.flush_buffer_on_shutdown is delivery.flush_buffer_on_shutdown
    assert monitor._flush_pending is delivery.flush_pending
