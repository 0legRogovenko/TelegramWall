"""Translations for all user-facing bot texts. Russian is the fallback."""

LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "es": "🇪🇸 Español"}
DEFAULT_LANG = "ru"


def lang_of(user) -> str:
    code = getattr(user, "language", None)
    return code if code in LANGS else DEFAULT_LANG


def t(key: str, lang: str, **kw) -> str:
    entry = T[key]
    template = entry.get(lang) or entry[DEFAULT_LANG]
    return template.format(**kw) if kw else template


# ── Reply keyboard buttons (text-matched — handlers accept every language) ───

BUTTONS = {
    "channels":  {"ru": "📋 Мои каналы",        "en": "📋 My channels",         "es": "📋 Mis canales"},
    "add":       {"ru": "➕ Добавить канал",     "en": "➕ Add channel",          "es": "➕ Añadir canal"},
    "summary":   {"ru": "📝 Саммари поста",      "en": "📝 Post summary",        "es": "📝 Resumen del post"},
    "digest":    {"ru": "📰 Дайджест",           "en": "📰 Digest",              "es": "📰 Boletín"},
    "subscribe": {"ru": "⭐ Тарифы и подписка",  "en": "⭐ Plans & subscription", "es": "⭐ Planes y suscripción"},
}


def btn(key: str, lang: str) -> str:
    return BUTTONS[key].get(lang) or BUTTONS[key][DEFAULT_LANG]


def btn_variants(key: str) -> list[str]:
    return list(dict.fromkeys(BUTTONS[key].values()))


# ── Tier labels ───────────────────────────────────────────────────────────────

_TIERS = {
    "basic":        {"ru": "Basic ⭐",         "en": "Basic ⭐",        "es": "Basic ⭐"},
    "pro":          {"ru": "Pro 💎",           "en": "Pro 💎",          "es": "Pro 💎"},
    "annual_basic": {"ru": "Basic Годовой ⭐", "en": "Basic Annual ⭐", "es": "Basic Anual ⭐"},
    "annual_pro":   {"ru": "Pro Годовой 💎",   "en": "Pro Annual 💎",   "es": "Pro Anual 💎"},
    "free":         {"ru": "Free",             "en": "Free",            "es": "Free"},
}


def tier_label(tier: str, lang: str) -> str:
    entry = _TIERS.get(tier, {})
    return entry.get(lang) or entry.get(DEFAULT_LANG) or tier


# ── Pluralization ─────────────────────────────────────────────────────────────

def plural_posts(n: int, lang: str = "ru") -> str:
    if lang == "en":
        return "post" if n == 1 else "posts"
    if lang == "es":
        return "publicación" if n == 1 else "publicaciones"
    if n % 10 == 1 and n % 100 != 11:
        return "пост"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "поста"
    return "постов"


def count_new_posts(n: int, lang: str = "ru") -> str:
    p = plural_posts(n, lang)
    if lang == "en":
        return f"{n} new {p}"
    if lang == "es":
        return f"{n} {p} {'nueva' if n == 1 else 'nuevas'}"
    return f"{n} {'новый' if n == 1 else 'новых'} {p}"


# ── Texts ─────────────────────────────────────────────────────────────────────

