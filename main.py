"""Entry point.

Webhook mode  — when TELEGRAM_WEBHOOK_URL starts with https://
               Flask handles incoming updates on /webhook.
Polling mode  — fallback for local development (no HTTPS required).
               Flask still starts for the /health endpoint.
"""
import asyncio
import logging
import threading

from src.bot.app import build_ptb_app, flask_app
from src.config import config
from src.database import init_db
from src.userbot.monitor import start_userbot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("src.userbot.monitor").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

_USE_WEBHOOK = config.TELEGRAM_WEBHOOK_URL.startswith("https://")


async def _async_main(loop: asyncio.AbstractEventLoop) -> None:
    from telegram import BotCommand
    ptb_app = build_ptb_app(loop)
    await ptb_app.initialize()

    await ptb_app.bot.set_my_commands([
        BotCommand("start",          "Начало работы"),
        BotCommand("channels",       "Мои каналы"),
        BotCommand("add_channel",    "Добавить канал"),
        BotCommand("remove_channel", "Удалить канал"),
        BotCommand("filter",         "Фильтр по словам для канала"),
        BotCommand("quiet",          "Тихий режим (часы без уведомлений)"),
        BotCommand("summary",        "Саммари поста по ID"),
        BotCommand("autosummary",    "Авто-саммари каждого поста (Pro)"),
        BotCommand("digest",         "Ежедневный дайджест (Pro)"),
        BotCommand("trial",          "3 дня Pro бесплатно"),
        BotCommand("refer",          "Реферальная ссылка"),
        BotCommand("status",         "Статус подписки"),
        BotCommand("subscribe",      "Оформить подписку"),
    ])
    logger.info("Bot commands menu set")

    if _USE_WEBHOOK:
        await ptb_app.bot.set_webhook(
            url=f"{config.TELEGRAM_WEBHOOK_URL.rstrip('/')}/webhook",
            drop_pending_updates=True,
        )
        logger.info("Webhook mode: %s/webhook", config.TELEGRAM_WEBHOOK_URL)
        await ptb_app.start()
    else:
        # Delete any stale webhook so polling works
        await ptb_app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Polling mode (no HTTPS webhook URL configured)")
        await ptb_app.start()
        await ptb_app.updater.start_polling(drop_pending_updates=True)

    await start_userbot()

    await asyncio.Event().wait()


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_async_main(loop))


def main() -> None:
    init_db()
    logger.info("Database initialised")

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_run_loop, args=(loop,), daemon=True, name="async-worker")
    thread.start()

    mode = "webhook" if _USE_WEBHOOK else "polling"
    logger.info("Starting Flask on port %d [%s mode]", config.PORT, mode)
    flask_app.run(host="0.0.0.0", port=config.PORT, debug=config.DEBUG, use_reloader=False)


if __name__ == "__main__":
    main()
