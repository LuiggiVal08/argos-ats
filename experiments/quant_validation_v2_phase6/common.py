"""Phase 6 — Probability Calibration.

Metrics: Brier score, Expected Calibration Error (ECE), reliability curves,
mean confidence vs accuracy, max overconfidence, Platt scaling.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from experiments.quant_validation_v2_phase5.common import (
    load_predictions_parquet as _load_p5,
    logger as _phase5_logger,
)

logger = logging.getLogger("phase6")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PHASE5_REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase5"
REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase6"
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_predictions_parquet(filename: str) -> pd.DataFrame | None:
    path = PHASE5_REPORT_DIR / filename
    if not path.exists():
        logger.warning(f"[parquet] not found: {path}")
        return None
    df = pd.read_parquet(path)
    logger.info(f"[parquet] loaded {len(df)} predictions from {path}")
    return df


def save_report(data: dict, filename: str) -> Path:
    path = REPORT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info(f"[report] saved {path}")
    return path

SYMBOLS = ["BTC", "ETH", "SOL"]
N_BINS = 10


# ── Brier score ────────────────────────────────────────────────────


def brier_score(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    return float(np.mean((y_proba - y_true) ** 2))


# ── Expected Calibration Error (ECE) ──────────────────────────────


def expected_calibration_error(
    y_true: np.ndarray, y_proba: np.ndarray, n_bins: int = N_BINS,
) -> tuple[float, list[dict]]:
    """Compute ECE and return per-bin details for reliability diagram."""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.digitize(y_proba, bins, right=False) - 1
    bin_ids = np.clip(bin_ids, 0, n_bins - 1)

    ece = 0.0
    bin_details = []
    for i in range(n_bins):
        mask = bin_ids == i
        n_in_bin = int(mask.sum())
        if n_in_bin == 0:
            bin_details.append({
                "bin": i,
                "bin_lower": float(bins[i]),
                "bin_upper": float(bins[i + 1]),
                "n": 0,
                "conf": 0.0,
                "acc": 0.0,
                "gap": 0.0,
            })
            continue
        conf = float(y_proba[mask].mean())
        acc = float(y_true[mask].mean())
        frac = n_in_bin / len(y_true)
        gap = abs(conf - acc)
        ece += frac * gap
        bin_details.append({
            "bin": i,
            "bin_lower": float(bins[i]),
            "bin_upper": float(bins[i + 1]),
            "n": n_in_bin,
            "conf": round(conf, 4),
            "acc": round(acc, 4),
            "gap": round(gap, 4),
        })

    return round(ece, 6), bin_details


# ── Over / under confidence ───────────────────────────────────────


def overconfidence_metrics(
    y_true: np.ndarray, y_proba: np.ndarray,
) -> dict:
    mean_conf = float(y_proba.mean())
    mean_acc = float(y_true.mean())
    # Overconfidence: avg(proba - accuracy) for correct predictions only (arguable)
    # Standard: avg(proba) - avg(accuracy)
    overconf = mean_conf - mean_acc

    # Max overconfidence: max(proba_i - indicator(y_i=1)) per sample
    indicators = y_true.astype(float)
    per_sample_overconf = y_proba - indicators
    max_overconf = float(per_sample_overconf.max())
    mean_overconf = float(per_sample_overconf.mean())

    return {
        "mean_confidence": round(mean_conf, 4),
        "mean_accuracy": round(mean_acc, 4),
        "overconfidence": round(overconf, 4),
        "max_overconfidence": round(max_overconf, 4),
        "mean_overconfidence": round(mean_overconf, 4),
    }


# ── Platt scaling ─────────────────────────────────────────────────


def platt_scale(
    y_true: np.ndarray, y_proba: np.ndarray,
) -> dict:
    """Apply Platt scaling (logistic regression on logit of probabilities).

    Uses 5-fold cross-fitting on the calibration dataset. Returns metrics
    before and after scaling.
    """
    from sklearn.model_selection import StratifiedKFold

    orig_brier = brier_score(y_true, y_proba)
    orig_ece, _ = expected_calibration_error(y_true, y_proba)

    # Clip to avoid log(0)
    eps = 1e-12
    proba_clipped = np.clip(y_proba, eps, 1 - eps)
    logit = np.log(proba_clipped / (1 - proba_clipped)).reshape(-1, 1)

    # Cross-validated Platt scaling
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    calibrated_probas = np.zeros_like(y_proba)

    for train_idx, val_idx in skf.split(logit, y_true):
        if len(np.unique(y_true[train_idx])) < 2:
            calibrated_probas[val_idx] = y_proba[val_idx]
            continue
        platt = LogisticRegression(max_iter=1000)
        platt.fit(logit[train_idx], y_true[train_idx])
        calibrated_probas[val_idx] = platt.predict_proba(logit[val_idx])[:, 1]

    platt_brier = brier_score(y_true, calibrated_probas)
    platt_ece, platt_bins = expected_calibration_error(y_true, calibrated_probas)

    return {
        "original_brier": round(orig_brier, 6),
        "original_ece": round(orig_ece, 6),
        "platt_brier": round(platt_brier, 6),
        "platt_ece": round(platt_ece, 6),
        "brier_improvement_pct": round((orig_brier - platt_brier) / orig_brier * 100, 2),
        "ece_improvement_pct": round((orig_ece - platt_ece) / orig_ece * 100, 2) if orig_ece > 0 else 0.0,
        "platt_calibrated_bins": platt_bins,
    }


# ── main analysis ─────────────────────────────────────────────────


def analyze_symbol(symbol: str) -> dict | None:
    df = load_predictions_parquet(f"{symbol.lower()}_predictions.parquet")
    if df is None or len(df) < 100:
        logger.warning(f"[{symbol}] insufficient predictions for calibration")
        return None

    y_true = df["y_true"].values
    y_proba = df["y_proba"].values

    brier = brier_score(y_true, y_proba)
    ece, bins = expected_calibration_error(y_true, y_proba)
    overconf = overconfidence_metrics(y_true, y_proba)

    # Platt scaling
    platt = platt_scale(y_true, y_proba)

    # Verdict
    verdicts = {}
    if ece < 0.05:
        verdicts["calibration"] = "CALIBRATED"
    elif ece < 0.10:
        verdicts["calibration"] = "MILD_MISCALIBRATION"
    else:
        verdicts["calibration"] = "SEVERE_MISCALIBRATION"

    if overconf["overconfidence"] > 0.05:
        verdicts["bias"] = "OVERCONFIDENT"
    elif overconf["overconfidence"] < -0.05:
        verdicts["bias"] = "UNDERCONFIDENT"
    else:
        verdicts["bias"] = "WELL_CALIBRATED"

    if platt["ece_improvement_pct"] > 20:
        verdicts["fixable_by_platt"] = True
    else:
        verdicts["fixable_by_platt"] = False

    result = {
        "symbol": symbol,
        "n_predictions": len(df),
        "class_balance": {
            "buy_pct": round(float((y_true == 1).mean()), 4),
            "sell_pct": round(float((y_true == 0).mean()), 4),
        },
        "brier_score": round(brier, 6),
        "ece": round(ece, 6),
        "ece_bins": bins,
        "overconfidence": overconf,
        "platt_scaling": platt,
        "verdicts": verdicts,
    }

    logger.info(
        f"[{symbol}] Brier={brier:.4f}  ECE={ece:.4f}  "
        f"Conf={overconf['mean_confidence']:.3f}  Acc={overconf['mean_accuracy']:.3f}  "
        f"Overconf={overconf['overconfidence']:.3f}  "
        f"PlattΔBrier={platt['brier_improvement_pct']:+.1f}%  "
        f"Verdict={verdicts['calibration']}/{verdicts['bias']}"
    )
    return result


def main():
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  Phase 6 — Probability Calibration    ║")
    logger.info("╚══════════════════════════════════════╝")

    sym_results = {}
    for sym in SYMBOLS:
        r = analyze_symbol(sym)
        sym_results[sym] = r or {"status": "error"}

    # Pooled metrics (all predictions combined)
    all_y_true = []
    all_y_proba = []
    for sym in SYMBOLS:
        df = load_predictions_parquet(f"{sym.lower()}_predictions.parquet")
        if df is not None and len(df) > 0:
            all_y_true.append(df["y_true"].values)
            all_y_proba.append(df["y_proba"].values)

    if all_y_true:
        y_true_pooled = np.concatenate(all_y_true)
        y_proba_pooled = np.concatenate(all_y_proba)
        pooled_brier = brier_score(y_true_pooled, y_proba_pooled)
        pooled_ece, pooled_bins = expected_calibration_error(y_true_pooled, y_proba_pooled)
        pooled_overconf = overconfidence_metrics(y_true_pooled, y_proba_pooled)
        pooled_platt = platt_scale(y_true_pooled, y_proba_pooled)

        pooled = {
            "symbol": "POOLED",
            "n_predictions": len(y_true_pooled),
            "brier_score": round(pooled_brier, 6),
            "ece": round(pooled_ece, 6),
            "ece_bins": pooled_bins,
            "overconfidence": pooled_overconf,
            "platt_scaling": pooled_platt,
        }
        logger.info(
            f"[POOLED] Brier={pooled_brier:.4f}  ECE={pooled_ece:.4f}  "
            f"PlattΔBrier={pooled_platt['brier_improvement_pct']:+.1f}%"
        )
    else:
        pooled = {"status": "no_data"}

    output = {
        "phase": "quant_validation_v2_phase6",
        "per_symbol": sym_results,
        "pooled": pooled,
    }
    save_report(output, "calibration.json")
    logger.info(f"\nPhase 6 complete — calibration.json saved")


if __name__ == "__main__":
    main()
