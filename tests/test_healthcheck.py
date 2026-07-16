"""Tests for the external watchdog (scripts/healthcheck.py).

The watchdog runs as its own GitHub Actions job and never imports the app, so
it is loaded here straight from its path. Its whole job is to be the one thing
still talking when the bot is dead — these tests pin the failure modes where it
would instead go quiet.
"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "healthcheck.py"


def _load_healthcheck():
    spec = importlib.util.spec_from_file_location("healthcheck", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["healthcheck"] = module
    spec.loader.exec_module(module)
    return module


hc = _load_healthcheck()


@pytest.fixture()
def db_url(tmp_path):
    """A real SQLite file with a bot_health table — the shape prod reads."""
    url = f"sqlite:///{tmp_path / 'health.db'}"
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE bot_health (
                id INTEGER PRIMARY KEY,
                started_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                last_report_on VARCHAR(10),
                alert_sent_at TIMESTAMP
            )
        """))
        conn.commit()
    return url


def _seed(url, last_seen_min_ago=None, alert_sent_min_ago=None):
    now = datetime.now(timezone.utc)
    seen = now - timedelta(minutes=last_seen_min_ago) if last_seen_min_ago is not None else None
    alert = now - timedelta(minutes=alert_sent_min_ago) if alert_sent_min_ago is not None else None
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO bot_health (id, last_seen_at, alert_sent_at) "
                 "VALUES (1, :seen, :alert)"),
            {"seen": seen, "alert": alert},
        )
        conn.commit()


def _alert_sent_at(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        return conn.execute(text("SELECT alert_sent_at FROM bot_health WHERE id = 1")).scalar()


@pytest.fixture()
def env(monkeypatch, db_url):
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test")
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "111,222")
    return db_url


@pytest.fixture()
def sent(monkeypatch):
    """Capture Telegram sends instead of hitting the network."""
    calls = []
    monkeypatch.setattr(hc, "_send", lambda token, chat, msg: calls.append((chat, msg)))
    return calls


class TestHealthy:
    def test_stays_quiet_when_bot_is_alive(self, env, sent):
        _seed(env, last_seen_min_ago=3)
        assert hc.main() == 0
        assert sent == []

    def test_quiet_when_no_health_row_yet(self, env, sent):
        assert hc.main() == 0  # bot has never started; nothing to say
        assert sent == []


class TestDown:
    def test_alerts_every_admin_when_heartbeat_is_stale(self, env, sent):
        _seed(env, last_seen_min_ago=45)
        assert hc.main() == 1
        assert [c for c, _ in sent] == ["111", "222"]
        assert "не отвечает" in sent[0][1]
        assert _alert_sent_at(env) is not None

    def test_does_not_spam_while_still_down(self, env, sent):
        _seed(env, last_seen_min_ago=45, alert_sent_min_ago=10)
        assert hc.main() == 0
        assert sent == []

    def test_realerts_after_the_repeat_window(self, env, sent):
        _seed(env, last_seen_min_ago=400, alert_sent_min_ago=60 * hc.REALERT_HOURS + 10)
        assert hc.main() == 1
        assert len(sent) == 2


class TestUndeliverableAlert:
    """The bug class that mattered most: state written for an alert nobody got."""

    def test_failed_alert_does_not_mark_as_sent(self, env, monkeypatch):
        _seed(env, last_seen_min_ago=45)

        def boom(token, chat, msg):
            raise OSError("telegram unreachable")

        monkeypatch.setattr(hc, "_send", boom)
        assert hc.main() == 1
        # Must stay NULL so the next tick retries — stamping here would mute
        # the watchdog for REALERT_HOURS over a momentary Telegram blip.
        assert _alert_sent_at(env) is None

    def test_partial_delivery_still_counts_as_sent(self, env, monkeypatch):
        _seed(env, last_seen_min_ago=45)

        def flaky(token, chat, msg):
            if chat == "111":
                raise OSError("blocked")

        monkeypatch.setattr(hc, "_send", flaky)
        assert hc.main() == 1
        assert _alert_sent_at(env) is not None  # admin 222 got it


class TestRecovery:
    def test_announces_recovery_once_and_clears_the_flag(self, env, sent):
        _seed(env, last_seen_min_ago=2, alert_sent_min_ago=30)
        assert hc.main() == 0
        assert "снова в строю" in sent[0][1]
        assert _alert_sent_at(env) is None

    def test_failed_recovery_notice_keeps_the_flag_for_a_retry(self, env, monkeypatch):
        _seed(env, last_seen_min_ago=2, alert_sent_min_ago=30)

        def boom(token, chat, msg):
            raise OSError("telegram unreachable")

        monkeypatch.setattr(hc, "_send", boom)
        assert hc.main() == 0
        assert _alert_sent_at(env) is not None  # all-clear not lost


class TestDatabaseFailure:
    def test_alerts_over_telegram_when_the_db_is_unreachable(self, monkeypatch, sent):
        # A DB outage means the bot is almost certainly down too — the watchdog
        # must speak up rather than die with a traceback nobody reads.
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@127.0.0.1:9/nope")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test")
        monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "111")
        assert hc.main() == 1
        assert len(sent) == 1
        assert "не смог прочитать" in sent[0][1]

    def test_quiet_when_table_does_not_exist_yet(self, tmp_path, monkeypatch, sent):
        # First deploy: this cron can fire before the bot ever ran init_db().
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test")
        monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "111")
        assert hc.main() == 0
        assert sent == []


class TestConfig:
    def test_blank_env_var_falls_back_to_default(self, monkeypatch):
        # An unset GitHub Actions variable arrives as "" — int("") would crash.
        monkeypatch.setenv("HEARTBEAT_STALE_MINUTES", "")
        assert hc._int_env("HEARTBEAT_STALE_MINUTES", 20) == 20

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("HEARTBEAT_STALE_MINUTES", "45")
        assert hc._int_env("HEARTBEAT_STALE_MINUTES", 20) == 45

    def test_no_admins_configured_is_a_quiet_noop(self, monkeypatch, sent):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///nope.db")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test")
        monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "")
        assert hc.main() == 0
        assert sent == []
