"""Allow independent users to interact while preserving each user's order."""
import asyncio
import logging

from telegram.ext import BaseUpdateProcessor

from src.services.recipients import resume_delivery

logger = logging.getLogger(__name__)


class UserUpdateProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates: int = 8):
        super().__init__(max_concurrent_updates)
        self._users: dict[int, tuple[asyncio.Lock, int]] = {}

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        self._users.clear()

    async def do_process_update(self, update, coroutine) -> None:
        user = getattr(update, "effective_user", None)
        if user is None:
            await coroutine
            return
        lock, waiting = self._users.get(user.id, (asyncio.Lock(), 0))
        self._users[user.id] = lock, waiting + 1
        try:
            async with lock:
                chat = getattr(update, "effective_chat", None)
                message = getattr(update, "effective_message", None)
                if (chat is not None and message is not None
                        and chat.type == "private" and chat.id == user.id):
                    try:
                        await asyncio.to_thread(resume_delivery, user.id)
                    except Exception as exc:
                        # Reachability bookkeeping must not swallow the command.
                        logger.warning("Could not resume private delivery: %s", exc)
                await coroutine
        finally:
            remaining = self._users[user.id][1] - 1
            if remaining:
                self._users[user.id] = lock, remaining
            else:
                self._users.pop(user.id, None)
