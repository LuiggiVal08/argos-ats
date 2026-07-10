"""Fetch OHLCV 1h + funding rates for Bybit and OKX.

Usage:
  python3 -m experiments.quant_validation_v2_phase38.fetch_data

Skips Binance (data already exists from Phase 3.75).
For each (symbol, exchange), fetches and caches:
  apps/analytics-engine/data/{symbol}_{exchange}_usdt_1h.parquet
  apps/analytics-engine/data/{symbol}_{exchange}_funding_rates.parquet

Funding rate fallback chain:
  1. exchange.fetchFundingRateHistory() via CCXT
  2. Direct REST (public endpoint)
"""

from __future__ import annotations

import time
import json
from pathlib import Path
from urllib.request import urlopen, Request

import ccxt
import pandas as pd

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGES = ["bybit", "okx"]
TIMEFRAME = "1h"
LIMIT = 1000
DELAY_MS = 250


# ── exchange config ──────────────────────────────────────────────


def get_ohlcv_exchange(name: str):
    if name == "bybit":
        return ccxt.bybit({"defaultType": "swap", "enableRateLimit": True})
    elif name == "okx":
        return ccxt.okx({"enableRateLimit": True})
    raise ValueError(f"Unknown exchange: {name}")


def get_funding_exchange(name: str):
    if name == "bybit":
        return ccxt.bybit({"defaultType": "swap", "enableRateLimit": True})
    elif name == "okx":
        return ccxt.okx({"enableRateLimit": True})
    raise ValueError(f"Unknown exchange: {name}")


def ohlcv_symbol(symbol: str) -> str:
    return f"{symbol}/USDT:USDT"


def funding_symbol(symbol: str) -> str:
    return f"{symbol}/USDT:USDT"


# ── file paths ────────────────────────────────────────────────────


def ohlcv_path(symbol: str, exchange: str) -> Path:
    return DATA_DIR / f"{symbol.lower()}_{exchange}_usdt_1h.parquet"


def funding_path(symbol: str, exchange: str) -> Path:
    return DATA_DIR / f"{symbol.lower()}_{exchange}_funding_rates.parquet"


# ── fetch helpers ────────────────────────────────────────────────


def _fetch_ohlcv_single(symbol_root: str, exchange: str) -> pd.DataFrame:
    path = ohlcv_path(symbol_root, exchange)
    if path.exists():
        sz = path.stat().st_size
        print(f"  [skip] {symbol_root} OHLCV already cached: {path.name} ({sz/1024:.0f} KB)")
        return pd.read_parquet(path)

    ex = get_ohlcv_exchange(exchange)
    sym = ohlcv_symbol(symbol_root)
    target_start = ex.parse8601("2022-01-01T00:00:00Z")
    # Use backward pagination: newest → oldest
    until = ex.milliseconds()
    all_candles: list[list] = []
    total = 0
    t0 = time.time()
    chunk_hours = int(LIMIT * 1.2)

    print(f"  [fetch] {symbol_root}/{exchange.upper()} OHLCV 1h (backward)...")
    while True:
        since = max(target_start, int(until - chunk_hours * 3600 * 1000))
        try:
            candles = ex.fetch_ohlcv(sym, TIMEFRAME, since=since, limit=LIMIT)
        except Exception as e:
            print(f"    error: {e}; retrying in 5s...")
            time.sleep(5)
            continue
        if not candles:
            break
        # Prepend: new batches are older
        new_count = len([c for c in candles if c[0] not in {x[0] for x in all_candles}])
        all_candles = candles + all_candles
        total = len(set(c[0] for c in all_candles))
        until = candles[0][0]
        elapsed = time.time() - t0
        print(f"    fetched {len(candles)} candles ({new_count} new, {total} total, {elapsed:.0f}s)", end="\r")
        if until <= target_start or new_count == 0:
            break
        time.sleep(DELAY_MS / 1000)

    print()
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(path, index=False)
    print(f"  [saved] {path.name} ({len(df)} rows, {df['timestamp'].min()} → {df['timestamp'].max()})")
    return df


