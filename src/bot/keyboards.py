from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

BUTTON_CHANNELS    = "📋 Мои каналы"
BUTTON_ADD_CHANNEL = "➕ Добавить канал"
BUTTON_SUMMARY     = "📝 Саммари поста"
BUTTON_DIGEST      = "📰 Дайджест"

TIER_LABEL = {
    "basic":        "Basic ⭐",
    "pro":          "Pro 💎",
    "annual_basic": "Basic Годовой ⭐",
    "annual_pro":   "Pro Годовой 💎",
    "free":         "Free",
}

TIER_ICON = {
    "basic": "⭐", "pro": "💎",
    "annual_basic": "⭐", "annual_pro": "💎",
    "free": "",
}

SEP = "―――――――――――――――"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BUTTON_CHANNELS), KeyboardButton(BUTTON_ADD_CHANNEL)],
            [KeyboardButton(BUTTON_SUMMARY),  KeyboardButton(BUTTON_DIGEST)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Как пользоваться", callback_data="show_help")],
        [InlineKeyboardButton("🆓 Попробовать Pro бесплатно", callback_data="start_trial")],
    ])


def subscribe_keyboard() -> InlineKeyboardMarkup:
    from src.config import config
    c = config
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⭐ Basic — {c.SUBSCRIPTION_PRICE_BASIC_STARS} Stars / мес",
            callback_data="subscribe:basic",
        )],
        [InlineKeyboardButton(
            f"💎 Pro — {c.SUBSCRIPTION_PRICE_PRO_STARS} Stars / мес",
            callback_data="subscribe:pro",
        )],
        [InlineKeyboardButton(
            f"📅 Basic год — {c.SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS} Stars  −20%",
            callback_data="subscribe:annual_basic",
        )],
        [InlineKeyboardButton(
            f"📅 Pro год — {c.SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS} Stars  −20%",
            callback_data="subscribe:annual_pro",
        )],
    ])


def subscription_active_keyboard(tier: str = "basic") -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        f"🔄 Продлить {TIER_LABEL.get(tier, tier)}",
        callback_data=f"subscribe:{tier}",
    )]]
    if tier == "basic":
        rows.append([InlineKeyboardButton("⬆️ Улучшить до Pro 💎", callback_data="subscribe:pro")])
    if tier == "annual_basic":
        rows.append([InlineKeyboardButton("⬆️ Улучшить до Pro Годовой 💎", callback_data="subscribe:annual_pro")])
    return InlineKeyboardMarkup(rows)


def auto_summary_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    label = "✅ Авто-саммари: ВКЛ" if enabled else "❌ Авто-саммари: ВЫКЛ"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="toggle_auto_summary")]])


def digest_keyboard(digest_enabled: bool, auto_summary_enabled: bool) -> InlineKeyboardMarkup:
    d_label = "✅ Дайджест каждый день: ВКЛ" if digest_enabled else "❌ Дайджест каждый день: ВЫКЛ"
    a_label = "✅ Авто-саммари постов: ВКЛ" if auto_summary_enabled else "❌ Авто-саммари постов: ВЫКЛ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(d_label, callback_data="toggle_digest")],
        [InlineKeyboardButton(a_label, callback_data="toggle_auto_summary")],
        [InlineKeyboardButton("📨 Получить дайджест сейчас", callback_data="request_digest")],
    ])


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
            InlineKeyboardButton(label, callback_data=f"toggle_uc:{uc.id}"),
            InlineKeyboardButton("🗑", callback_data=f"del_uc:{uc.id}"),
        ])
    return InlineKeyboardMarkup(rows)
