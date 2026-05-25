"""Run this once to authorise the Telethon userbot session.
After it exits successfully, main.py will start without asking for a code."""
import asyncio
from src.config import config
from telethon import TelegramClient


async def main():
    client = TelegramClient(config.SESSION_PATH, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)
    me = await client.get_me()
    print(f"\n✅ Авторизован как: {me.first_name} (@{me.username})")
    print(f"   Сессия сохранена: {config.SESSION_PATH}.session")
    await client.disconnect()


asyncio.run(main())
