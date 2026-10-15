"""Persist private-chat reachability without changing user preferences."""
import logging

from telegram.error import Forbidden

from src.database import db_session
from src.models import PendingPost, User

logger = logging.getLogger(__name__)


class DeliveryForbidden(Forbidden):
    """A rejected send tied to the private-interaction generation it observed."""
    def __init__(self, error: Forbidden, generation: int):
        super().__init__(str(error))
        self.generation = generation


def get_delivery_generation(telegram_id: int) -> int | None:
    with db_session() as db:
        row = db.query(User.delivery_paused, User.delivery_generation).filter_by(
            telegram_id=telegram_id,
        ).first()
        if row and row[0]:
            return None
        return row[1] if row else 0


def pause_delivery(telegram_id: int, generation: int) -> bool:
    """Stop impossible sends after Forbidden; discard only this chat's queue.

    Accounts, subscriptions, source posts and channel settings are preserved.
    A returning user resumes the feed through a new private interaction.
    """
    with db_session() as db:
        changed = db.query(User).filter_by(
            telegram_id=telegram_id, delivery_generation=generation,
        ).update({User.delivery_paused: True})
        if not changed and db.query(User.id).filter_by(telegram_id=telegram_id).first():
            return False  # A newer incoming private interaction invalidated this send.
        db.query(PendingPost).filter_by(telegram_id=telegram_id).delete()
        db.commit()
    if changed:
        logger.info("Paused delivery to unavailable recipient %s", telegram_id)
    return True


def resume_delivery(telegram_id: int) -> None:
    """An incoming private interaction proves this chat is reachable again."""
    with db_session() as db:
        changed = db.query(User).filter_by(telegram_id=telegram_id).update({
            User.delivery_paused: False,
            User.delivery_generation: User.delivery_generation + 1,
        })
        if changed:
            db.commit()
