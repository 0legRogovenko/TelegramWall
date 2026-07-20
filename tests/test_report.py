"""Tests for ops metrics and the daily admin report."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from src.models import BotEvent, BotHealth
from src.services import metrics
from src.services.report import build_report
from tests.conftest import create_user


def _event(db, etype: str, detail: str | None = None, hours_ago: float = 0):
    ev = BotEvent(type=etype, detail=detail)
    ev.created_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    db.add(ev)
    db.commit()
    return ev


class TestAiCost:
    def test_costs_tokens_at_haiku_prices(self):
        # 1M input = $1, 1M output = $5
        assert metrics.ai_cost_usd(["1000000,0"]) == 1.0
        assert metrics.ai_cost_usd(["0,1000000"]) == 5.0

    def test_sums_multiple_calls(self):
        cost = metrics.ai_cost_usd(["300,120", "200,3"])
        expected = (300 + 200) / 1e6 * 1.0 + (120 + 3) / 1e6 * 5.0
        assert abs(cost - expected) < 1e-12

    def test_ignores_malformed_details(self):
        assert metrics.ai_cost_usd([None, "", "junk", "1,2,3"]) == 0.0


class TestRecordAndFlush:
    @pytest.fixture(autouse=True)
    def _clean(self, db):
        metrics._buffer.clear()
        db.query(BotEvent).delete()
        db.commit()

    def test_record_buffers_without_touching_the_db(self, db):
        metrics.record(metrics.POST_SAVED)
        # Nothing may reach the DB yet: record() runs on the asyncio loop thread
        # in the delivery path and must not pay for a round trip.
        assert db.query(BotEvent).count() == 0
        assert len(metrics._buffer) == 1

    def test_flush_writes_buffered_events(self, db):
        metrics.record(metrics.POST_SAVED)
        metrics.record(metrics.DELIVERED_POST, "to 42")
        assert metrics.flush() == 2
        assert db.query(BotEvent).count() == 2
        assert metrics._buffer == []

    def test_flush_is_a_noop_when_empty(self):
        assert metrics.flush() == 0

    def test_record_never_raises_and_flush_requeues_on_db_failure(self, db, monkeypatch):
        def boom():
            raise RuntimeError("DB down")

        monkeypatch.setattr("src.services.metrics.SessionFactory", boom)
        metrics.record(metrics.POST_SAVED)  # must not raise
        assert metrics.flush() == 0
        # Events survive for the next tick rather than being dropped
        assert len(metrics._buffer) == 1

    def test_buffer_is_bounded(self):
        for i in range(metrics.MAX_BUFFERED + 50):
            metrics.record(metrics.POST_SAVED, str(i))
        assert len(metrics._buffer) == metrics.MAX_BUFFERED
        # The oldest are dropped, newest kept
        assert metrics._buffer[-1][1] == str(metrics.MAX_BUFFERED + 49)

    def test_heartbeat_stamps_and_creates_the_singleton(self, db):
        db.query(BotHealth).delete()
        db.commit()
        metrics.heartbeat(started=True)
        row = db.query(BotHealth).filter_by(id=1).first()
        assert row is not None and row.last_seen_at is not None
        assert row.started_at is not None

    def test_heartbeat_never_raises(self, monkeypatch):
        def boom():
            raise RuntimeError("DB down")

        monkeypatch.setattr("src.services.metrics.SessionFactory", boom)
        metrics.heartbeat()  # must not raise


class TestCleanup:
    @pytest.fixture(autouse=True)
    def _clean(self, db):
        metrics._buffer.clear()
        db.query(BotEvent).delete()
        db.commit()

    def test_purges_old_events_even_with_no_expired_posts(self, db):
        # Regression: the bot_events purge used to sit below the "no old posts"
        # early return, so the ops log grew forever on quiet days.
        from src.userbot.monitor import _cleanup_old_posts

        _event(db, metrics.POST_SAVED, hours_ago=24 * 30)
        _event(db, metrics.POST_SAVED, hours_ago=1)
        assert _cleanup_old_posts(db) == 0  # no posts at all in the DB
        remaining = db.query(BotEvent).all()
        assert len(remaining) == 1, "old event should have been purged"


class TestBuildReport:
    @pytest.fixture(autouse=True)
    def _clean_events(self, db):
        # The suite shares one in-memory DB and _event() commits, so wipe the
        # event log first — otherwise counts leak in from other tests.
        metrics._buffer.clear()
        db.query(BotEvent).delete()
        db.commit()

    def test_escapes_html_in_error_detail(self, db):
        # A 502 from the AI gateway embeds an HTML body; unescaped it makes
        # Telegram reject the entire report.
        _event(db, metrics.ERROR_AI, "filter: 502 - <html><head>&bad")
        text = build_report(db)
        assert "<html><head>" not in text
        assert "&lt;html&gt;&lt;head&gt;&amp;bad" in text

    def test_counts_only_last_24h(self, db):
        _event(db, metrics.POST_SAVED, hours_ago=1)
        _event(db, metrics.POST_SAVED, hours_ago=2)
        _event(db, metrics.POST_SAVED, hours_ago=30)  # older than the window
        text = build_report(db)
        assert "Новых постов собрано: <b>2</b>" in text

    def test_reports_ai_cost_from_real_tokens(self, db):
        _event(db, metrics.AI_SUMMARY, "1000000,0")  # exactly $1.00
        text = build_report(db)
        assert "$1.0000" in text

    def test_no_errors_line_when_clean(self, db):
        text = build_report(db)
        assert "Ошибок нет" in text

    def test_groups_errors_and_shows_last(self, db):
        _event(db, metrics.ERROR_DELIVERY, "Timed out")
        _event(db, metrics.ERROR_DELIVERY, "Flood control")
        text = build_report(db)
        assert "Ошибки: 2" in text
        assert "delivery: 2" in text
        assert "Flood control" in text

    def test_counts_only_real_error_types(self, db):
        # "error_%" as a LIKE pattern would match this too: _ is a wildcard.
        _event(db, "errors_total", "not an error event")
        assert "Ошибок нет" in build_report(db)

    def test_new_user_increments_daily_count(self, db):
        def new_count() -> int:
            m = re.search(r"\(\+(\d+) за сутки\)", build_report(db))
            assert m, "daily new-user counter missing from report"
            return int(m.group(1))

        before = new_count()
        create_user(db, telegram_id=7701)
        assert new_count() == before + 1

    def test_survives_without_health_row(self, db):
        db.query(BotHealth).delete()
        db.commit()
        text = build_report(db)  # must not raise
        assert "Отчёт за сутки" in text


class TestDeadChannels:
    def test_unresolvable_channel_is_surfaced(self, db):
        from src.models import Channel
        ch = Channel(username="dead_chan_xyz", title="dead", telegram_id=None)
        db.add(ch)
        db.commit()
        text = build_report(db)
        assert "Каналы без доступа" in text
        assert "@dead_chan_xyz" in text
        db.delete(ch)
        db.commit()

    def test_no_dead_channel_section_when_all_resolve(self, db):
        from src.models import Channel
        # Uncommitted update — the db fixture rolls it back after the test
        db.query(Channel).filter(Channel.telegram_id.is_(None)).update(
            {Channel.telegram_id: 424242}, synchronize_session=False
        )
        assert "Каналы без доступа" not in build_report(db)
