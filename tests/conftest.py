"""Shared fixtures for all tests."""
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# Point to SQLite in-memory DB before any app code imports the real engine
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("TELEGRAM_WEBHOOK_URL", "http://local")
os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test")
os.environ.setdefault("TELEGRAM_PHONE", "+70000000000")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("TELEGRAM_ADMIN_IDS", "999")

from src.database import Base  # noqa: E402
from src.models import (  # noqa: E402, F401 — registers all models
    Bookmark, Channel, Post, Subscription, User, UserChannel,
)

# ── In-memory SQLite engine shared for the whole test session ─────────────────

_test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=_test_engine)
_TestSession = scoped_session(sessionmaker(bind=_test_engine))


@pytest.fixture()
def db():
    """Provide a clean DB session; rollback after each test."""
    session = _TestSession()
    yield session
    session.rollback()
    _TestSession.remove()


@pytest.fixture(autouse=True)
def patch_db_session(db, monkeypatch):
    """Replace db_session() in all modules with the test session."""
    @contextmanager
    def _test_db_session():
        yield db

    targets = [
        "src.database.db_session",
        "src.bot.payments.db_session",
        # handlers is a package — each submodule imports db_session directly
        "src.bot.handlers.general.db_session",
        "src.bot.handlers.channels.db_session",
        "src.bot.handlers.ai.db_session",
        "src.bot.handlers.admin.db_session",
        "src.bot.handlers.bookmarks.db_session",
        "src.bot.handlers.callbacks.db_session",
        "src.bot.handlers.buttons.db_session",
    ]
    for target in targets:
        monkeypatch.setattr(target, _test_db_session)


# ── Telegram mock helpers ─────────────────────────────────────────────────────

def make_tg_user(user_id: int = 1, first_name: str = "Test", username: str = "testuser"):
    u = MagicMock()
    u.id = user_id
    u.first_name = first_name
    u.username = username
    return u


def make_update(user_id: int = 1, first_name: str = "Test", args=None):
    update = MagicMock()
    update.effective_user = make_tg_user(user_id, first_name)
    update.effective_chat.id = user_id
    update.message.reply_text = AsyncMock()
    update.message.text = ""
    update.callback_query = None
    return update


def make_context(args=None):
    ctx = MagicMock()
    ctx.args = args or []
    ctx.user_data = {}
    ctx.bot.get_me = AsyncMock(return_value=MagicMock(username="testbot"))
    return ctx


# ── DB object factories ───────────────────────────────────────────────────────

def create_user(db, telegram_id: int = 1, **kwargs) -> User:
    user = User(telegram_id=telegram_id, first_name="Test", username="test", **kwargs)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_channel(db, username: str = "testchannel") -> Channel:
    ch = Channel(username=username, title=f"@{username}", telegram_id=100)
    db.add(ch)
    db.commit()
    db.refresh(ch)
    return ch


def create_subscription(db, user: User, tier: str = "basic", days: int = 30) -> Subscription:
    sub = Subscription(
        user_id=user.id,
        tier=tier,
        stars_paid=0,
        expires_at=datetime.now(timezone.utc) + timedelta(days=days),
    )
    db.add(sub)
    db.commit()
    return sub


def create_post(db, channel: Channel, text: str = "Test post", msg_id: int = 1) -> Post:
    post = Post(channel_id=channel.id, message_id=msg_id, text=text)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def subscribe_user_to_channel(db, user: User, channel: Channel, **kwargs) -> UserChannel:
    uc = UserChannel(user_id=user.id, channel_id=channel.id, **kwargs)
    db.add(uc)
    db.commit()
    return uc
