from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


_PAID_TIERS = ("basic", "pro", "annual_basic", "annual_pro")
_PRO_TIERS = ("pro", "annual_pro")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language: Mapped[str | None] = mapped_column(String(5))  # None until chosen at /start
    auto_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_used: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_start: Mapped[int | None] = mapped_column(Integer)   # UTC hour 0-23
    quiet_end: Mapped[int | None] = mapped_column(Integer)     # UTC hour 0-23
    referral_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    referred_by: Mapped[int | None] = mapped_column(Integer)   # user.id of referrer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user", order_by="Subscription.expires_at.desc()"
    )
    user_channels: Mapped[list["UserChannel"]] = relationship(back_populates="user")
    bookmarks: Mapped[list["Bookmark"]] = relationship(
        back_populates="user", order_by="Bookmark.created_at.desc()"
    )

    @property
    def active_subscription(self) -> "Subscription | None":
        now = datetime.now(timezone.utc)
        return next(
            (s for s in self.subscriptions if s.expires_at.replace(tzinfo=timezone.utc) > now),
            None,
        )

    @property
    def has_subscription(self) -> bool:
        return self.active_subscription is not None

    @property
    def subscription_tier(self) -> str:
        from src.config import config
        if self.telegram_id in config.ADMIN_IDS:
            return "pro"  # admins get Pro forever, free of charge
        sub = self.active_subscription
        return sub.tier if sub else "free"

    @property
    def channel_limit(self) -> int | None:
        from src.config import config
        tier = self.subscription_tier
        if tier in _PRO_TIERS:
            return None
        if tier in ("basic", "annual_basic"):
            return config.CHANNEL_LIMIT_BASIC
        return config.CHANNEL_LIMIT_FREE

    @property
    def can_summary(self) -> bool:
        return self.subscription_tier in _PAID_TIERS

    @property
    def can_auto_summary(self) -> bool:
        return self.subscription_tier in _PRO_TIERS


class Channel(Base):
    """Global registry of channels. Deduped by username."""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    # Polling cursor: highest message_id ever seen. Lives on the channel so
    # that daily post cleanup can never make the bot re-fetch old posts.
    last_message_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user_channels: Mapped[list["UserChannel"]] = relationship(back_populates="channel")
    posts: Mapped[list["Post"]] = relationship(back_populates="channel")


class UserChannel(Base):
    """Which user follows which channel."""
    __tablename__ = "user_channels"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[str | None] = mapped_column(Text)
    ai_filter: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="user_channels")
    channel: Mapped["Channel"] = relationship(back_populates="user_channels")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("channel_id", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(32))
    # Telegram album id: every item of an album shares it. Persisted (not kept
    # in memory) so the ~2h restart cannot split one album into two posts.
    grouped_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    channel: Mapped["Channel"] = relationship(back_populates="posts")


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("user_id", "post_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="bookmarks")
    post: Mapped["Post"] = relationship()


class PendingPost(Base):
    """Multi-post batches queued for scheduled delivery (survives restarts)."""
    __tablename__ = "pending_posts"
    __table_args__ = (UniqueConstraint("telegram_id", "post_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BotEvent(Base):
    """Append-only operational log — the source of the daily admin report.

    One row per notable event (post saved, delivery, AI call, error).
    Purged on the same daily schedule as posts.
    """
    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # For ai_* events: "<input_tokens>,<output_tokens>". For errors: the message.
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class BotHealth(Base):
    """Singleton (id=1) liveness row — written by the bot, read by the watchdog.

    The external healthcheck workflow can't see inside the process, so the bot
    stamps last_seen_at; a stale stamp is what proves the bot is down.
    """
    __tablename__ = "bot_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Date (UTC) of the last daily report — lets a restart send a missed report late
    last_report_on: Mapped[str | None] = mapped_column(String(10))
    # Set by the watchdog when it alerts; cleared when the bot recovers
    alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    tier: Mapped[str] = mapped_column(String(16), default="basic", server_default="basic")
    stars_paid: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="XTR")
    payment_charge_id: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="subscriptions")
