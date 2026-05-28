import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Bot
    TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_WEBHOOK_URL: str = os.environ["TELEGRAM_WEBHOOK_URL"]
    ADMIN_IDS: list[int] = [
        int(x) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip()
    ]
    # Telethon userbot
    API_ID: int = int(os.environ["TELEGRAM_API_ID"])
    API_HASH: str = os.environ["TELEGRAM_API_HASH"]
    PHONE: str = os.environ["TELEGRAM_PHONE"]
    SESSION_NAME: str = os.getenv("TELEGRAM_SESSION_NAME", "userbot")
    SESSION_PATH: str = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "sessions",
        os.getenv("TELEGRAM_SESSION_NAME", "userbot"),
    )
    SESSION_STRING: str | None = os.getenv("TELEGRAM_SESSION_STRING")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
    CLAUDE_MODEL: str = "claude-sonnet-4-6"

    # Database
    DATABASE_URL: str = os.environ["DATABASE_URL"]

    # Subscription tiers
    SUBSCRIPTION_PRICE_BASIC_STARS: int = int(os.getenv("SUBSCRIPTION_PRICE_BASIC_STARS", "149"))
    SUBSCRIPTION_PRICE_PRO_STARS: int = int(os.getenv("SUBSCRIPTION_PRICE_PRO_STARS", "499"))
    SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS", "1430")
    )
    SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS: int = int(
        os.getenv("SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS", "4790")
    )
    SUBSCRIPTION_DAYS: int = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
    CHANNEL_LIMIT_FREE: int = int(os.getenv("CHANNEL_LIMIT_FREE", "3"))
    CHANNEL_LIMIT_BASIC: int = int(os.getenv("CHANNEL_LIMIT_BASIC", "10"))
    TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "3"))
    REFERRAL_BONUS_DAYS: int = int(os.getenv("REFERRAL_BONUS_DAYS", "7"))
    DIGEST_HOUR_UTC: int = int(os.getenv("DIGEST_HOUR_UTC", "8"))

    # Flask
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-me")
    PORT: int = int(os.getenv("FLASK_PORT", "5001"))
    DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"


config = Config()
