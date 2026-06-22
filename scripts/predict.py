#!/usr/bin/env python3
"""Inference pipeline for ARGOS production models.

Carga model.pkl + scaler.pkl desde models/{symbol}/, computa 53 features
sobre OHLCV + funding entrantes, y devuelve probabilidades + señal.

Uso:
  python scripts/predict.py --symbol BTC
  python scripts/predict.py --symbol BTC --ohlcv data/btc_usdt_1h.parquet --funding data/btc_funding_rates.parquet

Modo servidor (próximamente):
  python scripts/predict.py --symbol BTC --serve
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("predict")

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "apps" / "analytics-engine" / "data"
MODELS_DIR = PROJECT_ROOT / "models"

from experiments.quant_validation_v2.common import (
    compute_base_ta,
    compute_mtf_features,
    compute_funding_features,
)


def load_model(symbol: str) -> tuple:
    sym_dir = MODELS_DIR / symbol.lower()
    model_path = sym_dir / "model.pkl"
    scaler_path = sym_dir / "scaler.pkl"
    meta_path = sym_dir / "metadata.json"

    for p in [model_path, scaler_path, meta_path]:
        if not p.exists():
            raise FileNotFoundError(f"Model file not found: {p}")

    model = joblib.load(model_path)
    scaler = joblib.load(scaler_path)
    with open(meta_path) as f:
        metadata = json.load(f)

    logger.info(f"Loaded {symbol} model v{metadata['model_version']} "
                f"(trained {metadata['training_date']})")
    logger.info(f"  {metadata['n_features']} features, "
                f"params: C={metadata['parameters']['C']}, "
                f"{metadata['parameters']['solver']}")

    return model, scaler, metadata


def build_X(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> np.ndarray:
    base = compute_base_ta(ohlcv)
    mtf = compute_mtf_features(ohlcv, ("4h", "1d"))
    fund_feat = compute_funding_features(ohlcv, funding)
    combined = pd.concat([base, mtf, fund_feat], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    return combined.values


def predict(
    symbol: str,
    ohlcv: pd.DataFrame | None = None,
    funding: pd.DataFrame | None = None,
) -> dict:
    model, scaler, metadata = load_model(symbol)

    if ohlcv is None:
        ohlcv = load_default_ohlcv(symbol)
    if funding is None:
        funding = load_default_funding(symbol)

    t0 = time.time()
    X = build_X(ohlcv, funding)
    logger.info(f"Features: {X.shape[0]} × {X.shape[1]}  [{time.time() - t0:.2f}s]")

    t0 = time.time()
    X_scaled = scaler.transform(X)
    y_proba = model.predict_proba(X_scaled)[:, 1]
    logger.info(f"Inference: {len(X_scaled)} samples  [{time.time() - t0:.3f}s]")

    buy_threshold = metadata.get("thresholds", {}).get("buy", 0.60)
    sell_threshold = metadata.get("thresholds", {}).get("sell", 0.40)

    signals = np.full(len(y_proba), "HOLD", dtype=object)
    signals[y_proba > buy_threshold] = "BUY"
    signals[y_proba < sell_threshold] = "SELL"

    latest = {
        "symbol": symbol,
        "timestamp": str(ohlcv["timestamp"].iloc[-1]),
        "close": float(ohlcv["close"].iloc[-1]),
        "probability": round(float(y_proba[-1]), 4),
        "signal": str(signals[-1]),
        "model_version": metadata["model_version"],
    }

    n_buy = int((signals == "BUY").sum())
    n_sell = int((signals == "SELL").sum())
    n_hold = int((signals == "HOLD").sum())
    logger.info(f"Signals: {n_buy} BUY / {n_sell} SELL / {n_hold} HOLD")
    logger.info(f"Latest: {latest['signal']} @ {latest['probability']:.4f} "
                f"(close={latest['close']:.2f})")

    return {
        "latest": latest,
        "probabilities": y_proba.tolist(),
        "signals": signals.tolist(),
        "signal_counts": {"BUY": n_buy, "SELL": n_sell, "HOLD": n_hold},
        "metadata": {
            "symbol": symbol,
            "model_version": metadata["model_version"],
            "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        },
    }


def load_default_ohlcv(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_usdt_1h.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    logger.info(f"Loaded {len(df)} OHLCV rows from {path.name}")
    return df


def load_default_funding(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_funding_rates.parquet"
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    logger.info(f"Loaded {len(df)} funding rows from {path.name}")
    return df


def main():
    parser = argparse.ArgumentParser(description="ARGOS inference pipeline")
    parser.add_argument("--symbol", default="BTC", choices=["BTC", "ETH", "SOL"])
    parser.add_argument("--ohlcv", type=str, help="Path to OHLCV parquet (default: auto)")
    parser.add_argument("--funding", type=str, help="Path to funding parquet (default: auto)")
    parser.add_argument("--serve", action="store_true", help="Start inference server (TODO)")
    args = parser.parse_args()

    if args.serve:
        logger.error("Server mode not yet implemented. Use standalone predict.")
        sys.exit(1)

    ohlcv = load_default_ohlcv(args.symbol) if not args.ohlcv else pd.read_parquet(args.ohlcv)
    funding = load_default_funding(args.symbol) if not args.funding else pd.read_parquet(args.funding)

    result = predict(args.symbol, ohlcv, funding)
    print(json.dumps(result["latest"], indent=2))


if __name__ == "__main__":
    main()
