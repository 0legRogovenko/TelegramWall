"""Flask app + PTB Application setup."""
import asyncio
import json
import logging

from flask import Flask, Response, request
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

from src.bot.handlers import (
    btn_add_channel_prompt,
    btn_channels,
    btn_digest,
    btn_summary_prompt,
    callback_handler,
    cmd_add_channel,
    cmd_admin,
    cmd_aifilter,
    cmd_autosummary,
    cmd_channels,
    cmd_digest,
    cmd_filter,
    cmd_quiet,
    cmd_refer,
    cmd_remove_channel,
    cmd_save,
    cmd_saved,
    cmd_start,
    cmd_stats,
    cmd_status,
    cmd_subscribe,
    cmd_summary,
    cmd_trial,
    cmd_unsave,
    handle_text,
)
from src.bot.keyboards import BUTTON_ADD_CHANNEL, BUTTON_CHANNELS, BUTTON_DIGEST, BUTTON_SUMMARY
from src.bot.payments import handle_pre_checkout, handle_successful_payment
from src.config import config

logger = logging.getLogger(__name__)

flask_app = Flask(__name__)
flask_app.secret_key = config.SECRET_KEY

ptb_app: Application | None = None
_loop: asyncio.AbstractEventLoop | None = None


def build_ptb_app(loop: asyncio.AbstractEventLoop) -> Application:
    """Build and configure the PTB Application."""
    global ptb_app, _loop
    _loop = loop

    use_webhook = config.TELEGRAM_WEBHOOK_URL.startswith("https://")
    builder = Application.builder().token(config.TELEGRAM_BOT_TOKEN)
    if use_webhook:
        builder = builder.updater(None)  # Flask handles incoming updates
    app = builder.build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("trial", cmd_trial))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("autosummary", cmd_autosummary))
    app.add_handler(CommandHandler("digest", cmd_digest))
    app.add_handler(CommandHandler("quiet", cmd_quiet))
    app.add_handler(CommandHandler("filter", cmd_filter))
    app.add_handler(CommandHandler("channels", cmd_channels))
    app.add_handler(CommandHandler("add_channel", cmd_add_channel))
    app.add_handler(CommandHandler("remove_channel", cmd_remove_channel))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("unsave", cmd_unsave))
    app.add_handler(CommandHandler("saved", cmd_saved))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("aifilter", cmd_aifilter))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(handle_pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_successful_payment))
    # Reply keyboard buttons
    app.add_handler(MessageHandler(filters.Text([BUTTON_CHANNELS]), btn_channels))
    app.add_handler(MessageHandler(filters.Text([BUTTON_ADD_CHANNEL]), btn_add_channel_prompt))
    app.add_handler(MessageHandler(filters.Text([BUTTON_SUMMARY]), btn_summary_prompt))
    app.add_handler(MessageHandler(filters.Text([BUTTON_DIGEST]), btn_digest))
    # Plain text — catches post ID after summary button
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    ptb_app = app
    return app


@flask_app.route("/webhook", methods=["POST"])
def webhook():
    if ptb_app is None or _loop is None:
        return Response("Bot not initialised", status=503)

    data = request.get_json(force=True)
    update = Update.de_json(data, ptb_app.bot)
    future = asyncio.run_coroutine_threadsafe(
        ptb_app.process_update(update), _loop
    )
    try:
        future.result(timeout=30)
    except Exception as exc:
        logger.error("Error processing update: %s", exc)
    return Response("ok", status=200)


@flask_app.route("/health")
def health():
    return Response(json.dumps({"status": "ok"}), mimetype="application/json")
