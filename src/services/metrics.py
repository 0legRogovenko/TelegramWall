"""Operational metrics: event log + liveness heartbeat.

Monitoring must never degrade delivery, so this module is built around two
rules:

* `record()` only appends to an in-memory buffer. It never touches the DB and
  never raises, so it is safe to call from the asyncio loop thread in the
  delivery hot path — an INSERT per delivered post would have put a Supabase
  round trip in front of every user's message.
* Everything that does touch the DB (`flush`, `heartbeat`) is blocking, must be
  called via `asyncio.to_thread`, and swallows its own failures.

Buffered events are lost if the process is killed without a flush; they feed a
24-hour report, so that trade is deliberate.
"""
import logging
import threading
from datetime import datetime, timezone

from src.database import SessionFactory
from src.models import BotEvent, BotHealth

logger = logging.getLogger(__name__)

# Event types (kept short — they are grouped/counted in the daily report)
POST_SAVED = "post_saved"
DELIVERED_POST = "delivered_post"
DELIVERED_BURST = "delivered_burst"
DELIVERED_SUMMARY = "delivered_summary"
AI_SUMMARY = "ai_summary"
AI_DIGEST = "ai_digest"
AI_FILTER = "ai_filter"
ERROR_AI = "error_ai"
ERROR_DELIVERY = "error_delivery"
STARTED = "started"

# Haiku 4.5 pricing, USD per 1M tokens — used to cost the ai_* events.
# Keep in sync with config.CLAUDE_MODEL; ai_cost_usd is only right for Haiku.
PRICE_IN_PER_MTOK = 1.0
PRICE_OUT_PER_MTOK = 5.0

# Bound the buffer so a long DB outage can't grow it without limit. Dropping
# the oldest metrics is always preferable to exhausting the bot's memory.
MAX_BUFFERED = 5000

_buffer: list[tuple[str, str | None, datetime]] = []
_lock = threading.Lock()


def record(event_type: str, detail: str | None = None) -> None:
    """Buffer one operational event. Never raises, never blocks, no I/O."""
    try:
        with _lock:
            _buffer.append((
                event_type,
                (detail or "")[:500] or None,
                datetime.now(timezone.utc),
            ))
            if len(_buffer) > MAX_BUFFERED:
                del _buffer[:len(_buffer) - MAX_BUFFERED]
    except Exception:  # pragma: no cover — appending cannot realistically fail
        pass


def record_ai(event_type: str, usage) -> None:
    """Buffer an AI call with its token usage (detail = 'input,output')."""
    try:
        detail = f"{usage.input_tokens},{usage.output_tokens}"
    except Exception:
        detail = None
    record(event_type, detail)


def flush() -> int:
    """Write buffered events to the DB. Blocking — call via asyncio.to_thread.

    Returns the number of rows written. Never raises: on failure the batch is
    put back so the next tick retries instead of losing the events.
    """
    with _lock:
        if not _buffer:
            return 0
        batch = list(_buffer)
        _buffer.clear()

    db = None
    try:
        db = SessionFactory()
        db.bulk_save_objects([
            BotEvent(type=etype, detail=detail, created_at=ts)
            for etype, detail, ts in batch
        ])
        db.commit()
        return len(batch)
    except Exception as exc:
        logger.debug("metrics.flush failed, re-queueing %d event(s): %s", len(batch), exc)
        with _lock:
            _buffer[:0] = batch
            if len(_buffer) > MAX_BUFFERED:
                del _buffer[:len(_buffer) - MAX_BUFFERED]
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def heartbeat(started: bool = False) -> None:
    """Stamp liveness. The external watchdog reads last_seen_at. Never raises.

    Blocking — call via asyncio.to_thread. Creates the singleton row on first
    use, so no migration needs to seed it.
    """
    db = None
    try:
        db = SessionFactory()
        now = datetime.now(timezone.utc)
        row = db.query(BotHealth).filter_by(id=1).first()
        if row is None:
            row = BotHealth(id=1)
            db.add(row)
        row.last_seen_at = now
        if started:
            row.started_at = now
        db.commit()
    except Exception as exc:
        logger.debug("metrics.heartbeat failed: %s", exc)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def ai_cost_usd(pairs: list[str]) -> float:
    """Sum USD cost of ai_* events from their 'input,output' details."""
    total = 0.0
    for detail in pairs:
        if not detail or "," not in detail:
            continue
        try:
            tin, tout = (int(x) for x in detail.split(",", 1))
        except ValueError:
            continue
        total += tin * PRICE_IN_PER_MTOK / 1e6 + tout * PRICE_OUT_PER_MTOK / 1e6
    return total
