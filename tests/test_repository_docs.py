"""Repository documentation must match the deployed runtime."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
README = (ROOT / "README.md").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()

README_HEADINGS = (
    "## Возможности и тарифы",
    "## Команды",
    "## Как работает доставка",
    "## Архитектура",
    "## AI и обработка данных",
    "## Переменные окружения",
    "## Локальный запуск",
    "## Проверка изменений",
    "## Деплой через GitHub Actions",
    "## Мониторинг и устранение неполадок",
    "## Безопасность и правовые ограничения",
    "## Лицензия",
    "## Автор и контакты",
)

CONFIG_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_SECRET",
    "TELEGRAM_ADMIN_IDS",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_PHONE",
    "TELEGRAM_SESSION_NAME",
    "TELEGRAM_SESSION_STRING",
    "ANTHROPIC_API_KEY",
    "CLAUDE_MODEL",
    "CLAUDE_FILTER_MODEL",
    "AI_DAILY_SUMMARY_LIMIT",
    "DATABASE_URL",
    "YOOKASSA_PROVIDER_TOKEN",
    "SUBSCRIPTION_PRICE_BASIC_STARS",
    "SUBSCRIPTION_PRICE_PRO_STARS",
    "SUBSCRIPTION_PRICE_ANNUAL_BASIC_STARS",
    "SUBSCRIPTION_PRICE_ANNUAL_PRO_STARS",
    "SUBSCRIPTION_PRICE_BASIC_RUB",
    "SUBSCRIPTION_PRICE_PRO_RUB",
    "SUBSCRIPTION_PRICE_ANNUAL_BASIC_RUB",
    "SUBSCRIPTION_PRICE_ANNUAL_PRO_RUB",
    "CHANNEL_LIMIT_FREE",
    "CHANNEL_LIMIT_BASIC",
    "TRIAL_DAYS",
    "SUBSCRIPTION_WARNING_HOURS",
    "UPSELL_INTERVAL_DAYS",
    "UPSELL_BATCH_SIZE",
    "REFERRAL_BONUS_DAYS",
    "DIGEST_HOUR_UTC",
    "POST_RETENTION_DAYS",
    "MEDIA_MAX_MB",
    "BLOCKED_CHANNELS",
    "ADMIN_REPORT_HOUR_UTC",
    "FLASK_SECRET_KEY",
    "FLASK_PORT",
    "FLASK_DEBUG",
)


def test_readme_has_operator_sections():
    required = (
        "## Команды",
        "## Как работает доставка",
        "## Переменные окружения",
        "## Локальный запуск",
        "## Деплой через GitHub Actions",
        "## Мониторинг и устранение неполадок",
    )
    for heading in required:
        assert heading in README


def test_readme_uses_the_documented_section_order():
    headings = tuple(
        line for line in README.splitlines() if line.startswith("## ")
    )
    assert headings == README_HEADINGS


def test_environment_example_defaults_to_polling():
    assert "TELEGRAM_WEBHOOK_URL=\n" in ENV_EXAMPLE
    assert "your-app.onrender.com" not in ENV_EXAMPLE


def test_environment_example_documents_runtime_controls():
    required = (
        "TELEGRAM_WEBHOOK_SECRET",
        "CLAUDE_MODEL",
        "CLAUDE_FILTER_MODEL",
        "AI_DAILY_SUMMARY_LIMIT",
        "UPSELL_INTERVAL_DAYS",
        "UPSELL_BATCH_SIZE",
        "ADMIN_REPORT_HOUR_UTC",
    )
    for key in required:
        assert key in ENV_EXAMPLE


def test_environment_example_lists_each_config_key_once():
    for key in CONFIG_KEYS:
        assert ENV_EXAMPLE.count(f"{key}=") == 1, key

    assert ENV_EXAMPLE.count("HEARTBEAT_STALE_MINUTES=") == 1
    assert ENV_EXAMPLE.count("REALERT_HOURS=") == 1
