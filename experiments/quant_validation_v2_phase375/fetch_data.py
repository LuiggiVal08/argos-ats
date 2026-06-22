"""Fetch OHLCV 1h + funding rates for ETH and SOL.

Usage:
  python3 -m experiments.quant_validation_v2_phase375.fetch_data

Fetches and caches:
  apps/analytics-engine/data/{symbol}_usdt_1h.parquet
  apps/analytics-engine/data/{symbol}_funding_rates.parquet
"""

from __future__ import annotations

import time
from pathlib import Path

import ccxt
import pandas as pd

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["ETH", "SOL"]
TIMEFRAME = "1h"
YEARS = 4
LIMIT = 1000
DELAY_MS = 250


def fetch_ohlcv(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_usdt_1h.parquet"
    if path.exists():
        sz = path.stat().st_size
        print(f"[fetch] {symbol} OHLCV already cached: {path.name} ({sz/1024:.0f} KB)")
        return pd.read_parquet(path)

    print(f"[fetch] {symbol} OHLCV: fetching from Binance spot...")
    exchange = ccxt.binance({"enableRateLimit": True})
    since = exchange.parse8601(f"2022-01-01T00:00:00Z")
    all_candles: list[list] = []
    total = 0
    t0 = time.time()

    while True:
        try:
            candles = exchange.fetch_ohlcv(
                f"{symbol}/USDT", TIMEFRAME, since=since, limit=LIMIT
            )
        except Exception as e:
            print(f"[fetch] {symbol} OHLCV error: {e}; retrying in 5s...")
            time.sleep(5)
            continue

        if not candles:
            break

        all_candles.extend(candles)
        total += len(candles)
        since = candles[-1][0] + 1
        elapsed = time.time() - t0
        print(f"  fetched {total} candles ({elapsed:.0f}s)")

        if len(candles) < LIMIT:
            break
        time.sleep(DELAY_MS / 1000)

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(path, index=False)
    print(f"[fetch] {symbol} OHLCV saved: {path} ({len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()})")
    return df


def fetch_funding(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_funding_rates.parquet"
    if path.exists():
        sz = path.stat().st_size
        print(f"[fetch] {symbol} funding already cached: {path.name} ({sz/1024:.0f} KB)")
        return pd.read_parquet(path)

    print(f"[fetch] {symbol} funding: fetching from Binance Futures...")
    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    since = exchange.parse8601(f"2022-01-01T00:00:00Z")
    all_rates: list[list] = []
    total = 0
    t0 = time.time()

    while True:
        try:
            rates = exchange.fetch_funding_rate_history(
                f"{symbol}/USDT", since=since, limit=LIMIT
            )
        except Exception as e:
            print(f"[fetch] {symbol} funding error: {e}; retrying in 5s...")
            time.sleep(5)
            continue

        if not rates:
            break

        rows = [[r["timestamp"], r["fundingRate"]] for r in rates]
        all_rates.extend(rows)
        total += len(rows)
        since = rates[-1]["timestamp"] + 1
        elapsed = time.time() - t0
        print(f"  fetched {total} funding records ({elapsed:.0f}s)")

        if len(rows) < LIMIT:
            break
        time.sleep(DELAY_MS / 1000)

    df = pd.DataFrame(all_rates, columns=["timestamp", "fundingRate"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df["symbol"] = f"{symbol}/USDT"
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(path, index=False)
    print(f"[fetch] {symbol} funding saved: {path} ({len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()})")
    return df


def main():
    print("=" * 60)
    print("QV2 Phase 3.75 — Data fetcher")
    print("=" * 60)

    t0 = time.time()
    for sym in SYMBOLS:
        print(f"\n{'─' * 40}")
        fetch_ohlcv(sym)
        fetch_funding(sym)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"All data fetched: {elapsed:.0f}s")

    for sym in SYMBOLS:
        for kind in ["usdt_1h", "funding_rates"]:
            path = DATA_DIR / f"{sym.lower()}_{kind}.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                print(f"  {path.name}: {len(df):>6} rows, {str(df['timestamp'].min())[:10]} → {str(df['timestamp'].max())[:10]}")


if __name__ == "__main__":
    main()
