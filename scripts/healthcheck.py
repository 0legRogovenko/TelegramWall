"""External watchdog — runs in GitHub Actions on a cron, NOT inside the bot.

The bot stamps bot_health.last_seen_at every 5 minutes. This script reads that
stamp from the same database and alerts admins over Telegram when it goes
stale — which is exactly the failure an in-process report can never tell you
about (a dead bot sends nothing, and silence looks like "no news").

Alert state lives in bot_health.alert_sent_at so a prolonged outage doesn't
spam one message per cron tick, and recovery is announced once.
"""
import html
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text


def _int_env(name: str, default: int) -> int:
    """Read an int env var. An unset GitHub Actions variable arrives as "" —
    fall back rather than dying on int("")."""
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


STALE_MINUTES = _int_env("HEARTBEAT_STALE_MINUTES", 20)
REALERT_HOURS = _int_env("REALERT_HOURS", 6)


def _as_dt(value) -> datetime | None:
    """Coerce a timestamp column to an aware datetime.

    Postgres (prod) hands back datetime objects; SQLite hands back strings —
    accept both so the script is driver-agnostic.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _send(token: str, chat_id: str, message: str) -> None:
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _broadcast(token: str, admin_ids: list[str], message: str) -> bool:
    """Send to every admin. True if at least one delivery succeeded.

    The return value gates every write to alert_sent_at: recording an alert
    that was never delivered would mute the next 6 hours of ticks over a
    momentary Telegram blip — silence exactly when the bot is down.
    """
    delivered = False
    for admin in admin_ids:
        try:
            _send(token, admin, message)
            delivered = True
        except Exception as exc:
            print(f"Send to {admin} failed: {exc}", file=sys.stderr)
    return delivered


def _stamp(engine, sql: str, params: dict) -> None:
    """Persist alert state. A failure here must not mask a delivered alert."""
    try:
        with engine.connect() as conn:
            conn.execute(text(sql), params)
            conn.commit()
    except Exception as exc:
        print(f"Could not persist alert state: {exc}", file=sys.stderr)


def _read_health(db_url: str):
    """Return (row, error). error is a human string when the DB is unreachable.

    A missing bot_health table is not an error: on the very first deploy this
    cron can fire before the bot has run init_db(), and that resolves itself.
    It's asked via has_table rather than by catching the failure, because the
    exception class differs per dialect (Postgres raises ProgrammingError,
    SQLite OperationalError) — which would make "table missing" indistinguishable
    from "database down", the one case that must alert.
    """
    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            if not inspect(conn).has_table("bot_health"):
                return None, None  # bot hasn't deployed this code yet
            row = conn.execute(
                text("SELECT last_seen_at, alert_sent_at FROM bot_health WHERE id = 1")
            ).first()
            return row, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    db_url = os.getenv("DATABASE_URL", "")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_ids = [x.strip() for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip()]
    if not admin_ids or not token:
        print("TELEGRAM_ADMIN_IDS/TELEGRAM_BOT_TOKEN missing — nothing to alert")
        return 0

    now = datetime.now(timezone.utc)
    row, db_error = _read_health(db_url)

    # The watchdog reads the heartbeat from the same DB the bot writes it to, so
    # a DB outage is itself a strong signal the bot is down. Telegram doesn't
    # depend on the DB — alert over it rather than dying with a traceback that
    # nobody sees. Dedup state is unreachable here, so this may repeat per tick;
    # a hard-down database is worth repeating.
    if db_error:
        _broadcast(token, admin_ids, (
            "🟠 <b>Сторож не смог прочитать состояние бота</b>\n\n"
            f"База недоступна: <code>{html.escape(db_error[:300])}</code>\n\n"
            "Скорее всего бот тоже лежит — проверь базу и Actions → <b>Bot</b>."
        ))
        print(f"DB unreachable: {db_error}", file=sys.stderr)
        return 1

    if row is None:
        print("No bot_health row yet — bot has never started; skipping")
        return 0

    last_seen = _as_dt(row[0])
    alert_sent_at = _as_dt(row[1])

    if last_seen is None:
        print("last_seen_at is NULL — bot has never stamped; skipping")
        return 0

    stale_for = now - last_seen
    is_down = stale_for > timedelta(minutes=STALE_MINUTES)
    mins = int(stale_for.total_seconds() // 60)

    engine = create_engine(db_url, pool_pre_ping=True)

    if is_down:
        # Don't re-alert on every tick — once, then every REALERT_HOURS
        if alert_sent_at and now - alert_sent_at < timedelta(hours=REALERT_HOURS):
            print(f"Bot down {mins} min — already alerted, staying quiet")
            return 0
        delivered = _broadcast(token, admin_ids, (
            "🔴 <b>Бот не отвечает</b>\n\n"
            f"Последний сигнал: <b>{mins} мин назад</b>\n"
            f"Порог: {STALE_MINUTES} мин\n\n"
            "Проверь вкладку Actions → workflow <b>Bot</b>."
        ))
        if not delivered:
            # Leave alert_sent_at untouched so the next tick retries instead of
            # going quiet for REALERT_HOURS on an alert nobody received.
            print("Bot down but no alert could be delivered — will retry", file=sys.stderr)
            return 1
        _stamp(engine, "UPDATE bot_health SET alert_sent_at = :now WHERE id = 1", {"now": now})
        print(f"ALERT SENT — bot down for {mins} min")
        return 1

    # Healthy. If we had alerted, announce recovery once and clear the flag.
    if alert_sent_at:
        if not _broadcast(token, admin_ids, "✅ <b>Бот снова в строю</b>"):
            print("Recovery notice undelivered — keeping flag to retry", file=sys.stderr)
            return 0
        _stamp(engine, "UPDATE bot_health SET alert_sent_at = NULL WHERE id = 1", {})
        print("Bot recovered — notice sent")
        return 0

    print(f"OK — last seen {mins} min ago")
    return 0


if __name__ == "__main__":
    sys.exit(main())
