"""Authorization and budget guards for user-triggered AI summaries."""
from datetime import datetime, timezone

from src.config import config
from src.models import Post, User, UserChannel


def authorized_post(db, user: User, post_id: int) -> Post | None:
    """Return a post only when it belongs to one of the user's active channels."""
    return (
        db.query(Post)
        .join(
            UserChannel,
            (UserChannel.channel_id == Post.channel_id)
            & (UserChannel.user_id == user.id)
            & UserChannel.is_active.is_(True),
        )
        .filter(Post.id == post_id)
        .first()
    )


def claim_summary_request(db, user: User) -> bool:
    """Consume one daily summary slot before calling the paid AI API.

    Admins are exempt. Failed AI calls still consume a slot: the quota protects
    the operator's budget, including repeated requests during provider errors.
    """
    if user.telegram_id in config.ADMIN_IDS:
        return True

    today = datetime.now(timezone.utc).date().isoformat()
    if user.ai_usage_date != today:
        user.ai_usage_date = today
        user.ai_usage_count = 0
    if user.ai_usage_count >= config.AI_DAILY_SUMMARY_LIMIT:
        return False
    user.ai_usage_count += 1
    db.commit()
    return True
