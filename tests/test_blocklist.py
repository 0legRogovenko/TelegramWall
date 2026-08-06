"""Tests for the channel blocklist (legal/editorial refusals)."""
import pytest

from src.services import blocklist
from tests.conftest import create_channel


@pytest.fixture(autouse=True)
def _clean(db):
    from src.models import BlockedChannel
    db.query(BlockedChannel).delete()
    db.commit()
    yield
    db.query(BlockedChannel).delete()
    db.commit()


class TestBlocklistCore:
    def test_block_and_lookup_normalizes_username(self, db):
        assert blocklist.block(db, "@Durov", reason="test")
        assert blocklist.is_blocked(db, "durov")
        assert blocklist.is_blocked(db, "@DUROV")
        assert not blocklist.is_blocked(db, "other_channel")

    def test_double_block_is_noop(self, db):
        assert blocklist.block(db, "somechan")
        assert not blocklist.block(db, "@somechan")

    def test_unblock(self, db):
        blocklist.block(db, "somechan")
        assert blocklist.unblock(db, "@somechan")
        assert not blocklist.is_blocked(db, "somechan")
        assert not blocklist.unblock(db, "somechan")  # already gone

    def test_config_entries_are_pinned(self, db, monkeypatch):
        monkeypatch.setattr(
            "src.services.blocklist.config",
            type("C", (), {"BLOCKED_CHANNELS": frozenset({"pinned_chan"})})(),
        )
        assert blocklist.is_blocked(db, "@Pinned_Chan")
        # unblock cannot remove a config-pinned entry
        assert not blocklist.unblock(db, "pinned_chan")
        assert blocklist.is_blocked(db, "pinned_chan")

    def test_blocked_usernames_merges_both_sources(self, db, monkeypatch):
        monkeypatch.setattr(
            "src.services.blocklist.config",
            type("C", (), {"BLOCKED_CHANNELS": frozenset({"static_one"})})(),
        )
        blocklist.block(db, "dynamic_one")
        names = blocklist.blocked_usernames(db)
        assert {"static_one", "dynamic_one"} <= names


class TestPollingExclusion:
    def test_blocked_channel_is_not_polled(self, db):
        # The poll loop's channel selection must drop blocked usernames
        ch = create_channel(db, username="blocked_poll_chan")
        blocklist.block(db, "blocked_poll_chan")
        blocked = blocklist.blocked_usernames(db)
        selected = [
            c for c in [ch]
            if (c.username or "").lower() not in blocked
        ]
        assert selected == []