def _fetch_funding_ccxt(symbol_root: str, exchange: str) -> pd.DataFrame | None:
    ex = get_funding_exchange(exchange)
    sym = funding_symbol(symbol_root)
    target_start = ex.parse8601("2022-01-01T00:00:00Z")
    until = ex.milliseconds()
    all_rates: list[list] = []
    t0 = time.time()
    chunk_hours = int(LIMIT * 0.5 * 8)  # funding every 8h, so 500 chunks ~ 4000 hours

    try:
        while True:
            since = max(target_start, int(until - chunk_hours * 3600 * 1000))
            rates = ex.fetch_funding_rate_history(sym, since=since, limit=LIMIT)
            if not rates:
                break
            new_count = len([r for r in rates if r["timestamp"] not in {x[0] for x in all_rates}])
            all_rates = rates + all_rates
            until = rates[0]["timestamp"]
            elapsed = time.time() - t0
            print(f"    fetched {len(rates)} records ({new_count} new, {elapsed:.0f}s)", end="\r")
            if until <= target_start or new_count == 0:
                break
            time.sleep(DELAY_MS / 1000)
    except Exception as e:
        print(f"\n    CCXT funding rate error: {e}")
        return None

    if not all_rates:
        return None

    df = pd.DataFrame(
        [{"timestamp": r["timestamp"], "fundingRate": r["fundingRate"], "symbol": sym} for r in all_rates]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    elapsed = time.time() - t0
    print(f"\n    CCXT: {len(df)} funding records ({elapsed:.0f}s)")
    return df


def _fetch_funding_rest_bybit(symbol_root: str) -> pd.DataFrame | None:
    """Fallback: Bybit public REST endpoint."""
    sym = f"{symbol_root}USDT"
    url = f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&limit=200"
    return _fetch_funding_rest_paginated(
        url, "result", "list", "fundingRate", "fundingTime", symbol_root,
    )


def _fetch_funding_rest_okx(symbol_root: str) -> pd.DataFrame | None:
    """Fallback: OKX public REST endpoint."""
    sym = f"{symbol_root}-USDT-SWAP"
    url = f"https://www.okx.com/api/v5/public/funding-rate-history?instId={sym}&limit=100"
    return _fetch_funding_rest_paginated(
        url, "data", None, "fundingRate", "fundingTime", symbol_root,
    )


def _fetch_funding_rest_paginated(
    base_url: str,
    result_key: str,
    list_key: str | None,
    rate_key: str,
    time_key: str,
    symbol_root: str,
) -> pd.DataFrame | None:
    all_rates: list[dict] = []
    cursor: str | None = None
    t0 = time.time()
    retries = 0

    try:
        while True:
            url = base_url
            params = f"&limit=100"
            if cursor:
                params += f"&cursor={cursor}" if "?" in url else f"?cursor={cursor}"

            # Apply URL with params
            full_url = url
            if params:
                if "?" not in full_url:
                    full_url += params.replace("&", "?", 1)
                else:
                    full_url += params

            req = Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())

            items = data.get(result_key, [])
            if list_key:
                items = items.get(list_key, items) if isinstance(items, dict) else items

            if not items:
                break

            for item in items:
                all_rates.append({
                    "timestamp": int(item[time_key]),
                    "fundingRate": float(item[rate_key]),
                    "symbol": f"{symbol_root}/USDT:USDT",
                })

            # Pagination
            cursor = None
            if isinstance(data.get(result_key), dict):
                cursor = data.get(result_key, {}).get("nextPageCursor")
            if not cursor:
                break
            time.sleep(DELAY_MS / 1000)

    except Exception as e:
        print(f"    REST pagination error: {e}")
        if not all_rates:
            return None

    if not all_rates:
        return None

    df = pd.DataFrame(all_rates)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    elapsed = time.time() - t0
    print(f"    REST: {len(df)} funding records ({elapsed:.0f}s)")
    return df


def _fetch_funding_single(symbol_root: str, exchange: str) -> pd.DataFrame:
    path = funding_path(symbol_root, exchange)
    if path.exists():
        sz = path.stat().st_size
        print(f"  [skip] {symbol_root} funding already cached: {path.name} ({sz/1024:.0f} KB)")
        return pd.read_parquet(path)

    print(f"  [fetch] {symbol_root}/{exchange.upper()} funding rates...")

    # Try CCXT first
    df = _fetch_funding_ccxt(symbol_root, exchange)

    # Fallback to REST
    if df is None:
        print("    CCXT failed, trying REST fallback...")
        if exchange == "bybit":
            df = _fetch_funding_rest_bybit(symbol_root)
        elif exchange == "okx":
            df = _fetch_funding_rest_okx(symbol_root)

    if df is None or len(df) == 0:
        print(f"    ⚠ No funding data for {symbol_root}/{exchange}")
        df = pd.DataFrame(columns=["timestamp", "fundingRate", "symbol"])

    df.to_parquet(path, index=False)
    print(f"  [saved] {path.name} ({len(df)} rows)")
    return df


# ── main ─────────────────────────────────────────────────────────


def main():
    print("=" * 60)
    print("QV2 Phase 3.8 — Cross-Exchange Data Fetcher")
    print("Exchanges: Bybit, OKX")
    print("(Binance data reused from Phase 3.75)")
    print("=" * 60)

    t0 = time.time()
    total_new = 0

    for sym in SYMBOLS:
        print(f"\n{'─' * 40}")
        print(f"{sym}/USDT")
        for ex in EXCHANGES:
            _fetch_ohlcv_single(sym, ex)
            _fetch_funding_single(sym, ex)

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"All data fetched: {elapsed:.0f}s")

    for sym in SYMBOLS:
        for ex in ["binance", *EXCHANGES]:
            for kind in ["usdt_1h", "funding_rates"]:
                if ex == "binance":
                    p = DATA_DIR / f"{sym.lower()}_{kind}.parquet"
                else:
                    p = DATA_DIR / f"{sym.lower()}_{ex}_{kind}.parquet"
                if p.exists():
                    df = pd.read_parquet(p)
                    print(f"  {p.name:45s}  {len(df):>6} rows")

    print(f"\nNext: python3 -m experiments.quant_validation_v2_phase38.run")


if __name__ == "__main__":
    main()
