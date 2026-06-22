"""Phase 5 — Portfolio Validation: run predictions for BTC, ETH, SOL.

Mismo protocolo que Phase 3.5: lookahead=5, stride=5, embargo=1, 53 features, LR champion.
Además de métricas agregadas, guarda predicciones completas en parquet.
"""

from __future__ import annotations

import logging
import time
import traceback
from pathlib import Path

import numpy as np

from experiments.quant_validation_v2_phase35.common import (
    subsample_indices,
    walk_forward_splits_phase35,
    label_binary,
)

from experiments.quant_validation_v2_phase38.common import (
    build_feature_matrix,
)

from experiments.quant_validation_v2_phase5.common import (
    make_lr,
    run_classification_with_probas,
    save_predictions_parquet,
    save_report,
    heartbeat_if_due,
    logger,
    REPORT_DIR,
    DATA_DIR,
)

LOOKAHEAD = 5
STRIDE = 5
EMBARGO = 1
N_FOLDS = 10
SYMBOLS = ["BTC", "ETH", "SOL"]
EXCHANGE = "binance"
EXCHANGE_LABEL = "binance"


def run_symbol(symbol: str) -> bool:
    label = f"{symbol}_{EXCHANGE_LABEL}"
    logger.info(f"{'='*60}")
    logger.info(f"[Phase5] Starting {label}")
    start_time = time.time()
    last_heartbeat = start_time

    try:
        # Step 1: load & build features
        t0 = time.time()
        logger.info(f"[{label}] building feature matrix...")
        (X, y_class, feature_names, _), ohlcv = build_feature_matrix(symbol, EXCHANGE)
        logger.info(f"[{label}] X shape={X.shape}, OHLCV rows={len(ohlcv)}")
        last_heartbeat = heartbeat_if_due(last_heartbeat, f"{label}_build", start_time)

        # Step 2: subsample
        idx_sub = subsample_indices(len(X), LOOKAHEAD, STRIDE)
        X_sub = X[idx_sub]
        logger.info(f"[{label}] subsampled {len(idx_sub)} rows (stride={STRIDE})")
        last_heartbeat = heartbeat_if_due(last_heartbeat, f"{label}_subsample", start_time)

        # Step 3: binary labels
        y_bin, mask = label_binary(ohlcv, lookahead=LOOKAHEAD, threshold=0.5)
        y_bin_sub = y_bin[idx_sub]
        mask_sub = mask[idx_sub]
        n_hold = int((~mask_sub).sum())
        n_signals = int(mask_sub.sum())
        logger.info(f"[{label}] binary labels: {n_signals} signals, {n_hold} HOLD ({n_hold/(n_hold+n_signals)*100:.1f}%)")
        last_heartbeat = heartbeat_if_due(last_heartbeat, f"{label}_labels", start_time)

        # Step 4: filter non-HOLD
        X_bin = X_sub[mask_sub]
        y_bin = y_bin_sub[mask_sub]
        idx_bin = idx_sub[mask_sub]
        logger.info(f"[{label}] X_bin shape={X_bin.shape}")

        if len(X_bin) < 100:
            logger.warning(f"[{label}] too few non-HOLD samples ({len(X_bin)}), skipping")
            return False

        # Step 5: walk-forward splits
        splits = walk_forward_splits_phase35(len(X_bin), N_FOLDS, EMBARGO)
        logger.info(f"[{label}] {len(splits)} walk-forward folds")
        last_heartbeat = heartbeat_if_due(last_heartbeat, f"{label}_splits", start_time)

        # Step 6: run classification with probas
        logger.info(f"[{label}] running LR classification (champion)...")
        t_run = time.time()
        result, predictions = run_classification_with_probas(
            X_bin, y_bin, idx_bin, ohlcv, splits, make_lr,
        )
        run_elapsed = time.time() - t_run
        logger.info(f"[{label}] classification done in {run_elapsed:.0f}s")
        last_heartbeat = heartbeat_if_due(last_heartbeat, f"{label}_run", start_time)

        # Step 8: save predictions parquet
        parquet_file = f"{symbol.lower()}_predictions.parquet"
        save_predictions_parquet(predictions, parquet_file)

        # Step 9: build metrics dict
        metrics = {
            "symbol": symbol,
            "exchange": EXCHANGE,
            "protocol": "phase35",
            "lookahead": LOOKAHEAD,
            "stride": STRIDE,
            "embargo": EMBARGO,
            "n_folds": N_FOLDS,
            "n_features": X_bin.shape[1],
            "n_samples": len(X_bin),
            "n_subsampled": len(idx_sub),
            "n_signals": n_signals,
            "n_hold": n_hold,
            "test_n_total": sum(fp.test_end - fp.test_start for fp in predictions),
            "avg_accuracy": result.avg_accuracy,
            "avg_kappa": result.avg_kappa,
            "avg_mcc": result.avg_mcc,
            "avg_f1": result.avg_f1,
            "avg_auc": result.avg_auc,
            "model": result.model_name,
            "folds": result.folds,
            "run_elapsed_s": round(run_elapsed, 1),
            "total_elapsed_s": round(time.time() - start_time, 1),
        }

        # Step 10: save report JSON
        json_file = f"{symbol.lower()}.json"
        save_report(metrics, json_file)

        total_elapsed = time.time() - start_time
        logger.info(f"[{label}] ✓ COMPLETE in {total_elapsed:.0f}s")
        return True

    except Exception as e:
        logger.error(f"[{label}] ✗ FAILED: {e}")
        logger.error(traceback.format_exc())
        return False


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 5 — Portfolio Validation       ║")
    logger.info("║  BTC → ETH → SOL, LR champion         ║")
    logger.info("╚══════════════════════════════════════╝")
    logger.info(f"Symbols: {SYMBOLS}")
    logger.info(f"Protocol: lookahead={LOOKAHEAD}, stride={STRIDE}, embargo={EMBARGO}, folds={N_FOLDS}")
    logger.info(f"Report dir: {REPORT_DIR}")
    logger.info(f"Data dir: {DATA_DIR}")

    t_global = time.time()
    results = {}
    for sym in SYMBOLS:
        elapsed = time.time() - t_global
        logger.info(f"\n[{sym}] queueing (elapsed so far: {elapsed:.0f}s)...")
        ok = run_symbol(sym)
        results[sym] = "OK" if ok else "FAIL"

    total = time.time() - t_global
    logger.info(f"\n{'='*60}")
    logger.info(f"Phase 5 COMPLETE — total time: {total:.0f}s")
    for sym, status in results.items():
        logger.info(f"  {sym}: {status}")
    logger.info(f"{'='*60}")


if __name__ == "__main__":
    main()
