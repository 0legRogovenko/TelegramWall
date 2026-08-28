"""Subscriber eligibility and soft channel-limit enforcement."""
import logging

from sqlalchemy.orm import joinedload

from src.bot.i18n import lang_of
from src.models import User, UserChannel

logger = logging.getLogger(__name__)

__all__ = [
    "get_eligible_subscriber_details",
    "get_eligible_subscribers",
    "user_lang",
]


def _batch_allowed_channel_ids(
    db, limited_users: list[tuple[int, int]],
) -> dict[int, set[int]]:
    """Return {user_id: set of allowed channel_ids} for limited tiers.

    Queries all UserChannels so deactivating old channels cannot shift newer
    ones into the allowed window. Stable tie-breaking via (created_at, id)
    prevents non-determinism at identical timestamps. A single batch query
    replaces N individual queries.
    """
    if not limited_users:
        return {}
    user_ids = [uid for uid, _ in limited_users]
    limits = {uid: lim for uid, lim in limited_users}

    rows = (
        db.query(UserChannel.user_id, UserChannel.channel_id)
        .filter(UserChannel.user_id.in_(user_ids))
        .order_by(UserChannel.user_id, UserChannel.created_at, UserChannel.id)
        .all()
    )

    result: dict[int, set[int]] = {}
    counts: dict[int, int] = {}
    for user_id, ch_id in rows:
        seen = counts.get(user_id, 0)
        if seen < limits[user_id]:
            result.setdefault(user_id, set()).add(ch_id)
            counts[user_id] = seen + 1
    return result


def get_eligible_subscribers(
    db, channel_id: int, text: str,
) -> list[tuple[int, str | None]]:
    """Return subscribers who pass all delivery checks.

    The result contains ``(telegram_id, ai_filter)`` tuples. If a user's tier
    allows fewer channels than they currently have, only the earliest-added
    channels are delivered. UserChannel records are not modified.
    """
    return [
        (telegram_id, ai_filter)
        for telegram_id, ai_filter, _ in get_eligible_subscriber_details(
            db, channel_id, text
        )
    ]


def get_eligible_subscriber_details(
    db, channel_id: int, text: str,
) -> list[tuple[int, str | None, bool]]:
    """Return eligible subscribers and their digest-only preference."""
    ucs = (
        db.query(UserChannel)
        .filter_by(channel_id=channel_id, is_active=True)
        .options(joinedload(UserChannel.user).joinedload(User.subscriptions))
        .all()
    )

    limited_users = [
        (uc.user.id, uc.user.channel_limit)
        for uc in ucs
        if uc.user.channel_limit is not None
    ]
    allowed_map = _batch_allowed_channel_ids(db, limited_users)

    result = []
    for uc in ucs:
        user = uc.user

        if user.channel_limit is not None:
            if channel_id not in allowed_map.get(user.id, set()):
                logger.debug(
                    "Skipping user %s (channel %s exceeds tier limit %d)",
                    user.telegram_id, channel_id, user.channel_limit,
                )
                continue

        digest_only = user.digest_enabled and user.can_auto_summary
        result.append((user.telegram_id, uc.ai_filter, digest_only))

    logger.debug(
        "Eligible subscribers for channel %s: %s",
        channel_id,
        [row[0] for row in result],
    )
    return result


def user_lang(db, telegram_id: int) -> str:
    user = db.query(User).filter_by(telegram_id=telegram_id).first()
    return lang_of(user) if user else "ru"
