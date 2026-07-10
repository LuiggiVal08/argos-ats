"""QV2 Phase 4 — Cross-Regime Validation Summary.

Loads individual experiment JSONs from the reports directory,
evaluates gates, computes stability ratios, and produces a verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .common import REPORT_DIR, load_report, logger

# Experiments by group
TREND_EXPERIMENTS = ["bull", "bear", "sideways"]
VOL_EXPERIMENTS = ["high_vol", "medium_vol", "low_vol"]
ALL_EXPERIMENTS = ["full"] + TREND_EXPERIMENTS + VOL_EXPERIMENTS

# Gate thresholds (same as Phase 3.8)
GATE_F1 = 0.60
GATE_AUC = 0.70
GATE_R2 = 0.05
GATE_SHUFFLE_DELTA = 0.01


def load_results() -> dict[str, dict | None]:
    results = {}
    for exp in ALL_EXPERIMENTS:
        data = load_report(f"{exp}.json")
        if data is None:
            logger.warning(f"Missing report: {exp}.json")
        results[exp] = data
    return results


def extract_metrics(result: dict) -> dict:
    """Extract consolidated metrics from a single experiment result."""
    p3b = result.get("phases", {}).get("3B_binary", {})
    p3c = result.get("phases", {}).get("3C_regression", {})

    metrics = {
        "n_samples": result.get("n_samples_subsampled", 0),
        "n_binary": p3b.get("n_samples_nonhold", 0),
        "n_regression": p3c.get("n_samples", 0),
        "binary_f1": p3b.get("best_f1", 0),
        "auc": p3b.get("best_auc", 0) or 0,
        "regression_r2": p3c.get("best_r2", 0),
        "shuffle_delta_bin": p3b.get("best_shuffle_delta", 0),
        "shuffle_delta_reg": p3c.get("best_shuffle_delta", 0),
        "best_model_bin": p3b.get("best_model", "?"),
        "best_model_reg": p3c.get("best_model", "?"),
    }

    # Extract per-fold metrics for stability computation
    models_3b = p3b.get("models", {})
    models_3c = p3c.get("models", {})

    # Use best model's per-fold F1 (binary)
    best_model_bin = p3b.get("best_model", "")
    best_model_reg = p3c.get("best_model", "")
    bm = models_3b.get(best_model_bin, {})
    rm = models_3c.get(best_model_reg, {})

    fold_records_bin = bm.get("folds", [])
    fold_records_reg = rm.get("folds", [])

    f1_arr = np.array([f.get("f1_macro", 0) for f in fold_records_bin], dtype=float)
    auc_arr = np.array([f.get("auc", 0) or 0 for f in fold_records_bin], dtype=float)
    r2_arr = np.array([f.get("r2", 0) or 0 for f in fold_records_reg], dtype=float)

    if len(f1_arr) > 0 and f1_arr.max() > 0:
        metrics["stability_f1"] = round(float(f1_arr.min() / f1_arr.max()), 4)
    else:
        metrics["stability_f1"] = 0.0

    if len(auc_arr) > 0 and auc_arr.max() > 0:
        metrics["stability_auc"] = round(float(auc_arr.min() / auc_arr.max()), 4)
    else:
        metrics["stability_auc"] = 0.0

    if len(r2_arr) > 0 and r2_arr.max() > 0:
        metrics["stability_r2"] = round(float(r2_arr.min() / r2_arr.max()), 4)
    else:
        metrics["stability_r2"] = 0.0

    return metrics


def check_gates(m: dict) -> dict:
    return {
        "f1_pass": m["binary_f1"] > GATE_F1,
        "auc_pass": m["auc"] > GATE_AUC,
        "r2_pass": m["regression_r2"] > GATE_R2,
        "shuffle_bin_pass": m["shuffle_delta_bin"] > GATE_SHUFFLE_DELTA,
        "shuffle_reg_pass": m["shuffle_delta_reg"] > GATE_SHUFFLE_DELTA,
    }


def compute_group_stability(metrics: dict[str, dict], group_names: list[str],
                            full_metrics: dict | None) -> dict:
    """Compute stability ratios across a group of regimes.

    stability = min(metric) / max(metric) across regimes (including full if available).
    """
    group_metrics = {}
    if full_metrics:
        group_metrics["full"] = full_metrics
    for name in group_names:
        if name in metrics:
            group_metrics[name] = metrics[name]

    if not group_metrics:
        return {"stability_f1": 0, "stability_auc": 0, "stability_r2": 0}

    f1_vals = [m["binary_f1"] for m in group_metrics.values()]
    auc_vals = [m["auc"] for m in group_metrics.values()]
    r2_vals = [m["regression_r2"] for m in group_metrics.values()]

    def safe_ratio(vals):
        mx = max(vals)
        mn = min(vals)
        if mx > 0:
            return mn / mx
        return 0.0

    stab_f1 = safe_ratio(f1_vals)
    stab_auc = safe_ratio(auc_vals)
    stab_r2 = safe_ratio(r2_vals)

    return {
        "stability_f1": round(stab_f1, 4),
        "stability_auc": round(stab_auc, 4),
        "stability_r2": round(stab_r2, 4),
        "max_f1": round(max(f1_vals), 4),
        "min_f1": round(min(f1_vals), 4),
        "max_auc": round(max(auc_vals), 4),
        "min_auc": round(min(auc_vals), 4),
        "max_r2": round(max(r2_vals), 4),
        "min_r2": round(min(r2_vals), 4),
        "ratio_f1": round(max(f1_vals) / max(min(f1_vals), 0.01), 4),
        "ratio_auc": round(max(auc_vals) / max(min(auc_vals), 0.01), 4),
        "ratio_r2": round(max(r2_vals) / max(min(r2_vals), 0.01), 4),
    }


def determine_verdict(metrics: dict[str, dict], gates: dict[str, dict]) -> str:
    """Apply verdict tree from ROADMAP.md."""
    trend_ok = sum(1 for e in TREND_EXPERIMENTS if e in metrics and all(gates[e].values()))
    vol_ok = sum(1 for e in VOL_EXPERIMENTS if e in metrics and all(gates[e].values()))
    total_ok = trend_ok + vol_ok

    # Build per-regime gate pass map
    regime_pass = {}
    for e in ALL_EXPERIMENTS:
        if e == "full":
            continue
        if e in gates:
            regime_pass[e] = all(gates[e].values())
        else:
            regime_pass[e] = False

    n_pass = sum(regime_pass.values())

    # 1. REGIME ARTIFACT — most regimes collapse
    if n_pass <= 2:
        return "REGIME ARTIFACT"

    # 2. BULL-MARKET ALPHA — only bull survives
    if regime_pass.get("bull") and not regime_pass.get("bear"):
        return "BULL-MARKET ALPHA"

    # 3. VOLATILITY-PREMIUM ALPHA — only high_vol survives
    if regime_pass.get("high_vol") and not regime_pass.get("medium_vol") and not regime_pass.get("low_vol"):
        return "VOLATILITY-PREMIUM ALPHA"

    # 4. Check stability for all-pass case
    if n_pass >= 5:
        # Collect all available pass/fail for a more nuanced view
        all_f1 = [metrics[e]["binary_f1"] for e in ALL_EXPERIMENTS if e != "full" and e in metrics]
        all_auc = [metrics[e]["auc"] for e in ALL_EXPERIMENTS if e != "full" and e in metrics]

        def sr(vals):
            mx = max(vals)
            mn = min(vals)
            return mn / mx if mx > 0 else 0.0

        stab_f1 = sr(all_f1)
        stab_auc = sr(all_auc)

        if stab_f1 >= 0.75 and stab_auc >= 0.75:
            return "REGIME-INVARIANT ALPHA"
        else:
            return "REGIME-SENSITIVE ALPHA"

    # 5. Mixed case
    return "REGIME-SENSITIVE ALPHA"


def print_summary_table(metrics: dict[str, dict], gates: dict[str, dict]):
    """Print formatted results table."""
    logger.info(f"\n{'Experiment':15s}  {'F1':>7s}  {'AUC':>7s}  {'R²':>8s}  {'Δbin':>7s}  {'Δreg':>7s}  {'S(f1)':>6s}  {'S(auc)':>6s}  {'Gates':>6s}")
    logger.info("-" * 85)
    for exp in ALL_EXPERIMENTS:
        if exp not in metrics:
            logger.info(f"{exp:15s}  {'—':>7s}  {'—':>7s}  {'—':>8s}  {'—':>7s}  {'—':>7s}  {'—':>6s}  {'—':>6s}  {'—':>6s}")
            continue
        m = metrics[exp]
        g = gates.get(exp, {})
        n_pass = sum(1 for v in g.values() if v)
        logger.info(
            f"{exp:15s}  {m['binary_f1']:7.4f}  {m['auc']:7.4f}  {m['regression_r2']:8.4f}  "
            f"{m['shuffle_delta_bin']:7.4f}  {m['shuffle_delta_reg']:7.4f}  "
            f"{m.get('stability_f1', 0):6.4f}  {m.get('stability_auc', 0):6.4f}  {n_pass}/5"
        )


def print_gate_detail(gates: dict[str, dict]):
    """Print per-gate pass/fail."""
    logger.info(f"\n{'Experiment':15s}  {'F1>0.6':>8s}  {'AUC>0.7':>8s}  {'R²>0.05':>8s}  {'Δbin>0':>8s}  {'Δreg>0':>8s}")
    logger.info("-" * 65)
    for exp in ALL_EXPERIMENTS:
        if exp not in gates:
            logger.info(f"{exp:15s}  {'—':>8s}")
            continue
        g = gates[exp]
        logger.info(
            f"{exp:15s}  {str(g['f1_pass']):>8s}  {str(g['auc_pass']):>8s}  {str(g['r2_pass']):>8s}  "
            f"{str(g['shuffle_bin_pass']):>8s}  {str(g['shuffle_reg_pass']):>8s}"
        )


def main():
    logger.info("=" * 70)
    logger.info("QV2 Phase 4 — Cross-Regime Validation Summary")
    logger.info("=" * 70)

    results = load_results()

    # Check full (control)
    full = results.get("full")
    if full is None:
        logger.error("Missing full.json (control). Phase 4 invalidated.")
        return
    if full.get("status") == "insufficient_data" or full.get("status") == "error":
        logger.error(f"Full experiment failed: {full.get('status')}. Phase 4 invalidated.")
        return

    # Extract metrics
    metrics = {}
    for exp in ALL_EXPERIMENTS:
        r = results.get(exp)
        if r is None or r.get("status") in ("insufficient_data", "error"):
            logger.warning(f"[{exp}] no valid result — skipped")
            continue
        metrics[exp] = extract_metrics(r)

    if "full" not in metrics:
        logger.error("full experiment has no valid metrics. Phase 4 invalidated.")
        return

    # Check control deviation
    full_m = metrics["full"]
    control_f1_dev = abs(full_m["binary_f1"] - 0.744) / 0.744
    control_r2_dev = abs(full_m["regression_r2"] - 0.308) / 0.308
    if control_f1_dev > 0.05 or control_r2_dev > 0.05:
        logger.warning(
            f"Control deviation: F1={control_f1_dev*100:.1f}%, R²={control_r2_dev*100:.1f}%. "
            f"Phase 4 may be invalidated."
        )

    # Gates
    gates = {}
    for exp, m in metrics.items():
        gates[exp] = check_gates(m)

    # Group stability
    trend_stab = compute_group_stability(metrics, TREND_EXPERIMENTS, metrics.get("full"))
    vol_stab = compute_group_stability(metrics, VOL_EXPERIMENTS, metrics.get("full"))

    # Verdict
    verdict = determine_verdict(metrics, gates)

    # Print outputs
    print_summary_table(metrics, gates)
    print_gate_detail(gates)

    logger.info(f"\n{'─' * 40}")
    logger.info(f"Verdict: {verdict}")
    logger.info(f"{'─' * 40}")

    logger.info(f"\nTrend Stability:")
    logger.info(f"  F1:  min={trend_stab['min_f1']:.4f}  max={trend_stab['max_f1']:.4f}  "
                f"stability={trend_stab['stability_f1']:.4f}  ratio={trend_stab['ratio_f1']:.4f}")
    logger.info(f"  AUC: min={trend_stab['min_auc']:.4f}  max={trend_stab['max_auc']:.4f}  "
                f"stability={trend_stab['stability_auc']:.4f}  ratio={trend_stab['ratio_auc']:.4f}")
    logger.info(f"  R²:  min={trend_stab['min_r2']:.4f}  max={trend_stab['max_r2']:.4f}  "
                f"stability={trend_stab['stability_r2']:.4f}  ratio={trend_stab['ratio_r2']:.4f}")

    logger.info(f"\nVolatility Stability:")
    logger.info(f"  F1:  min={vol_stab['min_f1']:.4f}  max={vol_stab['max_f1']:.4f}  "
                f"stability={vol_stab['stability_f1']:.4f}  ratio={vol_stab['ratio_f1']:.4f}")
    logger.info(f"  AUC: min={vol_stab['min_auc']:.4f}  max={vol_stab['max_auc']:.4f}  "
                f"stability={vol_stab['stability_auc']:.4f}  ratio={vol_stab['ratio_auc']:.4f}")
    logger.info(f"  R²:  min={vol_stab['min_r2']:.4f}  max={vol_stab['max_r2']:.4f}  "
                f"stability={vol_stab['stability_r2']:.4f}  ratio={vol_stab['ratio_r2']:.4f}")

    # Save summary.json
    summary = {
        "experiment": "QV2_Phase4",
        "protocol": {"lookahead": 5, "stride": 5, "embargo": 1},
        "verdict": verdict,
        "control": {
            "binary_f1": full_m["binary_f1"],
            "auc": full_m["auc"],
            "regression_r2": full_m["regression_r2"],
            "f1_dev_pct": round(control_f1_dev * 100, 1),
            "r2_dev_pct": round(control_r2_dev * 100, 1),
        },
        "metrics": {exp: metrics[exp] if exp in metrics else None for exp in ALL_EXPERIMENTS},
        "gates": {exp: gates[exp] if exp in gates else None for exp in ALL_EXPERIMENTS},
        "trend_stability": trend_stab,
        "volatility_stability": vol_stab,
    }
    path = REPORT_DIR / "summary.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    logger.info(f"\nSummary saved to {path}")


if __name__ == "__main__":
    main()
