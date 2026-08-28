"""Selection and delivery of paid-feature offers to active Free users."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists

from src.bot.i18n import lang_of, t
from src.bot.keyboards import upsell_keyboard
from src.bot.payments import price_label
from src.config import config
from src.database import db_session
from src.models import Subscription, User, UserChannel
from src.services import metrics

CAMPAIGN_KEYS = ("upsell_summary", "upsell_digest", "upsell_capacity")
logger = logging.getLogger(__name__)


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


def _load_due() -> list[UpsellCandidate]:
    with db_session() as db:
        return due_upsells(db)


def _reload_due_candidate(user_id: int) -> UpsellCandidate | None:
    with db_session() as db:
        rows = due_upsells(db, limit=1, user_id=user_id)
        return rows[0] if rows else None


def mark_upsell_sent(telegram_id: int, sent_at: datetime | None = None) -> bool:
    """Persist the shared cooldown only after an offer reached Telegram."""
    with db_session() as db:
        user = db.query(User).filter_by(telegram_id=telegram_id).first()
        if user is None:
            return False
        user.upsell_last_sent_at = sent_at or datetime.now(timezone.utc)
        db.commit()
        return True


async def mark_contextual_upsell(telegram_id: int) -> bool:
    """Advance the same cooldown after a contextual subscription offer."""
    marked = await asyncio.to_thread(mark_upsell_sent, telegram_id)
    if marked:
        metrics.record(metrics.UPSELL_SENT, "contextual")
    return marked


async def send_due_upsells() -> int:
    """Send one bounded pass, revalidating every candidate before delivery."""
    from src.bot.app import ptb_app

    if ptb_app is None:
        return 0
    candidates = await asyncio.to_thread(_load_due)
    sent = 0
    for selected in candidates:
        candidate = await asyncio.to_thread(_reload_due_candidate, selected.user_id)
        if candidate is None:
            continue
        try:
            await ptb_app.bot.send_message(
                chat_id=candidate.telegram_id,
                text=render_upsell(candidate),
                parse_mode="HTML",
                reply_markup=upsell_keyboard(candidate.trial_used, candidate.lang),
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Upsell to %s failed: %s", candidate.telegram_id, exc)
            continue
        await asyncio.to_thread(mark_upsell_sent, candidate.telegram_id)
        metrics.record(metrics.UPSELL_SENT, "scheduled")
        sent += 1
    return sent
