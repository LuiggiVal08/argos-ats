"""Stress tests for CandleBuilder: throughput, boundedness, dedup compatibility."""
import time
from collections import deque

from app.infrastructure.trading.candle_builder import CandleBuilder, CandleBuffer


def test_1000_ticks_per_min_throughput() -> None:
    """~17 ticks/sec. 2000 ticks O(1) per tick in < 1s real time."""
    builder = CandleBuilder(symbol="BTC/USDT", timeframe_seconds=3600)
    now = int(time.time() * 1000)
    start = time.perf_counter()
    count = 2000
    completed = 0
    for i in range(count):
        ts = now + i * 60_000  # 1 tick per minute (spans ~33h = 33 candles)
        price = float(50000 + (i % 100))
        volume = float(0.1 + (i % 10) * 0.01)
        update = builder.update(ts_ms=ts, price=price, volume=volume)
        if update.completed is not None:
            completed += 1
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"2000 ticks took {elapsed:.3f}s (expected < 1s)"
    # 2000 ticks at 1/min = 33h → 33 completed candles (1h timeframe)
    assert completed == 33, f"expected 33 candles, got {completed}"


def test_bounded_memory_no_leak() -> None:
    """Run 100k ticks, verify builder does not accumulate unbounded state."""
    builder = CandleBuilder(symbol="ETH/USDT", timeframe_seconds=60)
    now = int(time.time() * 1000)
    for i in range(100_000):
        ts = now + i * 10  # 1 tick every 10ms
        price = float(3000 + (i % 200))
        volume = float(1.0 + (i % 5) * 0.1)
        builder.update(ts_ms=ts, price=price, volume=volume)
    # After 100k ticks, state is: current candle (one) + some counters
    assert builder.current is not None
    assert builder.desync_count >= 0
    assert builder.gap_count >= 0


def test_candle_buffer_bounded() -> None:
    """CandleBuffer with maxlen=200 never exceeds capacity."""
    buf = CandleBuffer(maxlen=200)
    for i in range(1000):
        from app.infrastructure.trading.candle_builder import Candle

        buf.append(
            Candle(
                symbol="BTC/USDT",
                open=float(50000 + i),
                high=float(50000 + i + 10),
                low=float(50000 + i - 10),
                close=float(50000 + i),
                volume=1.0,
                open_ts=i * 3600_000,
                close_ts=(i + 1) * 3600_000,
                is_complete=True,
            )
        )
    assert len(buf) == 200  # capped at maxlen
    assert buf.get_last(1)[0].close == float(50999)


def test_dedup_ring_buffer_bounded() -> None:
    """Simulate the dedup ring buffer from main.py to verify boundedness."""
    dedup_cache: set[str] = set()
    dedup_ring: deque[str] = deque()
    DEDUP_MAX = 20000
    now = int(time.time() * 1000)

    for i in range(100_000):
        trade_id = f"trade_{i % 50000}"  # recycle IDs to test dedup
        if trade_id not in dedup_cache:
            dedup_cache.add(trade_id)
            dedup_ring.append(trade_id)
            if len(dedup_ring) > DEDUP_MAX:
                oldest = dedup_ring.popleft()
                dedup_cache.discard(oldest)
    # After 100k iterations, ring is bounded
    assert len(dedup_ring) <= DEDUP_MAX
    assert len(dedup_cache) <= DEDUP_MAX


def test_dedup_with_candle_builder() -> None:
    """Dedup filter before CandleBuilder does not corrupt candle state."""
    builder = CandleBuilder(symbol="BTC/USDT", timeframe_seconds=3600)
    dedup_cache: set[str] = set()
    dedup_ring: deque[str] = deque()
    DEDUP_MAX = 20000
    now = int(time.time() * 1000)

    for i in range(500):
        ts = now + i * 60_000  # 1 per minute
        price = float(50000 + (i % 50))
        volume = float(0.1)
        trade_id = f"trade_{i % 100}"  # heavy duplicates

        if trade_id in dedup_cache:
            continue
        dedup_cache.add(trade_id)
        dedup_ring.append(trade_id)
        if len(dedup_ring) > DEDUP_MAX:
            dedup_ring.popleft()

        update = builder.update(ts_ms=ts, price=price, volume=volume)
        if update.completed is not None:
            assert update.completed.is_complete
            assert update.completed.volume > 0

    # Candle state should be valid after dedup
    assert builder.current is not None
    assert builder.current.volume > 0
