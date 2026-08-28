"""Proactive subscription/trial expiry notifications."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from src.bot.i18n import lang_of, t, tier_label
from src.bot.keyboards import subscribe_keyboard
from src.config import config
from src.database import db_session
from src.models import Subscription, User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpiryNotification:
    subscription_id: int
    telegram_id: int
    lang: str
    kind: str  # warning | expired
    is_trial: bool
    tier: str
    expires_at: datetime


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_notifications(db, now: datetime | None = None) -> list[ExpiryNotification]:
    """Return notices due for each user's latest subscription only."""
    now = now or datetime.now(timezone.utc)
    warning_after = now + timedelta(hours=config.SUBSCRIPTION_WARNING_HOURS)
    recently_expired = now - timedelta(hours=24)
    latest = (
        db.query(
            Subscription.user_id.label("user_id"),
            func.max(Subscription.expires_at).label("expires_at"),
        )
        .group_by(Subscription.user_id)
        .subquery()
    )
    rows = (
        db.query(Subscription, User)
        .join(
            latest,
            (latest.c.user_id == Subscription.user_id)
            & (latest.c.expires_at == Subscription.expires_at),
        )
        .join(User, User.id == Subscription.user_id)
        .filter(
            Subscription.expires_at >= recently_expired,
            Subscription.expires_at <= warning_after,
        )
        .all()
    )

    result = []
    for sub, user in rows:
        if user.telegram_id in config.ADMIN_IDS:
            continue
        expires = _aware(sub.expires_at)
        if expires > now and sub.expiry_warning_sent_at is None:
            kind = "warning"
        elif expires <= now and sub.expired_notice_sent_at is None:
            kind = "expired"
        else:
            continue
        result.append(ExpiryNotification(
            subscription_id=sub.id,
            telegram_id=user.telegram_id,
            lang=lang_of(user),
            kind=kind,
            is_trial=sub.stars_paid == 0 and sub.tier == "pro",
            tier=sub.tier,
            expires_at=expires,
        ))
    return result


def _mark_sent(subscription_id: int, kind: str) -> None:
    with db_session() as db:
        sub = db.query(Subscription).filter_by(id=subscription_id).first()
        if sub is None:
            return
        now = datetime.now(timezone.utc)
        if kind == "warning":
            sub.expiry_warning_sent_at = now
        else:
            sub.expired_notice_sent_at = now
        db.commit()


def _load_due() -> list[ExpiryNotification]:
    with db_session() as db:
        return due_notifications(db)


async def send_due_notifications() -> int:
    """Send due notices and mark only successfully delivered messages."""
    from src.bot.app import ptb_app

    if ptb_app is None:
        return 0
    notices = await asyncio.to_thread(_load_due)

    sent = 0
    for notice in notices:
        key = (
            f"{'trial' if notice.is_trial else 'subscription'}_"
            f"{notice.kind}"
        )
        try:
            await ptb_app.bot.send_message(
                chat_id=notice.telegram_id,
                text=t(
                    key,
                    notice.lang,
                    tier=tier_label(notice.tier, notice.lang),
                    date=notice.expires_at.strftime("%d.%m.%Y %H:%M UTC"),
                ),
                parse_mode="HTML",
                reply_markup=subscribe_keyboard(notice.lang),
            )
            await asyncio.to_thread(_mark_sent, notice.subscription_id, notice.kind)
            if notice.kind == "expired":
                from src.services.upsell import mark_contextual_upsell

                await mark_contextual_upsell(notice.telegram_id)
            sent += 1
        except Exception as exc:
            logger.warning(
                "Subscription notice %s to %s failed: %s",
                notice.kind, notice.telegram_id, exc,
            )
    return sent