T = {
    # Language selection
    "lang_choose": {
        "ru": "🌐 Выберите язык / Choose your language / Elige tu idioma:",
    },
    "lang_set": {
        "ru": "✅ Язык установлен: Русский",
        "en": "✅ Language set: English",
        "es": "✅ Idioma establecido: Español",
    },

    # /start
    "start_paid": {
        "ru": "👋 <b>Привет, {name}!</b>\n\n{icon} <b>{label}</b> активна до <b>{expires}</b>\n\n{sep}\n"
              "<code>/channels</code> — мои каналы\n<code>/add_channel @username</code> — добавить\n"
              "<code>/summary_ID</code> — саммари поста",
        "en": "👋 <b>Hi, {name}!</b>\n\n{icon} <b>{label}</b> active until <b>{expires}</b>\n\n{sep}\n"
              "<code>/channels</code> — my channels\n<code>/add_channel @username</code> — add one\n"
              "<code>/summary_ID</code> — post summary",
        "es": "👋 <b>¡Hola, {name}!</b>\n\n{icon} <b>{label}</b> activa hasta <b>{expires}</b>\n\n{sep}\n"
              "<code>/channels</code> — mis canales\n<code>/add_channel @username</code> — añadir\n"
              "<code>/summary_ID</code> — resumen del post",
    },
    "start_free": {
        "ru": "👋 <b>Привет, {name}!</b>\n\n<b>TelegramWall</b> следит за Telegram-каналами вместо вас — "
              "новые посты приходят прямо в этот чат.\n\n{sep}\nНачните с добавления канала:\n"
              "<code>/add_channel @username</code>",
        "en": "👋 <b>Hi, {name}!</b>\n\n<b>TelegramWall</b> watches Telegram channels for you — "
              "new posts arrive right in this chat.\n\n{sep}\nStart by adding a channel:\n"
              "<code>/add_channel @username</code>",
        "es": "👋 <b>¡Hola, {name}!</b>\n\n<b>TelegramWall</b> sigue los canales de Telegram por ti — "
              "los posts nuevos llegan directamente a este chat.\n\n{sep}\nEmpieza añadiendo un canal:\n"
              "<code>/add_channel @username</code>",
    },
    "start_hint": {
        "ru": "Кнопки управления внизу 👇",
        "en": "Control buttons below 👇",
        "es": "Botones de control abajo 👇",
    },
    "kb_help":  {"ru": "📖 Как пользоваться", "en": "📖 How to use", "es": "📖 Cómo usar"},
    "kb_trial": {"ru": "🆓 Попробовать Pro бесплатно", "en": "🆓 Try Pro for free", "es": "🆓 Prueba Pro gratis"},

    # Help
    "help": {
        "ru": "📖 <b>TelegramWall — быстрый старт</b>\n\n"
              "<b>Каналы</b>\n  <code>/add_channel @username</code> — добавить\n"
              "  <code>/channels</code> — список и управление\n\n"
              "<b>Фильтры</b>\n  <code>/filter @channel слово</code> — по ключевым словам\n"
              "  <code>/filter @channel ai тема</code> — по смыслу <i>(Basic+)</i>\n\n"
              "<b>AI-саммари</b>\n  <code>/summary_ID</code> — краткий пересказ поста\n"
              "  <code>/digest</code> — дайджест и авто-саммари <i>(Pro)</i>\n\n"
              "<b>Закладки</b>\n  <code>/save ID</code> — сохранить  ·  <code>/saved</code> — список\n\n"
              "<b>Комфорт</b>\n  <code>/quiet 23 9</code> — тишина с 23:00 до 09:00 UTC\n"
              "  <code>/stats</code> — статистика  ·  <code>/language</code> — язык\n\n{sep}\n"
              "<b>Тарифы</b>\n  Free — до {free_limit} каналов\n"
              "  ⭐ Basic — до {basic_limit} каналов + саммари\n    {basic_price} / мес\n"
              "  💎 Pro — ∞ каналов + авто-саммари + дайджест\n    {pro_price} / мес\n\n"
              "<code>/trial</code> — 3 дня Pro бесплатно\n<code>/refer</code> — пригласить → +{ref_days} дней",
        "en": "📖 <b>TelegramWall — quick start</b>\n\n"
              "<b>Channels</b>\n  <code>/add_channel @username</code> — add\n"
              "  <code>/channels</code> — list & manage\n\n"
              "<b>Filters</b>\n  <code>/filter @channel word</code> — by keywords\n"
              "  <code>/filter @channel ai topic</code> — by meaning <i>(Basic+)</i>\n\n"
              "<b>AI summaries</b>\n  <code>/summary_ID</code> — short post summary\n"
              "  <code>/digest</code> — digest & auto-summary <i>(Pro)</i>\n\n"
              "<b>Bookmarks</b>\n  <code>/save ID</code> — save  ·  <code>/saved</code> — list\n\n"
              "<b>Comfort</b>\n  <code>/quiet 23 9</code> — silence from 23:00 to 09:00 UTC\n"
              "  <code>/stats</code> — statistics  ·  <code>/language</code> — language\n\n{sep}\n"
              "<b>Plans</b>\n  Free — up to {free_limit} channels\n"
              "  ⭐ Basic — up to {basic_limit} channels + summaries\n    {basic_price} / mo\n"
              "  💎 Pro — ∞ channels + auto-summary + digest\n    {pro_price} / mo\n\n"
              "<code>/trial</code> — 3 days of Pro for free\n<code>/refer</code> — invite → +{ref_days} days",
        "es": "📖 <b>TelegramWall — inicio rápido</b>\n\n"
              "<b>Canales</b>\n  <code>/add_channel @username</code> — añadir\n"
              "  <code>/channels</code> — lista y gestión\n\n"
              "<b>Filtros</b>\n  <code>/filter @channel palabra</code> — por palabras clave\n"
              "  <code>/filter @channel ai tema</code> — por significado <i>(Basic+)</i>\n\n"
              "<b>Resúmenes AI</b>\n  <code>/summary_ID</code> — resumen breve del post\n"
              "  <code>/digest</code> — boletín y auto-resumen <i>(Pro)</i>\n\n"
              "<b>Marcadores</b>\n  <code>/save ID</code> — guardar  ·  <code>/saved</code> — lista\n\n"
              "<b>Comodidad</b>\n  <code>/quiet 23 9</code> — silencio de 23:00 a 09:00 UTC\n"
              "  <code>/stats</code> — estadísticas  ·  <code>/language</code> — idioma\n\n{sep}\n"
              "<b>Planes</b>\n  Free — hasta {free_limit} canales\n"
              "  ⭐ Basic — hasta {basic_limit} canales + resúmenes\n    {basic_price} / mes\n"
              "  💎 Pro — ∞ canales + auto-resumen + boletín\n    {pro_price} / mes\n\n"
              "<code>/trial</code> — 3 días de Pro gratis\n<code>/refer</code> — invita → +{ref_days} días",
    },

    # /status
    "status_paid": {
        "ru": "📋 <b>Статус аккаунта</b>\n\n{icon} <b>{label}</b>\n  📅 Активна до <b>{expires}</b>\n"
              "  📢 Каналов: до {limit}\n{sep}{features}{quiet}",
        "en": "📋 <b>Account status</b>\n\n{icon} <b>{label}</b>\n  📅 Active until <b>{expires}</b>\n"
              "  📢 Channels: up to {limit}\n{sep}{features}{quiet}",
        "es": "📋 <b>Estado de la cuenta</b>\n\n{icon} <b>{label}</b>\n  📅 Activa hasta <b>{expires}</b>\n"
              "  📢 Canales: hasta {limit}\n{sep}{features}{quiet}",
    },
    "status_f_summary":  {"ru": "\n  📝 Саммари по запросу ✅", "en": "\n  📝 On-demand summaries ✅", "es": "\n  📝 Resúmenes bajo demanda ✅"},
    "status_f_auto_ok":  {"ru": "\n  🤖 Авто-саммари ✅\n  📰 Дайджест ✅", "en": "\n  🤖 Auto-summary ✅\n  📰 Digest ✅", "es": "\n  🤖 Auto-resumen ✅\n  📰 Boletín ✅"},
    "status_f_auto_pro": {"ru": "\n  🤖 Авто-саммари — <i>только Pro</i>\n  📰 Дайджест — <i>только Pro</i>",
                          "en": "\n  🤖 Auto-summary — <i>Pro only</i>\n  📰 Digest — <i>Pro only</i>",
                          "es": "\n  🤖 Auto-resumen — <i>solo Pro</i>\n  📰 Boletín — <i>solo Pro</i>"},
    "status_quiet": {"ru": "\n{sep}\n  🔕 Тихий режим: {qs:02d}:00–{qe:02d}:00 UTC",
                     "en": "\n{sep}\n  🔕 Quiet mode: {qs:02d}:00–{qe:02d}:00 UTC",
                     "es": "\n{sep}\n  🔕 Modo silencio: {qs:02d}:00–{qe:02d}:00 UTC"},
    "status_free": {
        "ru": "📋 <b>Статус аккаунта</b>\n\n  Тариф: Free\n  📢 Каналов: <b>{count} / {limit}</b>\n"
              "  📝 Саммари: недоступно\n\n{sep}\n🎁 Попробуйте Pro бесплатно: <code>/trial</code>",
        "en": "📋 <b>Account status</b>\n\n  Plan: Free\n  📢 Channels: <b>{count} / {limit}</b>\n"
              "  📝 Summaries: not available\n\n{sep}\n🎁 Try Pro for free: <code>/trial</code>",
        "es": "📋 <b>Estado de la cuenta</b>\n\n  Plan: Free\n  📢 Canales: <b>{count} / {limit}</b>\n"
              "  📝 Resúmenes: no disponible\n\n{sep}\n🎁 Prueba Pro gratis: <code>/trial</code>",
    },

    # /subscribe
    "subscribe": {
        "ru": "💳 <b>Тарифы TelegramWall</b>\n\n  Free — до {free_limit} каналов, без AI\n\n{sep}\n"
              "  ⭐ <b>Basic</b> — {basic_price} / 30 дней\n    до {basic_limit} каналов · саммари по запросу\n\n"
              "  💎 <b>Pro</b> — {pro_price} / 30 дней\n    ∞ каналов · авто-саммари · дайджест\n\n"
              "  📅 Годовые тарифы — скидка 20%\n\n{sep}\n🆓 Попробовать бесплатно: <code>/trial</code>",
        "en": "💳 <b>TelegramWall plans</b>\n\n  Free — up to {free_limit} channels, no AI\n\n{sep}\n"
              "  ⭐ <b>Basic</b> — {basic_price} / 30 days\n    up to {basic_limit} channels · on-demand summaries\n\n"
              "  💎 <b>Pro</b> — {pro_price} / 30 days\n    ∞ channels · auto-summary · digest\n\n"
              "  📅 Annual plans — 20% off\n\n{sep}\n🆓 Try for free: <code>/trial</code>",
        "es": "💳 <b>Planes de TelegramWall</b>\n\n  Free — hasta {free_limit} canales, sin AI\n\n{sep}\n"
              "  ⭐ <b>Basic</b> — {basic_price} / 30 días\n    hasta {basic_limit} canales · resúmenes bajo demanda\n\n"
              "  💎 <b>Pro</b> — {pro_price} / 30 días\n    ∞ canales · auto-resumen · boletín\n\n"
              "  📅 Planes anuales — 20% de descuento\n\n{sep}\n🆓 Prueba gratis: <code>/trial</code>",
    },
    "kb_per_month": {"ru": "/ мес", "en": "/ mo", "es": "/ mes"},
    "kb_annual":    {"ru": "год", "en": "yr", "es": "año"},
    "kb_renew":     {"ru": "🔄 Продлить {label}", "en": "🔄 Renew {label}", "es": "🔄 Renovar {label}"},
    "kb_upgrade":   {"ru": "⬆️ Улучшить до {label}", "en": "⬆️ Upgrade to {label}", "es": "⬆️ Mejorar a {label}"},

    # /trial
    "trial_used": {
        "ru": "⚠️ <b>Пробный период уже использован</b>\n\nОформите подписку, чтобы продолжить:",
        "en": "⚠️ <b>Trial already used</b>\n\nSubscribe to continue:",
        "es": "⚠️ <b>La prueba ya fue utilizada</b>\n\nSuscríbete para continuar:",
    },
    "trial_have_sub": {
        "ru": "ℹ️ У вас уже есть активная подписка.\n\nИспользуйте <code>/status</code> для просмотра.",
        "en": "ℹ️ You already have an active subscription.\n\nUse <code>/status</code> to view it.",
        "es": "ℹ️ Ya tienes una suscripción activa.\n\nUsa <code>/status</code> para verla.",
    },
    "trial_ok": {
        "ru": "🎉 <b>Pro 💎 активирован!</b>\n\n  Срок: {days} дня\n  До: <b>{date}</b>\n\n{sep}\n"
              "Теперь доступно:\n  📰 Дайджест и авто-саммари — <code>/digest</code>\n  📢 Неограниченно каналов",
        "en": "🎉 <b>Pro 💎 activated!</b>\n\n  Duration: {days} days\n  Until: <b>{date}</b>\n\n{sep}\n"
              "Now available:\n  📰 Digest & auto-summary — <code>/digest</code>\n  📢 Unlimited channels",
        "es": "🎉 <b>¡Pro 💎 activado!</b>\n\n  Duración: {days} días\n  Hasta: <b>{date}</b>\n\n{sep}\n"
              "Ahora disponible:\n  📰 Boletín y auto-resumen — <code>/digest</code>\n  📢 Canales ilimitados",
    },

    "ref_bonus": {
        "ru": "🎁 <b>+{days} дней Basic!</b>\n\nПо вашей реферальной ссылке зарегистрировался новый пользователь.",
        "en": "🎁 <b>+{days} days of Basic!</b>\n\nA new user joined via your referral link.",
        "es": "🎁 <b>¡+{days} días de Basic!</b>\n\nUn nuevo usuario se registró con tu enlace de referido.",
    },

    # /refer
    "refer": {
        "ru": "🔗 <b>Реферальная программа</b>\n\nЗа каждого нового пользователя по вашей ссылке — "
              "<b>+{days} дней</b> Basic.\n\n{sep}\nВаша ссылка:\n<code>{link}</code>",
        "en": "🔗 <b>Referral program</b>\n\nFor every new user who joins via your link — "
              "<b>+{days} days</b> of Basic.\n\n{sep}\nYour link:\n<code>{link}</code>",
        "es": "🔗 <b>Programa de referidos</b>\n\nPor cada nuevo usuario que llegue con tu enlace — "
              "<b>+{days} días</b> de Basic.\n\n{sep}\nTu enlace:\n<code>{link}</code>",
    },

    # /quiet
    "quiet_active": {
        "ru": "🔕 <b>Тихий режим активен</b>\n\n  С {qs:02d}:00 до {qe:02d}:00 UTC\n\n<code>/quiet off</code> — выключить",
        "en": "🔕 <b>Quiet mode is on</b>\n\n  From {qs:02d}:00 to {qe:02d}:00 UTC\n\n<code>/quiet off</code> — turn off",
        "es": "🔕 <b>Modo silencio activado</b>\n\n  De {qs:02d}:00 a {qe:02d}:00 UTC\n\n<code>/quiet off</code> — desactivar",
    },
    "quiet_off_state": {
        "ru": "🔔 <b>Тихий режим выключен</b>\n\nВключить: <code>/quiet ЧЧ ЧЧ</code>\n"
              "Пример: <code>/quiet 23 9</code> — тишина с 23:00 до 09:00 UTC",
        "en": "🔔 <b>Quiet mode is off</b>\n\nTurn on: <code>/quiet HH HH</code>\n"
              "Example: <code>/quiet 23 9</code> — silence from 23:00 to 09:00 UTC",
        "es": "🔔 <b>Modo silencio desactivado</b>\n\nActivar: <code>/quiet HH HH</code>\n"
              "Ejemplo: <code>/quiet 23 9</code> — silencio de 23:00 a 09:00 UTC",
    },
    "quiet_disabled": {
        "ru": "🔔 <b>Тихий режим выключен</b>\n\nПосты приходят в любое время.",
        "en": "🔔 <b>Quiet mode turned off</b>\n\nPosts arrive at any time.",
        "es": "🔔 <b>Modo silencio desactivado</b>\n\nLos posts llegan a cualquier hora.",
    },
    "quiet_two_hours": {
        "ru": "Укажите два часа: <code>/quiet ЧЧ ЧЧ</code>\nПример: <code>/quiet 23 9</code>",
        "en": "Provide two hours: <code>/quiet HH HH</code>\nExample: <code>/quiet 23 9</code>",
        "es": "Indica dos horas: <code>/quiet HH HH</code>\nEjemplo: <code>/quiet 23 9</code>",
    },
    "quiet_bad_hours": {
        "ru": "❌ Часы должны быть числами от 0 до 23.",
        "en": "❌ Hours must be numbers from 0 to 23.",
        "es": "❌ Las horas deben ser números de 0 a 23.",
    },
    "quiet_enabled": {
        "ru": "🔕 <b>Тихий режим включён</b>\n\n  С {qs:02d}:00 до {qe:02d}:00 UTC\n\n<code>/quiet off</code> — выключить",
        "en": "🔕 <b>Quiet mode turned on</b>\n\n  From {qs:02d}:00 to {qe:02d}:00 UTC\n\n<code>/quiet off</code> — turn off",
        "es": "🔕 <b>Modo silencio activado</b>\n\n  De {qs:02d}:00 a {qe:02d}:00 UTC\n\n<code>/quiet off</code> — desactivar",
    },

    # Channels
    "add_usage": {
        "ru": "📢 <b>Добавление канала</b>\n\nПример: <code>/add_channel @durov</code>",
        "en": "📢 <b>Add a channel</b>\n\nExample: <code>/add_channel @durov</code>",
        "es": "📢 <b>Añadir un canal</b>\n\nEjemplo: <code>/add_channel @durov</code>",
    },
    "add_need_at": {
        "ru": "❌ <b>Канал указывается через @</b>\n\nПример: <code>/add_channel @durov</code>",
        "en": "❌ <b>Channel must start with @</b>\n\nExample: <code>/add_channel @durov</code>",
        "es": "❌ <b>El canal debe empezar con @</b>\n\nEjemplo: <code>/add_channel @durov</code>",
    },
    "add_limit": {
        "ru": "❌ <b>Лимит каналов</b>\n\n  {label} — до <b>{limit}</b> каналов.\n\n{upgrade}",
        "en": "❌ <b>Channel limit reached</b>\n\n  {label} — up to <b>{limit}</b> channels.\n\n{upgrade}",
        "es": "❌ <b>Límite de canales</b>\n\n  {label} — hasta <b>{limit}</b> canales.\n\n{upgrade}",
    },
    "add_upgrade_free": {"ru": "Оформите подписку:", "en": "Subscribe:", "es": "Suscríbete:"},
    "add_upgrade_pro":  {"ru": "Перейдите на Pro 💎:", "en": "Upgrade to Pro 💎:", "es": "Pasa a Pro 💎:"},
    "add_already": {
        "ru": "ℹ️ Канал <b>@{username}</b> уже в вашем списке.",
        "en": "ℹ️ Channel <b>@{username}</b> is already in your list.",
        "es": "ℹ️ El canal <b>@{username}</b> ya está en tu lista.",
    },
    "add_reenabled": {
        "ru": "✅ <b>@{username} включён</b>\n\nПосты снова будут приходить сюда.",
        "en": "✅ <b>@{username} enabled</b>\n\nPosts will arrive here again.",
        "es": "✅ <b>@{username} activado</b>\n\nLos posts volverán a llegar aquí.",
    },
    "add_done": {
        "ru": "✅ <b>@{username} добавлен</b>\n\nНовые посты будут приходить сюда.\n\n"
              "💡 Настроить фильтр: <code>/filter @{username}</code>",
        "en": "✅ <b>@{username} added</b>\n\nNew posts will arrive here.\n\n"
              "💡 Set up a filter: <code>/filter @{username}</code>",
        "es": "✅ <b>@{username} añadido</b>\n\nLos posts nuevos llegarán aquí.\n\n"
              "💡 Configura un filtro: <code>/filter @{username}</code>",
    },
    "ch_none": {
        "ru": "📋 <b>Каналы не добавлены</b>\n\nДобавьте первый:\n<code>/add_channel @username</code>",
        "en": "📋 <b>No channels yet</b>\n\nAdd your first one:\n<code>/add_channel @username</code>",
        "es": "📋 <b>Sin canales todavía</b>\n\nAñade el primero:\n<code>/add_channel @username</code>",
    },
    "ch_list": {
        "ru": "📋 <b>Мои каналы</b>  <i>{active} / {limit}</i>\n\n<b>Как управлять:</b>\n"
              "  • Нажмите на канал в списке ниже — включить ✅ / выключить ⏸ доставку постов\n"
              "  • Нажмите 🗑 рядом с каналом — удалить его\n"
              "  • 🔍 — стоит у каналов с фильтром слов\n  • 🤖 — стоит у каналов с AI-фильтром\n\n"
              "<b>Настроить фильтр канала:</b>\n{filters}",
        "en": "📋 <b>My channels</b>  <i>{active} / {limit}</i>\n\n<b>How to manage:</b>\n"
              "  • Tap a channel below — enable ✅ / pause ⏸ post delivery\n"
              "  • Tap 🗑 next to a channel — remove it\n"
              "  • 🔍 — channel has a keyword filter\n  • 🤖 — channel has an AI filter\n\n"
              "<b>Set up a channel filter:</b>\n{filters}",
        "es": "📋 <b>Mis canales</b>  <i>{active} / {limit}</i>\n\n<b>Cómo gestionar:</b>\n"
              "  • Toca un canal abajo — activar ✅ / pausar ⏸ la entrega\n"
              "  • Toca 🗑 junto a un canal — eliminarlo\n"
              "  • 🔍 — canal con filtro de palabras\n  • 🤖 — canal con filtro AI\n\n"
              "<b>Configurar filtro de canal:</b>\n{filters}",
    },
    "rm_usage": {
        "ru": "Использование: <code>/remove_channel @username</code>",
        "en": "Usage: <code>/remove_channel @username</code>",
        "es": "Uso: <code>/remove_channel @username</code>",
    },
    "rm_not_found": {
        "ru": "❌ Канал <b>@{username}</b> не найден.",
        "en": "❌ Channel <b>@{username}</b> not found.",
        "es": "❌ Canal <b>@{username}</b> no encontrado.",
    },
    "rm_not_in_list": {
        "ru": "ℹ️ Канал <b>@{username}</b> не в вашем списке.",
        "en": "ℹ️ Channel <b>@{username}</b> is not in your list.",
        "es": "ℹ️ El canal <b>@{username}</b> no está en tu lista.",
    },
    "rm_done": {
        "ru": "🗑 <b>@{username} удалён</b>",
        "en": "🗑 <b>@{username} removed</b>",
        "es": "🗑 <b>@{username} eliminado</b>",
    },
    "ch_deleted_all": {
        "ru": "📋 <b>Каналы удалены</b>\n\nДобавьте первый: <code>/add_channel @username</code>",
        "en": "📋 <b>Channels removed</b>\n\nAdd one: <code>/add_channel @username</code>",
        "es": "📋 <b>Canales eliminados</b>\n\nAñade uno: <code>/add_channel @username</code>",
    },

    # Filters
    "flt_help": {
        "ru": "🔍 <b>Фильтр постов</b>\n\n<b>Ключевые слова</b> — пропускать только нужные:\n"
              "  <code>/filter @channel слово1 слово2</code>\n  <code>/filter @channel off</code> — убрать\n\n"
              "<b>AI по теме</b> <i>(Basic / Pro)</i> — фильтр по смыслу:\n"
              "  <code>/filter @channel ai только про экономику</code>\n"
              "  <code>/filter @channel ai off</code> — убрать\n\nПосмотреть: <code>/filter @channel</code>",
        "en": "🔍 <b>Post filter</b>\n\n<b>Keywords</b> — deliver only matching posts:\n"
              "  <code>/filter @channel word1 word2</code>\n  <code>/filter @channel off</code> — remove\n\n"
              "<b>AI by topic</b> <i>(Basic / Pro)</i> — filter by meaning:\n"
              "  <code>/filter @channel ai only about economics</code>\n"
              "  <code>/filter @channel ai off</code> — remove\n\nView: <code>/filter @channel</code>",
        "es": "🔍 <b>Filtro de posts</b>\n\n<b>Palabras clave</b> — entregar solo los que coincidan:\n"
              "  <code>/filter @channel palabra1 palabra2</code>\n  <code>/filter @channel off</code> — quitar\n\n"
              "<b>AI por tema</b> <i>(Basic / Pro)</i> — filtro por significado:\n"
              "  <code>/filter @channel ai solo economía</code>\n"
              "  <code>/filter @channel ai off</code> — quitar\n\nVer: <code>/filter @channel</code>",
    },
    "flt_ch_not_found": {
        "ru": "❌ Канал <b>@{username}</b> не найден. Сначала добавьте его.",
        "en": "❌ Channel <b>@{username}</b> not found. Add it first.",
        "es": "❌ Canal <b>@{username}</b> no encontrado. Añádelo primero.",
    },
    "flt_show": {
        "ru": "🔍 <b>Фильтры @{username}</b>\n\n  📝 Слова: {kw}\n  🤖 AI: {ai}\n\n{sep}\n"
              "<b>Настроить:</b>\n<code>/filter @{username} слово1 слово2</code> — фильтр слов\n"
              "<code>/filter @{username} ai тема</code> — AI-фильтр <i>(Basic+)</i>\n\n"
              "<b>Убрать:</b>\n<code>/filter @{username} off</code> — слова\n"
              "<code>/filter @{username} ai off</code> — AI",
        "en": "🔍 <b>Filters for @{username}</b>\n\n  📝 Words: {kw}\n  🤖 AI: {ai}\n\n{sep}\n"
              "<b>Set:</b>\n<code>/filter @{username} word1 word2</code> — keyword filter\n"
              "<code>/filter @{username} ai topic</code> — AI filter <i>(Basic+)</i>\n\n"
              "<b>Remove:</b>\n<code>/filter @{username} off</code> — keywords\n"
              "<code>/filter @{username} ai off</code> — AI",
        "es": "🔍 <b>Filtros de @{username}</b>\n\n  📝 Palabras: {kw}\n  🤖 AI: {ai}\n\n{sep}\n"
              "<b>Configurar:</b>\n<code>/filter @{username} palabra1 palabra2</code> — filtro de palabras\n"
              "<code>/filter @{username} ai tema</code> — filtro AI <i>(Basic+)</i>\n\n"
              "<b>Quitar:</b>\n<code>/filter @{username} off</code> — palabras\n"
              "<code>/filter @{username} ai off</code> — AI",
    },
    "flt_ai_pro": {
        "ru": "🤖 <b>AI-фильтр</b>\n\nДоступен на тарифах Basic ⭐ и Pro 💎.",
        "en": "🤖 <b>AI filter</b>\n\nAvailable on Basic ⭐ and Pro 💎 plans.",
        "es": "🤖 <b>Filtro AI</b>\n\nDisponible en los planes Basic ⭐ y Pro 💎.",
    },
    "flt_ai_current": {
        "ru": "🤖 <b>AI-фильтр @{username}</b>\n\n  <code>{ai}</code>\n\n"
              "<code>/filter @{username} ai off</code> — удалить",
        "en": "🤖 <b>AI filter for @{username}</b>\n\n  <code>{ai}</code>\n\n"
              "<code>/filter @{username} ai off</code> — remove",
        "es": "🤖 <b>Filtro AI de @{username}</b>\n\n  <code>{ai}</code>\n\n"
              "<code>/filter @{username} ai off</code> — quitar",
    },
    "flt_ai_not_set": {
        "ru": "🤖 AI-фильтр для <b>@{username}</b> не задан.\n\n"
              "Пример: <code>/filter @{username} ai только про экономику</code>",
        "en": "🤖 No AI filter set for <b>@{username}</b>.\n\n"
              "Example: <code>/filter @{username} ai only about economics</code>",
        "es": "🤖 No hay filtro AI para <b>@{username}</b>.\n\n"
              "Ejemplo: <code>/filter @{username} ai solo economía</code>",
    },
    "flt_ai_removed": {
        "ru": "✅ AI-фильтр <b>@{username}</b> удалён.",
        "en": "✅ AI filter for <b>@{username}</b> removed.",
        "es": "✅ Filtro AI de <b>@{username}</b> eliminado.",
    },
    "flt_ai_set": {
        "ru": "✅ <b>AI-фильтр установлен</b>\n\n  @{username} → <code>{ai}</code>",
        "en": "✅ <b>AI filter set</b>\n\n  @{username} → <code>{ai}</code>",
        "es": "✅ <b>Filtro AI configurado</b>\n\n  @{username} → <code>{ai}</code>",
    },
    "flt_kw_removed": {
        "ru": "✅ Фильтр слов <b>@{username}</b> удалён.",
        "en": "✅ Keyword filter for <b>@{username}</b> removed.",
        "es": "✅ Filtro de palabras de <b>@{username}</b> eliminado.",
    },
    "flt_kw_set": {
        "ru": "✅ <b>Фильтр установлен</b>\n\n  @{username} → <code>{kw}</code>\n\n"
              "Приходят только посты с этими словами.",
        "en": "✅ <b>Filter set</b>\n\n  @{username} → <code>{kw}</code>\n\n"
              "Only posts containing these words will arrive.",
        "es": "✅ <b>Filtro configurado</b>\n\n  @{username} → <code>{kw}</code>\n\n"
              "Solo llegarán posts con estas palabras.",
    },

    # Summary
    "sum_unavailable": {
        "ru": "📝 <b>Саммари недоступно</b>\n\nДоступно на тарифах Basic ⭐ и Pro 💎.",
        "en": "📝 <b>Summaries unavailable</b>\n\nAvailable on Basic ⭐ and Pro 💎 plans.",
        "es": "📝 <b>Resúmenes no disponibles</b>\n\nDisponible en los planes Basic ⭐ y Pro 💎.",
    },
    "sum_usage": {
        "ru": "📝 Использование: <code>/summary &lt;ID поста&gt;</code>\n\nID указан под каждым постом.",
        "en": "📝 Usage: <code>/summary &lt;post ID&gt;</code>\n\nThe ID is shown under every post.",
        "es": "📝 Uso: <code>/summary &lt;ID del post&gt;</code>\n\nEl ID aparece bajo cada post.",
    },
    "sum_bad_id":    {"ru": "❌ ID поста должен быть числом.", "en": "❌ Post ID must be a number.", "es": "❌ El ID debe ser un número."},
    "sum_not_found": {"ru": "❌ Пост <b>#{id}</b> не найден.", "en": "❌ Post <b>#{id}</b> not found.", "es": "❌ Post <b>#{id}</b> no encontrado."},
    "sum_no_text":   {"ru": "❌ В этом посте нет текста для саммари.", "en": "❌ This post has no text to summarize.", "es": "❌ Este post no tiene texto para resumir."},
    "sum_header":    {"ru": "📝 <b>Саммари #{id}</b>\n\n{text}", "en": "📝 <b>Summary #{id}</b>\n\n{text}", "es": "📝 <b>Resumen #{id}</b>\n\n{text}"},
    "sum_generating": {"ru": "⏳ Генерирую саммари…", "en": "⏳ Generating summary…", "es": "⏳ Generando resumen…"},
    "sum_error":     {"ru": "❌ <b>Ошибка:</b> {err}", "en": "❌ <b>Error:</b> {err}", "es": "❌ <b>Error:</b> {err}"},

    # Digest
    "digest_unavailable": {
        "ru": "📰 <b>AI-режим недоступен</b>\n\nАвто-саммари и дайджест доступны на тарифе Pro 💎.",
        "en": "📰 <b>AI mode unavailable</b>\n\nAuto-summary and digest are available on the Pro 💎 plan.",
        "es": "📰 <b>Modo AI no disponible</b>\n\nAuto-resumen y boletín están disponibles en el plan Pro 💎.",
    },
    "digest_settings": {
        "ru": "📰 <b>AI-режим</b>\n\n  <b>Авто-саммари</b> — краткое изложение с каждым постом\n"
              "  <b>Дайджест</b> — AI-сводка за день в {hour:02d}:00 UTC\n\nНастройте кнопками ниже:",
        "en": "📰 <b>AI mode</b>\n\n  <b>Auto-summary</b> — a short summary with every post\n"
              "  <b>Digest</b> — daily AI overview at {hour:02d}:00 UTC\n\nConfigure below:",
        "es": "📰 <b>Modo AI</b>\n\n  <b>Auto-resumen</b> — resumen breve con cada post\n"
              "  <b>Boletín</b> — resumen AI diario a las {hour:02d}:00 UTC\n\nConfigura abajo:",
    },
    "kb_digest_on":  {"ru": "✅ Дайджест каждый день: ВКЛ", "en": "✅ Daily digest: ON", "es": "✅ Boletín diario: SÍ"},
    "kb_digest_off": {"ru": "❌ Дайджест каждый день: ВЫКЛ", "en": "❌ Daily digest: OFF", "es": "❌ Boletín diario: NO"},
    "kb_autosum_on":  {"ru": "✅ Авто-саммари постов: ВКЛ", "en": "✅ Auto-summary: ON", "es": "✅ Auto-resumen: SÍ"},
    "kb_autosum_off": {"ru": "❌ Авто-саммари постов: ВЫКЛ", "en": "❌ Auto-summary: OFF", "es": "❌ Auto-resumen: NO"},
    "kb_digest_now": {"ru": "📨 Получить дайджест сейчас", "en": "📨 Get digest now", "es": "📨 Recibir boletín ahora"},
    "digest_choose": {
        "ru": "📰 <b>Дайджест: выбор источников</b>\n\nОтметьте каналы, по которым составить дайджест, "
              "и нажмите «Создать»:",
        "en": "📰 <b>Digest: choose sources</b>\n\nSelect the channels to include, then tap “Create”:",
        "es": "📰 <b>Boletín: elige fuentes</b>\n\nMarca los canales a incluir y pulsa «Crear»:",
    },
    "kb_dsel_all":    {"ru": "☑️ Все / ничего", "en": "☑️ All / none", "es": "☑️ Todo / nada"},
    "kb_dsel_create": {"ru": "▶️ Создать дайджест", "en": "▶️ Create digest", "es": "▶️ Crear boletín"},
    "digest_no_selection": {
        "ru": "Выберите хотя бы один канал",
        "en": "Select at least one channel",
        "es": "Elige al menos un canal",
    },
    "digest_generating": {
        "ru": "⏳ Генерирую AI-дайджест…",
        "en": "⏳ Generating AI digest…",
        "es": "⏳ Generando el boletín AI…",
    },
    "digest_empty": {
        "ru": "📰 <b>Нет новых постов</b>\n\nЗа последние 24 часа по выбранным каналам ничего не поступало.",
        "en": "📰 <b>No new posts</b>\n\nNothing arrived from the selected channels in the last 24 hours.",
        "es": "📰 <b>Sin posts nuevos</b>\n\nNo llegó nada de los canales seleccionados en las últimas 24 horas.",
    },
    "digest_header": {
        "ru": "📰 <b>Дайджест {date}</b>",
        "en": "📰 <b>Digest {date}</b>",
        "es": "📰 <b>Boletín {date}</b>",
    },
    "pro_only_alert": {
        "ru": "❌ Доступно только на Pro 💎",
        "en": "❌ Pro 💎 only",
        "es": "❌ Solo para Pro 💎",
    },

    # Bookmarks & stats
    "save_usage":  {"ru": "📌 Использование: <code>/save &lt;ID поста&gt;</code>", "en": "📌 Usage: <code>/save &lt;post ID&gt;</code>", "es": "📌 Uso: <code>/save &lt;ID del post&gt;</code>"},
    "save_bad_id": {"ru": "❌ ID должен быть числом.", "en": "❌ ID must be a number.", "es": "❌ El ID debe ser un número."},
    "save_already": {"ru": "ℹ️ Пост <b>#{id}</b> уже в закладках.", "en": "ℹ️ Post <b>#{id}</b> is already bookmarked.", "es": "ℹ️ El post <b>#{id}</b> ya está guardado."},
    "save_done": {
        "ru": "📌 <b>Пост #{id} сохранён</b>\n\nПосмотреть все: <code>/saved</code>",
        "en": "📌 <b>Post #{id} saved</b>\n\nView all: <code>/saved</code>",
        "es": "📌 <b>Post #{id} guardado</b>\n\nVer todos: <code>/saved</code>",
    },
    "unsave_usage": {"ru": "Использование: <code>/unsave &lt;ID поста&gt;</code>", "en": "Usage: <code>/unsave &lt;post ID&gt;</code>", "es": "Uso: <code>/unsave &lt;ID del post&gt;</code>"},
    "unsave_not_in": {"ru": "ℹ️ Пост <b>#{id}</b> не в закладках.", "en": "ℹ️ Post <b>#{id}</b> is not bookmarked.", "es": "ℹ️ El post <b>#{id}</b> no está guardado."},
    "unsave_done": {"ru": "🗑 Пост <b>#{id}</b> удалён из закладок.", "en": "🗑 Post <b>#{id}</b> removed from bookmarks.", "es": "🗑 Post <b>#{id}</b> eliminado de guardados."},
    "saved_empty": {
        "ru": "📌 <b>Закладки пусты</b>\n\nСохраняйте посты командой <code>/save ID</code>",
        "en": "📌 <b>No bookmarks</b>\n\nSave posts with <code>/save ID</code>",
        "es": "📌 <b>Sin marcadores</b>\n\nGuarda posts con <code>/save ID</code>",
    },
    "saved_header": {"ru": "📌 <b>Закладки</b> — {n} {p}", "en": "📌 <b>Bookmarks</b> — {n} {p}", "es": "📌 <b>Marcadores</b> — {n} {p}"},
    "media_stub":  {"ru": "<i>[медиа]</i>", "en": "<i>[media]</i>", "es": "<i>[multimedia]</i>"},
    "stats_empty": {
        "ru": "📊 <b>Статистика</b>\n\nКаналы ещё не добавлены.\n\n<code>/add_channel @username</code> — начать",
        "en": "📊 <b>Statistics</b>\n\nNo channels added yet.\n\n<code>/add_channel @username</code> — start",
        "es": "📊 <b>Estadísticas</b>\n\nAún no hay canales.\n\n<code>/add_channel @username</code> — empezar",
    },
    "stats_body": {
        "ru": "📊 <b>Ваша статистика</b>\n\n  📢 Каналов: <b>{channels}</b>\n  📝 Постов всего: <b>{total}</b>\n"
              "  📅 За 7 дней: <b>{week}</b>\n  📌 Закладок: <b>{bookmarks}</b>",
        "en": "📊 <b>Your statistics</b>\n\n  📢 Channels: <b>{channels}</b>\n  📝 Posts total: <b>{total}</b>\n"
              "  📅 Last 7 days: <b>{week}</b>\n  📌 Bookmarks: <b>{bookmarks}</b>",
        "es": "📊 <b>Tus estadísticas</b>\n\n  📢 Canales: <b>{channels}</b>\n  📝 Posts en total: <b>{total}</b>\n"
              "  📅 Últimos 7 días: <b>{week}</b>\n  📌 Marcadores: <b>{bookmarks}</b>",
    },
    "stats_top": {"ru": "<b>Топ каналов за неделю:</b>", "en": "<b>Top channels this week:</b>", "es": "<b>Canales top de la semana:</b>"},

    # Button prompts (reply keyboard flows)
    "prompt_channel": {
        "ru": "📢 Введите username канала через @:\n<i>Например: @durov</i>",
        "en": "📢 Enter the channel username with @:\n<i>For example: @durov</i>",
        "es": "📢 Escribe el usuario del canal con @:\n<i>Por ejemplo: @durov</i>",
    },
    "prompt_channel_retry": {
        "ru": "❌ <b>Канал указывается через @</b>\n\nПопробуйте ещё раз. Например: <code>@durov</code>",
        "en": "❌ <b>The channel must start with @</b>\n\nTry again. For example: <code>@durov</code>",
        "es": "❌ <b>El canal debe empezar con @</b>\n\nInténtalo de nuevo. Por ejemplo: <code>@durov</code>",
    },
    "prompt_sum_id": {
        "ru": "📝 Введите ID поста:\n<i>ID указан под каждым сообщением от бота.</i>",
        "en": "📝 Enter the post ID:\n<i>The ID is shown under every bot message.</i>",
        "es": "📝 Escribe el ID del post:\n<i>El ID aparece bajo cada mensaje del bot.</i>",
    },
    "prompt_sum_id_retry": {
        "ru": "❌ ID поста должен быть числом. Попробуйте ещё раз.",
        "en": "❌ The post ID must be a number. Try again.",
        "es": "❌ El ID debe ser un número. Inténtalo de nuevo.",
    },

    # Monitor (deliveries)
    "batch_header": {"ru": "📢 <b>{label}</b> — {phrase}", "en": "📢 <b>{label}</b> — {phrase}", "es": "📢 <b>{label}</b> — {phrase}"},
    "open_channel": {"ru": "🔗 <a href=\"https://t.me/{u}\">Открыть @{u}</a>", "en": "🔗 <a href=\"https://t.me/{u}\">Open @{u}</a>", "es": "🔗 <a href=\"https://t.me/{u}\">Abrir @{u}</a>"},
    "auto_summary_msg": {
        "ru": "📝 <b>Саммари</b> из <b>{label}</b>:\n\n{text}\n\nЗапросить снова: /summary_{id}",
        "en": "📝 <b>Summary</b> from <b>{label}</b>:\n\n{text}\n\nRequest again: /summary_{id}",
        "es": "📝 <b>Resumen</b> de <b>{label}</b>:\n\n{text}\n\nPedir de nuevo: /summary_{id}",
    },

    # Command menu descriptions
    "cmd_start":     {"ru": "Начало работы", "en": "Get started", "es": "Empezar"},
    "cmd_channels":  {"ru": "Мои каналы", "en": "My channels", "es": "Mis canales"},
    "cmd_add":       {"ru": "Добавить канал", "en": "Add a channel", "es": "Añadir canal"},
    "cmd_subscribe": {"ru": "Тарифы и подписка", "en": "Plans & subscription", "es": "Planes y suscripción"},
    "cmd_help":      {"ru": "Все команды", "en": "All commands", "es": "Todos los comandos"},
    "cmd_language":  {"ru": "Язык / Language", "en": "Language", "es": "Idioma"},
    "cmd_summary":   {"ru": "Саммари поста по ID", "en": "Post summary by ID", "es": "Resumen por ID"},
    "cmd_filter":    {"ru": "Фильтр для канала", "en": "Channel filter", "es": "Filtro de canal"},
    "cmd_digest":    {"ru": "AI-режим: дайджест и авто-саммари", "en": "AI mode: digest & auto-summary", "es": "Modo AI: boletín y auto-resumen"},
}
