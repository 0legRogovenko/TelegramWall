from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from src.bot.i18n import LANGS, btn, t, tier_label

TIER_ICON = {
    "basic": "⭐", "pro": "💎",
    "annual_basic": "⭐", "annual_pro": "💎",
    "free": "",
}

SEP = "――――――――――――"


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"lang:{code}")]  # noqa: E231
        for code, label in LANGS.items()
    ])


def main_menu(paid: bool = True, lang: str = "ru") -> ReplyKeyboardMarkup:
    """Reply keyboard. Free users see only channels + subscribe buttons."""
    if paid:
        rows = [
            [KeyboardButton(btn("channels", lang)), KeyboardButton(btn("add", lang))],
            [KeyboardButton(btn("summary", lang)), KeyboardButton(btn("digest", lang))],
        ]
    else:
        rows = [
            [KeyboardButton(btn("channels", lang)), KeyboardButton(btn("add", lang))],
            [KeyboardButton(btn("subscribe", lang))],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def start_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t("kb_help", lang), callback_data="show_help")],
        [InlineKeyboardButton(t("kb_trial", lang), callback_data="start_trial")],
    ])


def subscribe_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    from src.bot.payments import price_label
    per_month = t("kb_per_month", lang)
    annual = t("kb_annual", lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⭐ Basic — {price_label('basic')} {per_month}",
            callback_data="subscribe:basic",
        )],
        [InlineKeyboardButton(
            f"💎 Pro — {price_label('pro')} {per_month}",
            callback_data="subscribe:pro",
        )],
        [InlineKeyboardButton(
            f"📅 Basic {annual} — {price_label('annual_basic')}  −20%",
            callback_data="subscribe:annual_basic",
        )],
        [InlineKeyboardButton(
            f"📅 Pro {annual} — {price_label('annual_pro')}  −20%",
            callback_data="subscribe:annual_pro",
        )],
    ])


def subscription_active_keyboard(tier: str = "basic", lang: str = "ru") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        t("kb_renew", lang, label=tier_label(tier, lang)),
        callback_data=f"subscribe:{tier}",  # noqa: E231
    )]]
    if tier == "basic":
        rows.append([InlineKeyboardButton(
            t("kb_upgrade", lang, label=tier_label("pro", lang)),
            callback_data="subscribe:pro",
        )])
    if tier == "annual_basic":
        rows.append([InlineKeyboardButton(
            t("kb_upgrade", lang, label=tier_label("annual_pro", lang)),
            callback_data="subscribe:annual_pro",
        )])
    return InlineKeyboardMarkup(rows)


def digest_keyboard(
    digest_enabled: bool, auto_summary_enabled: bool, lang: str = "ru"
) -> InlineKeyboardMarkup:
    d_label = t("kb_digest_on" if digest_enabled else "kb_digest_off", lang)
    a_label = t("kb_autosum_on" if auto_summary_enabled else "kb_autosum_off", lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(d_label, callback_data="toggle_digest")],
        [InlineKeyboardButton(a_label, callback_data="toggle_auto_summary")],
        [InlineKeyboardButton(t("kb_digest_now", lang), callback_data="request_digest")],
    ])


def digest_channels_keyboard(
    pairs: list[tuple[int, str]], selected: set[int], lang: str = "ru"
) -> InlineKeyboardMarkup:
    """Source picker for the AI digest. pairs: [(channel_id, username)]."""
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if cid in selected else '⬜'} @{username}",
            callback_data=f"dsel:{cid}",  # noqa: E231
        )]
        for cid, username in pairs
    ]
    rows.append([
        InlineKeyboardButton(t("kb_dsel_all", lang), callback_data="dall"),
        InlineKeyboardButton(t("kb_dsel_create", lang), callback_data="dgo"),
    ])
    return InlineKeyboardMarkup(rows)


def user_channels_keyboard(user_channels: list) -> InlineKeyboardMarkup:
    rows = []
    for uc in user_channels:
        status = "✅" if uc.is_active else "⏸"
        label = f"{status} @{uc.channel.username}"
        if uc.keywords:
            label += " 🔍"
        if uc.ai_filter:
            label += " 🤖"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"toggle_uc:{uc.id}"),  # noqa: E231
            InlineKeyboardButton("🗑", callback_data=f"del_uc:{uc.id}"),  # noqa: E231
        ])

    return InlineKeyboardMarkup(rows)
