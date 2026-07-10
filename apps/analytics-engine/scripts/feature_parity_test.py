#!/usr/bin/env python3
"""P0-P2: Feature parity, distribution drift & ablation test for BTC model.

Levels:
  P0 — Pipeline parity: verify live feature generation is self-consistent
  P1 — Distribution drift: per-feature z-score vs training (from RobustScaler)
  P2 — Feature ablation: zero out feature groups → measure P(HOLD) shift

Usage:
  .venv/bin/python scripts/feature_parity_test.py

Requires:
  - .env with EXCHANGE_API_KEY, EXCHANGE_SECRET (testnet OK)
  - models/production/btc/{model,scaler}.pkl
  - Network access to Binance (ccxt)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_APP = _HERE.parent / "app"
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

import numpy as np
import structlog
from dotenv import load_dotenv

log = structlog.get_logger()

load_dotenv(_HERE.parent / ".env")

MODEL_DIR = Path(os.environ.get(
    "ARGOS_CHECKPOINT_DIR",
    str(_HERE.parent.parent.parent / "models" / "production" / "btc"),
))
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

FEATURE_NAMES = [
    "volume", "rsi", "macd_hist", "adx", "obv",
    "volume_sma", "pct_change", "htf_rsi_4h", "htf_macd_hist_4h", "htf_adx_4h",
    "htf_obv_4h", "htf_volume_sma_4h", "htf_pct_change_4h", "htf_rsi_1d", "htf_macd_hist_1d",
    "htf_adx_1d", "htf_obv_1d", "htf_volume_sma_1d", "htf_pct_change_1d", "htf_atr_1d",
    "close", "ema_fast", "bb_middle", "macd", "atr",
    "htf_macd_4h", "htf_macd_1d", "funding_rate", "funding_momentum", "funding_change",
]

ABLATION_GROUPS: dict[str, list[int]] = {
    "volume_all": [0, 4, 5, 11, 17],
    "obv_all": [4, 10, 16],
    "price_level": [20, 21, 22],
    "funding": [27, 28, 29],
    "volume_plus_funding": [0, 4, 5, 11, 17, 27, 28, 29],
    "all_30": list(range(30)),
}

CLASS_LABELS = ["SELL", "HOLD", "BUY"]


def _load_production_artifacts(model_dir: Path):
    import pickle as _pickle

    model_path = model_dir / "model.pkl"
    scaler_path = model_dir / "scaler.pkl"
    meta_path = model_dir / "metadata.json"

    for p in [model_path, scaler_path, meta_path]:
        if not p.exists():
            log.error("artifact_not_found", path=str(p))
            sys.exit(1)

    model = _pickle.loads(model_path.read_bytes())
    scaler = _pickle.loads(scaler_path.read_bytes())
    metadata = json.loads(meta_path.read_bytes())

    log.info("artifacts_loaded",
             model=type(model).__name__,
             scaler=type(scaler).__name__,
             features=metadata.get("features", "?"),
             version=metadata.get("model_version", "?"))
    return model, scaler, metadata


def _fetch_ohlcv() -> list[dict]:
    import ccxt.async_support as ccxt_async

    async def _fetch():
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        secret = os.environ.get("EXCHANGE_SECRET", "")
        exchange = ccxt_async.binanceusdm({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        })
        try:
            ohlcv = await exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=1000)
            return [
                {"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                for r in ohlcv
            ]
        finally:
            await exchange.close()

    return asyncio.run(_fetch())


def _compute_features(ohlcv: list[dict], metadata: dict) -> np.ndarray:
    from app.infrastructure.training.data_preprocessor import TaDataPreprocessor
    from app.domain.value_objects.model_config import ModelConfig

    cfg = ModelConfig(
        features=tuple(metadata["feature_names"]),
        lookback=metadata.get("parameters", {}).get("lookback", 60),
        target_lookahead=metadata.get("parameters", {}).get("target_lookahead", 3),
    )
    preprocessor = TaDataPreprocessor()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(preprocessor.build_features(ohlcv, cfg))
    finally:
        loop.close()


def _analyze_p0(features_raw, features_norm, metadata) -> dict:
    """P0: pipeline self-consistency check."""
    result = {"pass": True, "checks": {}}

    nan_raw = int(np.isnan(features_raw).sum())
    inf_raw = int(np.isinf(features_raw).sum())
    nan_norm = int(np.isnan(features_norm).sum())
    inf_norm = int(np.isinf(features_norm).sum())

    result["checks"]["nan_inf_raw"] = {"pass": nan_raw == 0 and inf_raw == 0, "nan": nan_raw, "inf": inf_raw}
    result["checks"]["nan_inf_norm"] = {"pass": nan_norm == 0 and inf_norm == 0, "nan": nan_norm, "inf": inf_norm}

    expected = list(metadata["feature_names"])
    result["checks"]["feature_order"] = {"pass": expected == FEATURE_NAMES, "expected": expected, "got": FEATURE_NAMES}

    for c in result["checks"].values():
        if not c.get("pass", True):
            result["pass"] = False
    return result


def _analyze_p1(features_norm, scaler, model) -> dict:
    """P1: per-feature drift summary."""
    last_norm = features_norm[-1]

    severe = []
    moderate = []
    normal = []

    for i in range(30):
        z = last_norm[i]
        name = FEATURE_NAMES[i]
        entry = {
            "idx": i, "name": name, "z_score": round(float(z), 4),
            "norm_val": float(last_norm[i]),
            "coef_sell": float(model.coef_[0, i]),
            "coef_hold": float(model.coef_[1, i]),
            "coef_buy": float(model.coef_[2, i]),
        }
        if abs(z) > 3.0:
            severe.append(entry)
        elif abs(z) > 2.0:
            moderate.append(entry)
        else:
            normal.append(entry)

    severe.sort(key=lambda x: -abs(x["z_score"]))
    moderate.sort(key=lambda x: -abs(x["z_score"]))

    return {"severe": severe, "moderate": moderate, "normal": normal}


def _analyze_p2(model, features_norm) -> dict:
    """P2: ablation experiments — return probability changes per group."""
    baseline_proba = model.predict_proba(features_norm[-1:])[0]
    baseline_decision = int(model.predict(features_norm[-1:])[0])

    results = {}
    for label, indices in ABLATION_GROUPS.items():
        feat = features_norm.copy()
        feat[-1, indices] = 0.0
        proba = model.predict_proba(feat[-1:])[0]
        decision = int(model.predict(feat[-1:])[0])
        results[label] = {
            "zeroed_indices": indices,
            "proba": [float(round(p, 4)) for p in proba],
            "decision": decision,
            "p_hold_diff": round(proba[1] - baseline_proba[1], 4),
        }

    single_features = [20, 21, 22, 4, 0]
    for idx in single_features:
        label = f"single:{FEATURE_NAMES[idx]}"
        feat = features_norm.copy()
        feat[-1, idx] = 0.0
        proba = model.predict_proba(feat[-1:])[0]
        decision = int(model.predict(feat[-1:])[0])
        results[label] = {
            "zeroed_indices": [idx],
            "proba": [float(round(p, 4)) for p in proba],
            "decision": decision,
            "p_hold_diff": round(proba[1] - baseline_proba[1], 4),
        }

    return {
        "baseline_proba": [float(round(p, 4)) for p in baseline_proba],
        "baseline_decision": baseline_decision,
        "ablations": results,
    }


def main():
    model, scaler, metadata = _load_production_artifacts(MODEL_DIR)

    print(f"\n{'─'*80}")
    print(f"  P0: FETCHING LIVE OHLCV FROM BINANCE {SYMBOL} {TIMEFRAME}")
    print(f"{'─'*80}")
    ohlcv = _fetch_ohlcv()
    print(f"  Candles: {len(ohlcv)}   Close: {ohlcv[-1]['close']:.2f}")
    from app.domain.entities.multi_timeframe_aligner import MultiTimeframeAligner
    mtf_check = MultiTimeframeAligner.compute(ohlcv)
    print(f"  MTF computed: {mtf_check.shape[1]} features across {mtf_check.shape[0]} rows")

    features_raw = _compute_features(ohlcv, metadata)
    features_norm = scaler.transform(features_raw)

    p0 = _analyze_p0(features_raw, features_norm, metadata)
    print(f"\n  P0 PARITY CHECKS:")
    for name, c in p0["checks"].items():
        print(f"    {name:<25}  {'✅' if c['pass'] else '❌'}  (extra: { {k:v for k,v in c.items() if k != 'pass'} })")

    proba_base = model.predict_proba(features_norm[-1:])[0]
    dec_base = int(model.predict(features_norm[-1:])[0])
    print(f"\n  BASELINE: SELL={proba_base[0]:.4f}  HOLD={proba_base[1]:.4f}  BUY={proba_base[2]:.4f}  → {CLASS_LABELS[dec_base]}")

    p1 = _analyze_p1(features_norm, scaler, model)
    print(f"\n  P1: SEVERE DRIFT (|z| > 3): {len(p1['severe'])} features")
    for e in p1["severe"]:
        print(f"    {e['name']:<25}  z={e['z_score']:>8.2f}  (norm={e['norm_val']:>8.4f})  coef_HOLD={e['coef_hold']:>8.4f}")
    print(f"      MODERATE (2 < |z| <= 3): {len(p1['moderate'])}")
    for e in p1["moderate"]:
        print(f"    {e['name']:<25}  z={e['z_score']:>8.2f}  coef_HOLD={e['coef_hold']:>8.4f}")
    print(f"      NORMAL (|z| <= 2): {len(p1['normal'])} features")

    print(f"\n  FULL FEATURE TABLE:")
    hdr = f"{'#':>3} {'Name':<25} {'Raw':>12} {'Norm':>8} {'z-score':>8}  {'cSELL':>8} {'cHOLD':>8} {'cBUY':>8}"
    print(hdr)
    print("  " + "-" * len(hdr))
    last_raw = features_raw[-1]
    last_norm = features_norm[-1]
    for i in range(30):
        z = last_norm[i]
        print(f"  {i:>3} {FEATURE_NAMES[i]:<25} {last_raw[i]:>12.2f} {last_norm[i]:>8.4f} {z:>8.2f}  "
              f"{model.coef_[0,i]:>8.4f} {model.coef_[1,i]:>8.4f} {model.coef_[2,i]:>8.4f}")

    p2 = _analyze_p2(model, features_norm)
    print(f"\n  P2: ABLATION EXPERIMENTS")
    print(f"  Baseline: SELL={p2['baseline_proba'][0]:.4f}  HOLD={p2['baseline_proba'][1]:.4f}  BUY={p2['baseline_proba'][2]:.4f}  → {CLASS_LABELS[p2['baseline_decision']]}")
    print()
    hdr2 = f"  {'Ablation':<30} {'P(SELL)':>8} {'P(HOLD)':>8} {'P(BUY)':>8}  {'ΔHOLD':>8}  {'→':<10}"
    print(hdr2)
    print("  " + "-" * len(hdr2))
    for label, ab in p2["ablations"].items():
        p = ab["proba"]
        print(f"  {label:<30} {p[0]:>8.4f} {p[1]:>8.4f} {p[2]:>8.4f}  {ab['p_hold_diff']:>+8.4f}  {CLASS_LABELS[ab['decision']]:<10}")

    print(f"\n  TOP FEATURES BY COEFFICIENT MAGNITUDE (HOLD class):")
    hold_coefs = [(FEATURE_NAMES[i], model.coef_[1, i], model.coef_[0, i], model.coef_[2, i]) for i in range(30)]
    hold_coefs.sort(key=lambda x: -abs(x[1]))
    for name, ch, cs, cb in hold_coefs[:10]:
        print(f"    {name:<25}  HOLD={ch:>8.4f}  SELL={cs:>8.4f}  BUY={cb:>8.4f}")

    print(f"\n{'='*70}")
    print(f"  P0 PASS: {p0['pass']}")
    print(f"  P1 severe: {len(p1['severe'])}  |  moderate: {len(p1['moderate'])}")
    print(f"  P2: see ablation table above")
    print(f"{'='*70}")

    # ── Save results to JSON ──
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = _HERE.parent / "reports" / f"feature_parity_{ts}.json"
    out.write_text(json.dumps({
        "timestamp": ts, "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "last_close": ohlcv[-1]["close"], "n_candles": len(ohlcv),
        "p0": p0, "p1": p1, "p2": p2,
    }, indent=2))
    print(f"\n  Results saved to: {out}")


if __name__ == "__main__":
    main()
