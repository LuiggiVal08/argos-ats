"""Export training data for Colab training.

Usage:
    python -m scripts.export_training_data \
        --symbols BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,AVAX/USDT \
        --timeframe 5m --years 4 --output data_export.zip

    # Resume after a crash (skips already-processed symbols):
    python -m scripts.export_training_data \
        --symbols BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,AVAX/USDT \
        --timeframe 5m --years 4 --output data_export.zip --resume

Fetches OHLCV in chunks of 1000 candles with rate limiting,
preprocesses features + targets via TaDataPreprocessor,
and exports a ZIP with Parquet files per symbol + manifest.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import pickle
import zipfile
from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import ta
from dataclasses import replace

from app.infrastructure.ohlcv.ccxt_ohlcv_source import ccxt_ohlcv_source
from app.infrastructure.training.data_preprocessor import TaDataPreprocessor
from app.domain.value_objects.model_config import ModelConfig

CHUNK_SIZE = 1000
RATE_LIMIT_DELAY = 0.25
MINUTES_PER_YEAR = 365 * 24 * 60
CACHE_DIR = ".export_cache"


def _parse_args() -> Namespace:
    p = ArgumentParser(description="Export training data for Colab training")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols")
    p.add_argument("--timeframe", default="5m", help="Candle timeframe")
    p.add_argument("--years", type=int, default=4, help="Years of history")
    p.add_argument("--output", default="data_export.zip", help="Output ZIP path")
    p.add_argument("--exchange-id", default="binance", help="CCXT exchange id")
    p.add_argument("--lookback", type=int, default=None, help="Override lookback (default: ModelConfig.lookback)")
    p.add_argument("--target-lookahead", type=int, default=None, help="Override target_lookahead (default: ModelConfig.target_lookahead)")
    p.add_argument("--label-method", default="fixed", choices=["fixed", "atr", "triple_barrier"],
                    help="Labeling method (default: fixed)")
    p.add_argument("--atr-multiplier", type=float, default=1.5,
                    help="ATR multiplier for atr/triple_barrier methods")
    p.add_argument("--resume", action="store_true", help="Skip already-processed symbols")
    return p.parse_args()


def _symbol_to_key(symbol: str) -> str:
    return symbol.replace("/", "_")


def _make_exchange(exchange_id: str):
    import ccxt.async_support as ccxt

    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class({
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })


def _since_from_years(years: int) -> int:
    now = datetime.now(timezone.utc)
    start = now.replace(year=now.year - years)
    return int(start.timestamp() * 1000)


def _cache_path(key: str) -> Path:
    return Path(CACHE_DIR) / f"{key}.pkl"


def _is_cached(key: str) -> bool:
    return _cache_path(key).is_file()


def _save_cache(key: str, data: dict) -> None:
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    with open(_cache_path(key), "wb") as f:
        pickle.dump(data, f)


def _load_cache(key: str) -> dict:
    with open(_cache_path(key), "rb") as f:
        return pickle.load(f)


async def _fetch_chunked(
    exchange, symbol: str, timeframe: str, years: int,
) -> list[dict]:
    since = _since_from_years(years)
    all_candles: list[dict] = []
    chunk_num = 0

    while True:
        raw = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, limit=CHUNK_SIZE, since=since,
        )
        if not raw:
            break

        candles = [
            {
                "timestamp": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }
            for c in raw
        ]

        all_candles.extend(candles)
        chunk_num += 1
        print(f"  [{_symbol_to_key(symbol)}] chunk {chunk_num}: "
              f"{len(candles)} candles, total {len(all_candles)} "
              f"(-> {pd.Timestamp(raw[-1][0], unit='ms')})")

        if len(raw) < CHUNK_SIZE:
            break

        since = raw[-1][0] + 1
        await asyncio.sleep(RATE_LIMIT_DELAY)

    return all_candles


async def _process_symbol(
    exchange, symbol: str, timeframe: str, years: int,
    preprocessor: TaDataPreprocessor, cfg: ModelConfig,
    label_method: str = "fixed", atr_multiplier: float = 1.5,
) -> dict | None:
    key = _symbol_to_key(symbol)
    print(f"\nFetching {symbol} ({timeframe}, {years}y)...")
    ohlcv = await _fetch_chunked(exchange, symbol, timeframe, years)

    if len(ohlcv) < cfg.lookback + 10:
        print(f"  WARNING: {symbol} only got {len(ohlcv)} candles, skipping")
        return None

    print(f"  Preprocessing features...")
    features = await preprocessor.build_features(ohlcv, cfg)

    if label_method in ("atr", "triple_barrier"):
        df = pd.DataFrame(ohlcv)
        atr_ind = ta.volatility.AverageTrueRange(
            high=df["high"].astype(np.float64),
            low=df["low"].astype(np.float64),
            close=df["close"].astype(np.float64),
            window=14,
        )
        atr_values = atr_ind.average_true_range().values

    if label_method == "triple_barrier":
        max_holding = cfg.target_lookahead
        targets = await preprocessor.create_targets_triple_barrier(
            ohlcv, atr_values, atr_multiplier=atr_multiplier, max_holding=max_holding,
        )
    elif label_method == "atr":
        targets = await preprocessor.create_targets(ohlcv, cfg, atr_values=atr_values)
    else:
        targets = await preprocessor.create_targets(ohlcv, cfg)

    min_len = min(len(features), len(targets))
    features_raw = features[-min_len:]
    targets = targets[-min_len:]

    print(f"  -> {len(features_raw)} samples, {features_raw.shape[1]} features, "
          f"BUY={float((targets[:,0].sum()/len(targets))*100):.1f}% "
          f"SELL={float((targets[:,1].sum()/len(targets))*100):.1f}% "
          f"HOLD={float((targets[:,2].sum()/len(targets))*100):.1f}%")
    return {
        "symbol": symbol,
        "features": features_raw,
        "targets": targets,
        "feature_names": list(cfg.features),
        "n_samples": len(features_raw),
        "n_features": features_raw.shape[1],
        "label_method": label_method,
        "atr_multiplier": atr_multiplier,
    }


def _write_zip(
    results: list[dict], output: str, cfg: ModelConfig, timeframe: str,
) -> None:
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": timeframe,
        "config": {
            "lookback": cfg.lookback,
            "confidence_threshold": cfg.confidence_threshold,
            "layers": list(cfg.layers),
            "features": list(cfg.features),
            "target_lookahead": cfg.target_lookahead,
            "target_return_pct": cfg.target_return_pct,
            "dropout_rate": cfg.dropout_rate,
            "batch_size": cfg.batch_size,
            "max_epochs": cfg.max_epochs,
            "early_stop_patience": cfg.early_stop_patience,
            "label_method": results[0].get("label_method", "fixed"),
            "atr_multiplier": results[0].get("atr_multiplier", 1.5),
        },
        "symbols": [],
    }

    os.makedirs("features", exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in results:
            key = _symbol_to_key(r["symbol"])
            feat_df = pd.DataFrame(r["features"], columns=r["feature_names"])
            feat_path = f"features/{key}_features.parquet"
            feat_df.to_parquet(feat_path, index=False)
            zf.write(feat_path, feat_path)

            target_df = pd.DataFrame(
                r["targets"], columns=["buy_prob", "sell_prob", "hold_prob"]
            )
            tgt_path = f"features/{key}_targets.parquet"
            target_df.to_parquet(tgt_path, index=False)
            zf.write(tgt_path, tgt_path)

            os.remove(feat_path)
            os.remove(tgt_path)

            manifest["symbols"].append({
                "symbol": r["symbol"],
                "key": key,
                "n_samples": r["n_samples"],
                "n_features": r["n_features"],
            })

        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    print(f"\nExported {len(results)} symbols to {output}")


def _clean_output_path(output: str) -> None:
    out = Path(output)
    if out.exists():
        out.unlink()
        print(f"Removed existing {output}")


async def main() -> None:
    args = _parse_args()
    symbols = [s.strip() for s in args.symbols.split(",")]
    cfg = ModelConfig()
    if args.lookback is not None:
        cfg = replace(cfg, lookback=args.lookback)
    if args.target_lookahead is not None:
        cfg = replace(cfg, target_lookahead=args.target_lookahead)
    preprocessor = TaDataPreprocessor()

    exchange = _make_exchange(args.exchange_id)
    await exchange.load_markets()
    print(f"Connected to {args.exchange_id}, {len(exchange.markets)} markets loaded")

    results = []

    try:
        for sym in symbols:
            key = _symbol_to_key(sym)

            if args.resume and _is_cached(key):
                print(f"\nLoading cached result for {sym}...")
                results.append(_load_cache(key))
                print(f"  -> {results[-1]['n_samples']} samples cached")
                continue

            r = await _process_symbol(
                exchange, sym, args.timeframe, args.years, preprocessor, cfg,
                label_method=args.label_method, atr_multiplier=args.atr_multiplier,
            )
            if r:
                _save_cache(key, r)
                results.append(r)

        if results:
            _clean_output_path(args.output)
            _write_zip(results, args.output, cfg, args.timeframe)
        else:
            print("No data exported (all symbols failed)")

        # Clean cache after successful export
        if Path(CACHE_DIR).exists():
            import shutil
            shutil.rmtree(CACHE_DIR)
            print("Cache cleaned")

    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
