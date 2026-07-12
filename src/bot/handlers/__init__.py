"""Bot command handlers, split by domain.

Public names are re-exported here so `from src.bot.handlers import cmd_start`
keeps working for the app, payments and the test suite.
"""
from src.bot.handlers.admin import cmd_admin
from src.bot.handlers.ai import (
    cmd_autosummary,
    cmd_digest,
    cmd_summary,
    cmd_summary_link,
)
from src.bot.handlers.base import (
    _ensure_referral_code,
    _get_or_create_channel,
    _get_or_create_user,
    _guard_pro,
    _help_text,
    _sync_menu_commands,
)
from src.bot.handlers.bookmarks import cmd_save, cmd_saved, cmd_stats, cmd_unsave
from src.bot.handlers.buttons import (
    btn_add_channel_prompt,
    btn_channels,
    btn_digest,
    btn_summary_prompt,
    handle_text,
)
from src.bot.handlers.callbacks import callback_handler
from src.bot.handlers.channels import (
    cmd_add_channel,
    cmd_aifilter,
    cmd_channels,
    cmd_filter,
    cmd_filter_link,
    cmd_remove_channel,
)
from src.bot.handlers.general import (
    cmd_help,
    cmd_quiet,
    cmd_refer,
    cmd_start,
    cmd_status,
    cmd_subscribe,
    cmd_trial,
)

__all__ = [
    "btn_add_channel_prompt", "btn_channels", "btn_digest", "btn_summary_prompt",
    "callback_handler", "cmd_add_channel", "cmd_admin", "cmd_aifilter",
    "cmd_autosummary", "cmd_channels", "cmd_digest", "cmd_filter",
    "cmd_filter_link", "cmd_help", "cmd_quiet", "cmd_refer",
    "cmd_remove_channel", "cmd_save", "cmd_saved", "cmd_start", "cmd_stats",
    "cmd_status", "cmd_subscribe", "cmd_summary", "cmd_summary_link",
    "cmd_trial", "cmd_unsave", "handle_text",
    "_ensure_referral_code", "_get_or_create_channel", "_get_or_create_user",
    "_guard_pro", "_help_text", "_sync_menu_commands",
]
