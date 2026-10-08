"""Allow independent users to interact while preserving each user's order."""
import asyncio

from telegram.ext import BaseUpdateProcessor


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
                await coroutine
        finally:
            remaining = self._users[user.id][1] - 1
            if remaining:
                self._users[user.id] = lock, remaining
            else:
                self._users.pop(user.id, None)
