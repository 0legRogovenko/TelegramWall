from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, scoped_session

from src.config import config


class Base(DeclarativeBase):
    pass


engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)
SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Session = scoped_session(SessionFactory)


def _run_migration(conn, sql: str) -> None:
    try:
        conn.execute(text(sql))
        conn.commit()
    except Exception:
        conn.rollback()  # reset transaction so next ALTER TABLE can run


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
            "ALTER TABLE subscriptions ADD COLUMN payment_currency VARCHAR(3) NOT NULL DEFAULT 'XTR'",
        )
        # users — new columns
        _run_migration(conn, "ALTER TABLE users ADD COLUMN digest_enabled BOOLEAN DEFAULT FALSE")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT FALSE")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN quiet_start INTEGER")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN quiet_end INTEGER")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN referral_code VARCHAR(32)")
        _run_migration(conn, "ALTER TABLE users ADD COLUMN referred_by INTEGER")
        # user_channels
        _run_migration(conn, "ALTER TABLE user_channels ADD COLUMN keywords TEXT")
        _run_migration(conn, "ALTER TABLE user_channels ADD COLUMN ai_filter TEXT")
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


def get_session():
    return Session()


@contextmanager
def db_session():
    """Context manager for a DB session — closes automatically on exit."""
    db = Session()
    try:
        yield db
    finally:
        db.close()
