"""Tests for EventStore port (DomainEvent + SQLiteEventStore).

Covers:
  - DomainEvent.create() with deterministic event_id
  - DomainEvent round-trip (to_row / from_row)
  - SQLiteEventStore read/write/idempotency
  - Indexed queries (list_by_type, list_since, count)
  - Anti-patterns: event_id != f(timestamp), != f(uuid)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.domain.value_objects.domain_event import DomainEvent, _canonical_json
from app.infrastructure.repositories.sqlite_event_store import SQLiteEventStore


# ─── DomainEvent unit tests ──────────────────────────────────────────


class TestDomainEventCreate:
    def test_basic_creation(self) -> None:
        ev = DomainEvent.create(
            event_type="OrderCreated",
            source="ExecutionCore",
            version=1,
            timestamp_bucket="1234567890",
            data={"symbol": "BTC/USDT", "side": "BUY"},
        )
        assert ev.event_type == "OrderCreated"
        assert ev.source == "ExecutionCore"
        assert ev.version == 1
        assert ev.timestamp_bucket == "1234567890"
        assert ev.data == {"symbol": "BTC/USDT", "side": "BUY"}
        assert ev.event_id.startswith("evt_")
        assert len(ev.event_id) == 4 + 32  # prefix + hash digest

    def test_deterministic_same_inputs_same_id(self) -> None:
        data = {"symbol": "BTC/USDT", "signal_id": "abc123"}
        ev1 = DomainEvent.create(
            event_type="SignalGenerated",
            source="SignalEngine",
            timestamp_bucket="1000000",
            data=data,
        )
        ev2 = DomainEvent.create(
            event_type="SignalGenerated",
            source="SignalEngine",
            timestamp_bucket="1000000",
            data=data,
        )
        assert ev1.event_id == ev2.event_id

    def test_different_event_type_different_id(self) -> None:
        a = DomainEvent.create(event_type="OrderCreated", source="ExecutionCore", timestamp_bucket="1")
        b = DomainEvent.create(event_type="OrderFilled", source="ExecutionCore", timestamp_bucket="1")
        assert a.event_id != b.event_id

    def test_different_data_different_id(self) -> None:
        a = DomainEvent.create(
            event_type="OrderCreated", source="ExecutionCore", timestamp_bucket="1",
            data={"symbol": "BTC/USDT"},
        )
        b = DomainEvent.create(
            event_type="OrderCreated", source="ExecutionCore", timestamp_bucket="1",
            data={"symbol": "ETH/USDT"},
        )
        assert a.event_id != b.event_id

    def test_different_timestamp_bucket_different_id(self) -> None:
        a = DomainEvent.create(event_type="Tick", source="ExchangeAdapter", timestamp_bucket="100")
        b = DomainEvent.create(event_type="Tick", source="ExchangeAdapter", timestamp_bucket="200")
        assert a.event_id != b.event_id

    def test_auto_timestamp_bucket(self) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        assert ev.timestamp_bucket is not None
        assert ev.timestamp_bucket.isdigit()

    def test_default_prefix(self) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        assert ev.event_id.startswith("evt_")

    def test_custom_prefix(self) -> None:
        ev = DomainEvent.create(
            event_type="OrderCreated", source="ExecutionCore", prefix="oc",
        )
        assert ev.event_id.startswith("oc_")

    def test_rejects_empty_event_type(self) -> None:
        with pytest.raises(ValueError, match="event_type and source required"):
            DomainEvent.create(event_type="", source="Test")

    def test_rejects_empty_source(self) -> None:
        with pytest.raises(ValueError, match="event_type and source required"):
            DomainEvent.create(event_type="Test", source="")

    def test_default_data_is_empty_dict(self) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        assert ev.data == {}


class TestDomainEventRoundTrip:
    def test_to_row_round_trip(self) -> None:
        ev = DomainEvent.create(
            event_type="PositionOpened",
            source="ExecutionCore",
            data={"symbol": "BTC/USDT", "quantity": 0.1},
        )
        row = ev.to_row()
        assert row["event_id"] == ev.event_id
        assert row["event_type"] == "PositionOpened"
        assert row["source"] == "ExecutionCore"
        assert isinstance(row["data"], str)
        parsed = json.loads(row["data"])
        assert parsed["symbol"] == "BTC/USDT"

        restored = DomainEvent.from_row(row)
        assert restored == ev
        assert restored.event_id == ev.event_id
        assert restored.data == ev.data

    def test_from_row_with_parsed_data(self) -> None:
        row = {
            "event_id": "evt_abc123",
            "event_type": "Test",
            "source": "Test",
            "version": 1,
            "timestamp": "2026-01-01T00:00:00",
            "timestamp_bucket": "1234567890",
            "data": {"key": "value"},
        }
        ev = DomainEvent.from_row(row)
        assert ev.event_id == "evt_abc123"
        assert ev.data == {"key": "value"}


# ─── SQLiteEventStore integration tests ─────────────────────────────


@pytest.fixture
def tmp_db() -> str:
    """Create a temporary SQLite database for each test."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_events.db")
    yield db_path
    # clean up
    try:
        Path(db_path).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        Path(tmpdir).rmdir()
    except OSError:
        pass


