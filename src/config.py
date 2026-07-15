import hashlib
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # Bot
    TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_WEBHOOK_URL: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
    # Validates X-Telegram-Bot-Api-Secret-Token on /webhook; derived from the
    # bot token by default so no extra env var is required.
    WEBHOOK_SECRET: str = os.getenv("TELEGRAM_WEBHOOK_SECRET") or hashlib.sha256(
        TELEGRAM_BOT_TOKEN.encode()
    ).hexdigest()[:48]
    ADMIN_IDS: list[int] = [
        int(x) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip()
    ]
    # Telethon userbot
    API_ID: int = int(os.environ["TELEGRAM_API_ID"])
    API_HASH: str = os.environ["TELEGRAM_API_HASH"]
    PHONE: str = os.environ["TELEGRAM_PHONE"]
    SESSION_PATH: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "sessions",
        os.getenv("TELEGRAM_SESSION_NAME", "userbot"),
    )
    SESSION_STRING: str | None = os.getenv("TELEGRAM_SESSION_STRING")

    # Anthropic (optional — /summary and AI filter won't work without it)
    ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
    # Summaries & digest — Haiku 4.5: cheapest capable model ($1/$5 per MTok)
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    # AI relevance filter: cheap yes/no classification
    CLAUDE_FILTER_MODEL: str = os.getenv("CLAUDE_FILTER_MODEL", "claude-haiku-4-5")

    # Database
    DATABASE_URL: str = os.environ["DATABASE_URL"]

    # YooKassa (Telegram Payments)
    # Set this to enable real RUB payments; leave empty to use Telegram Stars fallback.
    YOOKASSA_PROVIDER_TOKEN: str = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")

    # Subscription tiers — Stars (XTR, used when YOOKASSA_PROVIDER_TOKEN is not set)
    SUBSCRIPTION_PRICE_BASIC_STARS: int = int(os.getenv("SUBSCRIPTION_PRICE_BASIC_STARS", "149"))
    SUBSCRIPTION_PRICE_PRO_STARS: int = int(os.getenv("SUBSCRIPTION_PRICE_PRO_STARS", "499"))
    SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS", "1430")
    )
    SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS", "4790")
    )

    # Subscription tiers — RUB in kopecks (used when YOOKASSA_PROVIDER_TOKEN is set)
    # Defaults: Basic 199₽/mo, Pro 499₽/mo; annual plans ~20% off
    SUBSCRIPTION_PRICE_BASIC_RUB: int = int(os.getenv("SUBSCRIPTION_PRICE_BASIC_RUB", "19900"))
    SUBSCRIPTION_PRICE_PRO_RUB: int = int(os.getenv("SUBSCRIPTION_PRICE_PRO_RUB", "49900"))
    SUBSCRIPTION_PRICE_ANNUAL_BASIC_RUB: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_BASIC_RUB", "199000")
    )
    SUBSCRIPTION_PRICE_ANNUAL_PRO_RUB: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_PRO_RUB", "479000")
    )
    CHANNEL_LIMIT_FREE: int = int(os.getenv("CHANNEL_LIMIT_FREE", "3"))
    CHANNEL_LIMIT_BASIC: int = int(os.getenv("CHANNEL_LIMIT_BASIC", "10"))
    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "3"))
    REFERRAL_BONUS_DAYS: int = int(os.getenv("REFERRAL_BONUS_DAYS", "3"))
    DIGEST_HOUR_UTC: int = int(os.getenv("DIGEST_HOUR_UTC", "8"))
    # Posts older than this are purged from the DB daily (chat messages remain)
    POST_RETENTION_DAYS: int = int(os.getenv("POST_RETENTION_DAYS", "3"))

    # Flask
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-me")
    PORT: int = int(os.getenv("FLASK_PORT", "5001"))
    DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"


config = Config()
