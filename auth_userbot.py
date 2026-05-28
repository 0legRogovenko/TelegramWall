"""Run once locally to authorise the Telethon userbot.

After this script exits, copy the printed SESSION_STRING into your
environment variables — main.py will use it instead of a session file.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from src.config import config


async def main():
    client = TelegramClient(StringSession(), config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)
    me = await client.get_me()
    print(f"\n✅ Авторизован как: {me.first_name} (@{me.username})")
    print("\n" + "=" * 60)
    print("TELEGRAM_SESSION_STRING (скопируй в Render env vars):")
    print("=" * 60)
    print(client.session.save())
    print("=" * 60 + "\n")
    await client.disconnect()


asyncio.run(main())