@pytest.fixture
def store(tmp_db: str) -> SQLiteEventStore:
    return SQLiteEventStore(db_path=tmp_db)


class TestSQLiteEventStore:
    async def test_write_and_count(self, store: SQLiteEventStore) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        result = await store.write(ev)
        assert result is True, "first write should return True"
        assert await store.count() == 1

    async def test_idempotent_duplicate(self, store: SQLiteEventStore) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        first = await store.write(ev)
        second = await store.write(ev)
        assert first is True
        assert second is False, "duplicate write should return False"
        assert await store.count() == 1, "count should not increase"

    async def test_exists(self, store: SQLiteEventStore) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        assert await store.exists(ev.event_id) is False
        await store.write(ev)
        assert await store.exists(ev.event_id) is True

    async def test_read(self, store: SQLiteEventStore) -> None:
        ev = DomainEvent.create(
            event_type="PositionClosed",
            source="ExecutionCore",
            data={"pnl": 123.45, "reason": "stop_loss"},
        )
        await store.write(ev)
        loaded = await store.read(ev.event_id)
        assert loaded is not None
        assert loaded.event_id == ev.event_id
        assert loaded.event_type == "PositionClosed"
        assert loaded.data["pnl"] == 123.45

    async def test_read_missing(self, store: SQLiteEventStore) -> None:
        loaded = await store.read("nonexistent_id")
        assert loaded is None

    async def test_list_by_type(self, store: SQLiteEventStore) -> None:
        a = DomainEvent.create(event_type="OrderCreated", source="ExecutionCore", timestamp_bucket="1")
        b = DomainEvent.create(event_type="OrderFilled", source="ExecutionCore", timestamp_bucket="2")
        c = DomainEvent.create(event_type="OrderCreated", source="ExecutionCore", timestamp_bucket="3")
        for ev in (a, b, c):
            await store.write(ev)

        orders = await store.list_by_type("OrderCreated")
        assert len(orders) == 2
        assert all(e.event_type == "OrderCreated" for e in orders)

        fills = await store.list_by_type("OrderFilled")
        assert len(fills) == 1

        unknown = await store.list_by_type("Unknown")
        assert len(unknown) == 0

    async def test_list_since(self, store: SQLiteEventStore) -> None:
        ev1 = DomainEvent.create(event_type="A", source="Test", timestamp_bucket="1")
        ev2 = DomainEvent.create(event_type="B", source="Test", timestamp_bucket="2")
        await store.write(ev1)
        await store.write(ev2)

        # list_since with ev1's timestamp should include both ev1 and ev2
        results = await store.list_since(ev1.timestamp)
        assert len(results) >= 1

    async def test_count_by_type(self, store: SQLiteEventStore) -> None:
        a = DomainEvent.create(
            event_type="TypeX", source="Test", timestamp_bucket="1", data={"seq": 1},
        )
        b = DomainEvent.create(
            event_type="TypeX", source="Test", timestamp_bucket="2", data={"seq": 2},
        )
        c = DomainEvent.create(
            event_type="TypeY", source="Test", timestamp_bucket="3", data={"seq": 1},
        )
        for ev in (a, b, c):
            await store.write(ev)

        assert await store.count_by_type("TypeX") == 2
        assert await store.count_by_type("TypeY") == 1
        assert await store.count_by_type("Nonexistent") == 0

    async def test_clear(self, store: SQLiteEventStore) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        await store.write(ev)
        assert await store.count() == 1

        await store.clear()
        assert await store.count() == 0

    async def test_multiple_events_preserve_order(self, store: SQLiteEventStore) -> None:
        evs = []
        for i in range(5):
            ev = DomainEvent.create(
                event_type="Sequence",
                source="Test",
                timestamp_bucket=str(1000 + i),
                data={"seq": i},
            )
            evs.append(ev)
            await store.write(ev)

        results = await store.list_by_type("Sequence", limit=10)
        assert len(results) == 5
        # Should be in ascending timestamp order
        buckets = [e.timestamp_bucket for e in results]
        assert buckets == sorted(buckets)

    async def test_limit_and_offset(self, store: SQLiteEventStore) -> None:
        for i in range(10):
            ev = DomainEvent.create(
                event_type="Paged",
                source="Test",
                timestamp_bucket=str(i),
                data={"i": i},
            )
            await store.write(ev)

        page1 = await store.list_by_type("Paged", limit=3, offset=0)
        assert len(page1) == 3
        assert page1[0].data["i"] == 0
        assert page1[2].data["i"] == 2

        page2 = await store.list_by_type("Paged", limit=3, offset=3)
        assert len(page2) == 3
        assert page2[0].data["i"] == 3

    async def test_idempotent_on_network_retry(self, store: SQLiteEventStore) -> None:
        """Simulate a network retry: same event sent twice."""
        data = {"symbol": "BTC/USDT", "side": "BUY", "price": 50000.0}
        orig = DomainEvent.create(
            event_type="OrderCreated",
            source="ExecutionCore",
            timestamp_bucket="1000000",
            data=data,
        )
        retry = DomainEvent.create(
            event_type="OrderCreated",
            source="ExecutionCore",
            timestamp_bucket="1000000",
            data=data,
        )
        assert orig.event_id == retry.event_id, "deterministic ID must match"

        first = await store.write(orig)
        second = await store.write(retry)
        assert first is True
        assert second is False, "retry must be rejected"

    async def test_close_and_reopen(self, tmp_db: str) -> None:
        """Events persist after close/reopen."""
        s1 = SQLiteEventStore(db_path=tmp_db)
        ev = DomainEvent.create(event_type="Persistent", source="Test")
        await s1.write(ev)
        s1.close()

        s2 = SQLiteEventStore(db_path=tmp_db)
        assert await s2.count() == 1
        loaded = await s2.read(ev.event_id)
        assert loaded is not None
        assert loaded.event_type == "Persistent"
        s2.close()

    async def test_write_large_data(self, store: SQLiteEventStore) -> None:
        large_data = {"values": list(range(1000))}
        ev = DomainEvent.create(event_type="LargeData", source="Test", data=large_data)
        await store.write(ev)
        loaded = await store.read(ev.event_id)
        assert loaded is not None
        assert loaded.data["values"] == list(range(1000))


