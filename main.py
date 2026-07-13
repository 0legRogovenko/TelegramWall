"""Entry point.

Webhook mode  — when TELEGRAM_WEBHOOK_URL starts with https://
               Flask handles incoming updates on /webhook.
Polling mode  — fallback for local development (no HTTPS required).
               Flask still starts for the /health endpoint.
"""
import asyncio
import logging
import os
import signal
import threading

from src.bot.app import build_ptb_app, flask_app
from src.config import config
from src.database import init_db
from src.userbot.monitor import start_userbot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
if config.DEBUG:
    logging.getLogger("src.userbot.monitor").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)

_USE_WEBHOOK = config.TELEGRAM_WEBHOOK_URL.startswith("https://")


async def _async_main(loop: asyncio.AbstractEventLoop) -> None:
    from telegram import BotCommand
    ptb_app = build_ptb_app(loop)
    await ptb_app.initialize()

    # Default (free) command set. Paid users get the extended set per-chat
    # via _sync_menu_commands on /start, /trial and successful payment.
    await ptb_app.bot.set_my_commands([
        BotCommand("start",       "Начало работы / Start"),
        BotCommand("channels",    "Мои каналы / My channels"),
        BotCommand("add_channel", "Добавить канал / Add channel"),
        BotCommand("subscribe",   "Тарифы и подписка / Plans"),
        BotCommand("stats",       "Статистика / Statistics"),
        BotCommand("refer",       "Пригласить друга / Invite"),
        BotCommand("language",    "Язык / Language / Idioma"),
        BotCommand("help",        "Все команды / All commands"),
    ])
    logger.info("Bot commands menu set")

    if _USE_WEBHOOK:
        await ptb_app.bot.set_webhook(
            url=f"{config.TELEGRAM_WEBHOOK_URL.rstrip('/')}/webhook",
            drop_pending_updates=True,
            secret_token=config.WEBHOOK_SECRET,
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


def _graceful_shutdown(signum, frame) -> None:
    """Persist buffered batches before the process is killed (deploy restart)."""
    logger.info("Signal %s received — flushing buffers and exiting", signum)
    try:
        from src.userbot.monitor import flush_buffer_on_shutdown
        flush_buffer_on_shutdown()
    except Exception as exc:
        logger.warning("Shutdown flush failed: %s", exc)
    finally:
        os._exit(0)


def main() -> None:
    init_db()
    logger.info("Database initialised")

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=_run_loop, args=(loop,), daemon=True, name="async-worker")
    thread.start()

    mode = "webhook" if _USE_WEBHOOK else "polling"
    if config.DEBUG:
        logger.info("Starting Flask dev server on port %d [%s mode]", config.PORT, mode)
        flask_app.run(host="0.0.0.0", port=config.PORT, debug=True, use_reloader=False)
    else:
        from waitress import serve
        logger.info("Starting waitress on port %d [%s mode]", config.PORT, mode)
        serve(flask_app, host="0.0.0.0", port=config.PORT, threads=8)


if __name__ == "__main__":
    main()
