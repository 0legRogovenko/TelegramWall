"""Selection and delivery of paid-feature offers to active Free users."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists

from src.bot.i18n import lang_of, t
from src.bot.payments import price_label
from src.config import config
from src.models import Subscription, User, UserChannel

CAMPAIGN_KEYS = ("upsell_summary", "upsell_digest", "upsell_capacity")


@dataclass(frozen=True)
class UpsellCandidate:
    user_id: int
    telegram_id: int
    lang: str
    trial_used: bool
    variant: int


def _variant(user_id: int, now: datetime) -> int:
    period = now.date().toordinal() // config.UPSELL_INTERVAL_DAYS
    return (user_id + period) % len(CAMPAIGN_KEYS)


def due_upsells(
    db,
    now: datetime | None = None,
    limit: int | None = None,
    user_id: int | None = None,
) -> list[UpsellCandidate]:
    """Return a bounded batch of active Free users whose offer is due."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=config.UPSELL_INTERVAL_DAYS)
    active_subscription = exists().where(
        Subscription.user_id == User.id,
        Subscription.expires_at > now,
    )
    query = db.query(User).filter(
        User.language.isnot(None),
        User.created_at <= cutoff,
        (User.upsell_last_sent_at.is_(None)) | (User.upsell_last_sent_at <= cutoff),
        User.user_channels.any(UserChannel.is_active.is_(True)),
        ~active_subscription,
    )
    if config.ADMIN_IDS:
        query = query.filter(User.telegram_id.notin_(config.ADMIN_IDS))
    if user_id is not None:
        query = query.filter(User.id == user_id)
    rows = query.order_by(User.id).limit(limit or config.UPSELL_BATCH_SIZE).all()
    return [
        UpsellCandidate(
            user_id=user.id,
            telegram_id=user.telegram_id,
            lang=lang_of(user),
            trial_used=user.trial_used,
            variant=_variant(user.id, now),
        )
        for user in rows
    ]


def render_upsell(candidate: UpsellCandidate) -> str:
    """Render one deterministic campaign in the candidate's language."""
    return t(
        CAMPAIGN_KEYS[candidate.variant],
        candidate.lang,
        basic_price=price_label("basic"),
        pro_price=price_label("pro"),
        basic_limit=config.CHANNEL_LIMIT_BASIC,
        trial_days=config.TRIAL_DAYS,
    )
