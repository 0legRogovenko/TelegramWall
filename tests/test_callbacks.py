"""Focused coverage for security-sensitive callback branches."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bot.handlers.callbacks import callback_handler

from .conftest import (
    create_channel,
    create_post,
    create_subscription,
    create_user,
    make_context,
    make_update,
    subscribe_user_to_channel,
)


def _callback_update(user_id: int, data: str):
    update = make_update(user_id=user_id)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.message.reply_text = AsyncMock()
    query.message.edit_text = AsyncMock()
    update.callback_query = query
    return update


@pytest.mark.asyncio
async def test_summary_callback_cannot_access_another_users_channel(db):
    user = create_user(db, telegram_id=9401, language="ru")
    create_subscription(db, user, tier="basic")
    own = create_channel(db, "callback_own")
    other = create_channel(db, "callback_other")
    subscribe_user_to_channel(db, user, own)
    post = create_post(db, other, text="x" * 100, msg_id=9401)
    update = _callback_update(user.telegram_id, f"sum:{post.id}")

    await callback_handler(update, make_context())

    assert update.callback_query.answer.await_count == 2
    assert update.callback_query.message.reply_text.await_count == 0


@pytest.mark.asyncio
async def test_summary_callback_returns_cached_summary_for_own_channel(db):
    user = create_user(db, telegram_id=9402, language="ru")
    create_subscription(db, user, tier="basic")
    channel = create_channel(db, "callback_cached")
    subscribe_user_to_channel(db, user, channel)
    post = create_post(db, channel, text="x" * 100, msg_id=9402)
    post.summary = "safe summary"
    db.commit()
    update = _callback_update(user.telegram_id, f"sum:{post.id}")

    await callback_handler(update, make_context())

    update.callback_query.message.reply_text.assert_awaited_once()
    assert "safe summary" in update.callback_query.message.reply_text.await_args.args[0]
