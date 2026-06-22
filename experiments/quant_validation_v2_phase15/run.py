"""Phase 15 — Model Serialization (Fase A del roadmap).

Entrena y serializa modelos LogisticRegression para BTC, ETH, SOL
usando ventana de 3 años (Jun 2023 - Jun 2026).
Pipeline exacto de QV2: 53 features, RobustScaler, thresholds 0.60/0.40.

Output: models/{symbol}/model.pkl + scaler.pkl + metadata.json
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, confusion_matrix

from experiments.quant_validation_v2.common import (
    BASE_FEATURES,
    FUNDING_FEATURES,
    compute_base_ta,
    compute_mtf_features,
    compute_funding_features,
    compute_vol_adj_returns,
    label_binary,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase15")

DATA_DIR = Path(__file__).parent.parent.parent / "apps" / "analytics-engine" / "data"
MODELS_DIR = Path(__file__).parent.parent.parent / "models"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase15"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "binance"
LOOKAHEAD = 5
STRIDE = 5
EMBARGO = 1

MODEL_PARAMS = {
    "C": 0.1,
    "class_weight": "balanced",
    "solver": "liblinear",
    "max_iter": 5000,
    "random_state": 42,
}

TRAIN_START = "2023-06-01"
TRAIN_END = "2026-06-16"
OOS_SPLIT = 0.80  # 80% train, 20% OOS validation


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_usdt_1h.parquet"
    if not path.exists():
        raise FileNotFoundError(f"OHLCV not found: {path}")
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_funding(symbol: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol.lower()}_funding_rates.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Funding not found: {path}")
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def build_X(ohlcv: pd.DataFrame, funding: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    base = compute_base_ta(ohlcv)
    mtf = compute_mtf_features(ohlcv, ("4h", "1d"))
    fund_feat = compute_funding_features(ohlcv, funding)
    feature_names = BASE_FEATURES + list(mtf.columns) + FUNDING_FEATURES
    combined = pd.concat([base, mtf, fund_feat], axis=1)
    combined = combined.bfill().ffill().fillna(0.0).astype(np.float64)
    return combined.values, feature_names


def apply_embargo_and_stride(
    X: np.ndarray, y: np.ndarray, mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    valid_idx = np.where(mask)[0]
    embargoed = valid_idx[valid_idx >= LOOKAHEAD + EMBARGO]
    strided = embargoed[::STRIDE]
    return X[strided], y[strided]


def train_symbol(symbol: str) -> dict:
    start_time = time.time()
    results: dict = {}

    # ── 1. Load data ──────────────────────────────────────────────
    t0 = time.time()
    logger.info("─" * 56)
    logger.info(f"▸ {symbol} — loading data...")
    ohlcv = load_ohlcv(symbol)
    funding = load_funding(symbol)
    ohlcv = ohlcv[(ohlcv["timestamp"] >= TRAIN_START) & (ohlcv["timestamp"] < TRAIN_END)].copy()
    funding = funding[(funding["timestamp"] >= TRAIN_START) & (funding["timestamp"] < TRAIN_END)].copy()
    logger.info(f"  {symbol} OHLCV: {len(ohlcv)} rows ({ohlcv['timestamp'].min()} → {ohlcv['timestamp'].max()})")
    logger.info(f"  {symbol} Funding: {len(funding)} rows  [{time.time() - t0:.1f}s]")
    results["n_rows"] = len(ohlcv)

    # ── 2. Build features ─────────────────────────────────────────
    t0 = time.time()
    logger.info(f"▸ {symbol} — building features (53)...")
    X_raw, feature_names = build_X(ohlcv, funding)
    logger.info(f"  {symbol} features: {X_raw.shape[0]} × {X_raw.shape[1]}  [{time.time() - t0:.1f}s]")
    results["n_features"] = X_raw.shape[1]

    # ── 3. Generate labels ────────────────────────────────────────
    t0 = time.time()
    logger.info(f"▸ {symbol} — generating labels (vol-adj, threshold=0.5)...")
    y_raw, mask = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
    up_pct = (y_raw[mask] == 1).sum() / mask.sum() * 100
    down_pct = (y_raw[mask] == 0).sum() / mask.sum() * 100
    logger.info(f"  {symbol} labels: {up_pct:.1f}% UP, {down_pct:.1f}% DOWN ({mask.sum()} valid)  [{time.time() - t0:.1f}s]")
    results["label_distribution"] = {"up_pct": round(up_pct, 1), "down_pct": round(down_pct, 1), "n_valid": int(mask.sum())}

    # ── 4. Apply embargo + stride ─────────────────────────────────
    X, y = apply_embargo_and_stride(X_raw, y_raw, mask)
    logger.info(f"  {symbol} after embargo+stride: {len(X)} samples")

    # ── 5. Train/val split (80/20 chronological) ──────────────────
    split = int(len(X) * OOS_SPLIT)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]
    logger.info(f"  {symbol} split: {len(X_train)} train / {len(X_val)} val  [{time.time() - t0:.1f}s]")

    # ── 6. Train scaler + model ───────────────────────────────────
    t0 = time.time()
    logger.info(f"▸ {symbol} — training RobustScaler + LogisticRegression...")
    scaler = RobustScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    model = LogisticRegression(**MODEL_PARAMS)
    model.fit(X_train_scaled, y_train)
    logger.info(f"  {symbol} training complete [{time.time() - t0:.2f}s]")
    results["training_time_s"] = round(time.time() - t0, 2)

    # ── 7. In-sample metrics ──────────────────────────────────────
    y_pred = model.predict(X_train_scaled)
    acc = accuracy_score(y_train, y_pred)
    f1 = f1_score(y_train, y_pred, zero_division=0)
    prec = precision_score(y_train, y_pred, zero_division=0)
    rec = recall_score(y_train, y_pred, zero_division=0)
    cm = confusion_matrix(y_train, y_pred).tolist()
    logger.info(f"  {symbol} train metrics:")
    logger.info(f"    Accuracy:  {acc:.4f}")
    logger.info(f"    F1:        {f1:.4f}")
    logger.info(f"    Precision: {prec:.4f}")
    logger.info(f"    Recall:    {rec:.4f}")
    results["train_metrics"] = {
        "accuracy": round(acc, 4), "f1": round(f1, 4),
        "precision": round(prec, 4), "recall": round(rec, 4),
        "confusion_matrix": cm,
    }

    # ── 8. OOS validation ─────────────────────────────────────────
    X_val_scaled = scaler.transform(X_val)
    y_val_pred = model.predict(X_val_scaled)
    val_acc = accuracy_score(y_val, y_val_pred)
    val_f1 = f1_score(y_val, y_val_pred, zero_division=0)
    val_cm = confusion_matrix(y_val, y_val_pred).tolist()
    logger.info(f"  {symbol} OOS metrics (last {len(X_val)} samples):")
    logger.info(f"    Accuracy:  {val_acc:.4f}")
    logger.info(f"    F1:        {val_f1:.4f}")
    results["oos_metrics"] = {
        "accuracy": round(val_acc, 4), "f1": round(val_f1, 4),
        "confusion_matrix": val_cm,
        "n_oos_samples": len(X_val),
    }

    # ── 9. Full retrain (all data) for serialization ──────────────
    t0 = time.time()
    logger.info(f"▸ {symbol} — full retrain on all data for serialization...")
    X_scaled = scaler.fit_transform(X)
    model.fit(X_scaled, y)
    logger.info(f"  {symbol} full retrain done  [{time.time() - t0:.2f}s]")

    # ── 10. Serialize ─────────────────────────────────────────────
    sym_dir = MODELS_DIR / symbol.lower()
    sym_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    logger.info(f"▸ {symbol} — serializing to {sym_dir}/...")
    joblib.dump(model, sym_dir / "model.pkl")
    joblib.dump(scaler, sym_dir / "scaler.pkl")
    model_size = (sym_dir / "model.pkl").stat().st_size
    scaler_size = (sym_dir / "scaler.pkl").stat().st_size

    metadata = {
        "symbol": symbol,
        "exchange": EXCHANGE,
        "model_version": "1.0.0",
        "training_date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "training_data_range": [TRAIN_START, TRAIN_END],
        "n_rows_training": len(ohlcv),
        "n_samples_after_stride": len(X),
        "n_features": X.shape[1],
        "feature_names": feature_names,
        "parameters": MODEL_PARAMS,
        "thresholds": {"buy": 0.60, "sell": 0.40},
        "train_metrics": results["train_metrics"],
        "oos_metrics": results["oos_metrics"],
    }
    with open(sym_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"  ✓ model.pkl     ({model_size / 1024:.1f} KB)")
    logger.info(f"  ✓ scaler.pkl    ({scaler_size / 1024:.1f} KB)")
    logger.info(f"  ✓ metadata.json  [{time.time() - t0:.2f}s]")

    # ── 11. Roundtrip validation ──────────────────────────────────
    t0 = time.time()
    logger.info(f"▸ {symbol} — roundtrip validation...")
    y_before = model.predict(X_scaled)
    model2 = joblib.load(sym_dir / "model.pkl")
    scaler2 = joblib.load(sym_dir / "scaler.pkl")
    X2 = scaler2.transform(X)
    y_after = model2.predict(X2)
    n_match = int((y_after == y_before).sum())
    n_total = len(y_before)
    roundtrip_ok = n_match == n_total
    logger.info(f"  {'✓' if roundtrip_ok else '✗'} Roundtrip: {n_match}/{n_total} predictions match (pre-save vs post-load)  [{time.time() - t0:.2f}s]")
    results["roundtrip_ok"] = roundtrip_ok
    results["roundtrip_match"] = f"{n_match}/{n_total}"

    # ── 12. Coef summary ──────────────────────────────────────────
    top_k = min(10, len(feature_names))
    coefs = model2.coef_[0]
    top_idx = np.argsort(np.abs(coefs))[::-1][:top_k]
    logger.info(f"  Top {top_k} features by |coef|:")
    for rank, idx in enumerate(top_idx, 1):
        logger.info(f"    {rank:2d}. {feature_names[idx]:30s} {coefs[idx]:+.6f}")

    results["duration_s"] = round(time.time() - start_time, 1)
    results["model_path"] = str(sym_dir / "model.pkl")
    return results


def main():
    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║  Phase 15 — Model Serialization (Fase A)                 ║")
    logger.info("║  3-year window: 2023-06-01 → 2026-06-16                  ║")
    logger.info("║  Model: LogisticRegression(C=0.1, balanced, liblinear)    ║")
    logger.info("║  Output: models/{symbol}/model.pkl + scaler.pkl + meta   ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    logger.info("")

    all_results: dict[str, dict] = {}
    for symbol in SYMBOLS:
        try:
            all_results[symbol] = train_symbol(symbol)
        except Exception as e:
            logger.error(f"✗ {symbol} FAILED: {e}", exc_info=True)
            all_results[symbol] = {"error": str(e)}

        logger.info("")

    # ── Summary table ─────────────────────────────────────────────
    logger.info("═" * 56)
    logger.info("  PHASE 15 COMPLETE — SUMMARY")
    logger.info("═" * 56)
    logger.info(f"  {'Symbol':8s} {'Acc':8s} {'F1':8s} {'OOS-Acc':8s} {'Roundtrip':10s} {'Duration':8s}")
    logger.info(f"  {'------':8s} {'---':8s} {'--':8s} {'-------':8s} {'---------':10s} {'--------':8s}")
    for sym in SYMBOLS:
        r = all_results.get(sym, {})
        acc = r.get("train_metrics", {}).get("accuracy", -1)
        f1 = r.get("train_metrics", {}).get("f1", -1)
        oos = r.get("oos_metrics", {}).get("accuracy", -1)
        rt = "✓ PASS" if r.get("roundtrip_ok") else "✗ FAIL" if "roundtrip_ok" in r else "—"
        dur = f"{r.get('duration_s', -1):.1f}s" if r.get("duration_s") else "ERR"
        logger.info(f"  {sym:8s} {acc:8.4f} {f1:8.4f} {oos:8.4f} {rt:10s} {dur:8s}")

    logger.info("")
    for sym in SYMBOLS:
        r = all_results.get(sym, {})
        if r.get("model_path"):
            logger.info(f"  {sym}: {r['model_path']}")

    # ── Save report ───────────────────────────────────────────────
    report = {
        "phase": "quant_validation_v2_phase15",
        "training_window": [TRAIN_START, TRAIN_END],
        "model_params": MODEL_PARAMS,
        "results": all_results,
        "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    report_path = REPORT_DIR / "phase15_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info(f"")
    logger.info(f"  Report saved: {report_path}")

    # ── Next steps ────────────────────────────────────────────────
    logger.info("")
    logger.info("  Next: Fase B — Forward Test")
    logger.info("  Inference stub: python scripts/predict.py --symbol BTC")
    logger.info("═" * 56)


if __name__ == "__main__":
    main()
