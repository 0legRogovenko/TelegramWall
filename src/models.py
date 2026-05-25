from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


_PAID_TIERS = ("basic", "pro", "annual_basic", "annual_pro")
_PRO_TIERS  = ("pro", "annual_pro")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
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

    @property
    def is_quiet_now(self) -> bool:
        if self.quiet_start is None or self.quiet_end is None:
            return False
        hour = datetime.now(timezone.utc).hour
        qs, qe = self.quiet_start, self.quiet_end
        if qs <= qe:
            return qs <= hour < qe
        return hour >= qs or hour < qe


class Channel(Base):
    """Global registry of channels. Deduped by username."""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(256))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user_channels: Mapped[list["UserChannel"]] = relationship(back_populates="channel")
    posts: Mapped[list["Post"]] = relationship(back_populates="channel")


class UserChannel(Base):
    """Which user follows which channel."""
    __tablename__ = "user_channels"
    __table_args__ = (UniqueConstraint("user_id", "channel_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    keywords: Mapped[str | None] = mapped_column(Text)   # comma-separated filter words
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="user_channels")
    channel: Mapped["Channel"] = relationship(back_populates="user_channels")

    @property
    def keyword_list(self) -> list[str]:
        if not self.keywords:
            return []
        return [k.strip().lower() for k in self.keywords.split(",") if k.strip()]


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("channel_id", "message_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    channel: Mapped["Channel"] = relationship(back_populates="posts")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), default="basic", server_default="basic")
    stars_paid: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_charge_id: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="subscriptions")