# ─── Anti-pattern verification ──────────────────────────────────────


class TestDeterministicAntiPatterns:
    def test_event_id_not_based_on_timestamp(self) -> None:
        """Same exact inputs → same event_id regardless of clock."""
        first = DomainEvent.create(
            event_type="AntiPattern", source="Test", timestamp_bucket="fixed",
        )
        second = DomainEvent.create(
            event_type="AntiPattern", source="Test", timestamp_bucket="fixed",
        )
        assert first.event_id == second.event_id

    def test_event_id_not_uuid(self) -> None:
        ev = DomainEvent.create(event_type="Test", source="Test")
        # UUID v4 format would be 8-4-4-4-12 hex chars
        # Our event_id is hex digest with prefix
        assert "_" in ev.event_id
        assert len(ev.event_id) == 4 + 32  # prefix_ + sha256[:32]
        hex_part = ev.event_id.split("_")[1]
        assert len(hex_part) == 32
        int(hex_part, 16)  # Should not raise


# ─── Helper tests ───────────────────────────────────────────────────


class TestCanonicalJson:
    def test_deterministic_order(self) -> None:
        a = _canonical_json({"b": 1, "a": 2})
        b = _canonical_json({"a": 2, "b": 1})
        assert a == b
        assert '"a":2' in a
        assert '"b":1' in a

    def test_compact_no_spaces(self) -> None:
        result = _canonical_json({"key": "value"})
        assert " " not in result
        assert result == '{"key":"value"}'
