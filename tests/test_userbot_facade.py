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
