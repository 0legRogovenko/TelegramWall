"""Channel blocklist: sources the bot refuses to monitor or redistribute.

Redistribution of some sources carries legal risk (in Russia, reposting
content of certain designated persons and organizations can be an offence).
Which sources those are is the operator's editorial/legal judgement — this
module only provides the mechanism: a static set from config plus a DB table
that admins manage at runtime with /block and /unblock.

Blocking works at two gates:
* adding — /add_channel refuses a blocked username;
* polling — blocked channels are excluded from the poll cycle, so existing
  subscriptions stop receiving posts immediately, without deleting anything.
"""
from src.config import config
from src.models import BlockedChannel


def _norm(username: str | None) -> str:
    return (username or "").strip().lstrip("@").lower()


def is_blocked(db, username: str | None) -> bool:
    name = _norm(username)
    if not name:
        return False
    if name in config.BLOCKED_CHANNELS:
        return True
    return db.query(BlockedChannel).filter_by(username=name).first() is not None


def blocked_usernames(db) -> set[str]:
    """Full effective blocklist (config + DB), lowercase, without @."""
    dynamic = {row[0] for row in db.query(BlockedChannel.username).all()}
    return set(config.BLOCKED_CHANNELS) | dynamic


def block(db, username: str, reason: str | None = None) -> bool:
    """Add to the runtime blocklist. False if it was already blocked."""
    name = _norm(username)
    if not name or is_blocked(db, name):
        return False
    db.add(BlockedChannel(username=name, reason=reason))
    db.commit()
    return True


def unblock(db, username: str) -> bool:
    """Remove from the runtime blocklist. False if it wasn't there.

    Entries from config.BLOCKED_CHANNELS cannot be removed here — they are
    pinned by the environment and survive on purpose.
    """
    name = _norm(username)
    row = db.query(BlockedChannel).filter_by(username=name).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
