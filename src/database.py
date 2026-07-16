import logging
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.config import config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
# One session per call — the whole app shares a single asyncio thread, so a
# thread-scoped session would be reused by concurrent tasks and corrupt state.
SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _run_migration(conn, sql: str) -> None:
    try:
        conn.execute(text(sql))
        conn.commit()
    except Exception as exc:
        conn.rollback()  # reset transaction so next ALTER TABLE can run
        # Usually "column already exists" — but log it so real failures are visible
        logger.debug("Migration skipped: %s (%s)", sql.strip().split("\n")[0], exc)


def init_db() -> None:
    from src import models  # noqa: F401 — registers models
    Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        # subscriptions
        _run_migration(
            conn,
            "ALTER TABLE subscriptions ADD COLUMN tier VARCHAR(16) DEFAULT 'basic'",
        )
        _run_migration(
            conn,
            "ALTER TABLE subscriptions ADD COLUMN payment_currency VARCHAR(3) "
            "NOT NULL DEFAULT 'XTR'",
        )
        # users — new columns
        _run_migration(conn, "ALTER TABLE users ADD COLUMN digest_enabled BOOLEAN DEFAULT FALSE")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT FALSE")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN quiet_start INTEGER")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN quiet_end INTEGER")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN referral_code VARCHAR(32)")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN referred_by INTEGER")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN language VARCHAR(5)")
        # channels — polling cursor
        _run_migration(
            conn, "ALTER TABLE channels ADD COLUMN last_message_id BIGINT DEFAULT 0"
        )
        _run_migration(conn, """
            UPDATE channels SET last_message_id = COALESCE(
                (SELECT MAX(message_id) FROM posts WHERE posts.channel_id = channels.id), 0
            ) WHERE COALESCE(last_message_id, 0) = 0
        """)
        # user_channels
        _run_migration(conn, "ALTER TABLE user_channels ADD COLUMN keywords TEXT")
        _run_migration(conn, "ALTER TABLE user_channels ADD COLUMN ai_filter TEXT")
        # pending_posts — queued multi-post batches
        _run_migration(conn, """
            CREATE TABLE IF NOT EXISTS pending_posts (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
                post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(telegram_id, post_id)
            )
        """)
        # bookmarks
        _run_migration(conn, """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, post_id)
            )
        """)
        # bot_events — operational log behind the daily admin report
        _run_migration(conn, """
            CREATE TABLE IF NOT EXISTS bot_events (
                id SERIAL PRIMARY KEY,
                type VARCHAR(32) NOT NULL,
                detail TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # bot_health — singleton liveness row read by the external watchdog.
        # The row itself is created by the first metrics.heartbeat() call.
        _run_migration(conn, """
            CREATE TABLE IF NOT EXISTS bot_health (
                id INTEGER PRIMARY KEY,
                started_at TIMESTAMPTZ,
                last_seen_at TIMESTAMPTZ,
                last_report_on VARCHAR(10),
                alert_sent_at TIMESTAMPTZ
            )
        """)
        # indexes for hot query paths
        _run_migration(
            conn, "CREATE INDEX IF NOT EXISTS ix_posts_created_at ON posts (created_at)"
        )
        _run_migration(
            conn,
            "CREATE INDEX IF NOT EXISTS ix_user_channels_channel_id "
            "ON user_channels (channel_id)",
        )
        _run_migration(
            conn,
            "CREATE INDEX IF NOT EXISTS ix_subscriptions_user_id ON subscriptions (user_id)",
        )
        _run_migration(
            conn,
            "CREATE INDEX IF NOT EXISTS ix_bot_events_created_at ON bot_events (created_at)",
        )
        _run_migration(
            conn, "CREATE INDEX IF NOT EXISTS ix_bot_events_type ON bot_events (type)"
        )


def get_session():
    return SessionFactory()


@contextmanager
def db_session():
    """Context manager for a DB session — rolls back on error, always closes."""
    db = SessionFactory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
