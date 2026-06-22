"""QV2 Phase 3.75 — Cross-Market Summary.

Loads phase375_master.json and produces one of 4 verdicts:

  STRUCTURAL ALPHA        — ETH + SOL both pass all gates
  BTC-SPECIFIC ALPHA      — Only BTC passes
  PARTIAL GENERALIZATION  — Mixed results across ETH/SOL
  DATASET ARTIFACT        — BTC fails (reproducibility broken)

Gates (same as Phase 3.5):
  G1: best_f1 > best_null_f1 * 1.01
  G2: shuffle_delta > 0.05  (signal > noise)
  G3: best_auc > 0.70       (binary AUC)
  G4: best_r2 > 0.05        (regression R²)
  G5: shuffle_delta_reg > 0.01

Usage:
  python3 -m experiments.quant_validation_v2_phase375.summary

Output:
  reports/quant_validation_v2_phase375/summary.json
  (console printed)
"""

from __future__ import annotations

import json
from pathlib import Path

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase375"


def load_master() -> dict:
    path = REPORT_DIR / "phase375_master.json"
    if not path.exists():
        raise FileNotFoundError(f"phase375_master.json not found at {path}\nRun `python3 -m experiments.quant_validation_v2_phase375.run` first.")
    with open(path) as f:
        return json.load(f)


def evaluate_symbol(master: dict, symbol: str) -> dict | None:
    r = master.get("symbols", {}).get(symbol.lower())
    if not r:
        return None

    phases = r.get("phases", {})
    p3b = phases.get("3B_binary", {})
    p3c = phases.get("3C_regression", {})

    best_f1 = p3b.get("best_f1", 0)
    best_null_f1 = p3b.get("best_null_f1", 0)
    best_auc = p3b.get("best_auc", 0)
    best_shuffle_delta = p3b.get("best_shuffle_delta", 0)
    best_r2 = p3c.get("best_r2", 0)
    best_null_r2 = p3c.get("best_null_r2", 0)
    best_shuffle_delta_reg = p3c.get("best_shuffle_delta", 0)

    g1 = bool(best_f1 > best_null_f1 * 1.01)
    g2 = bool(best_shuffle_delta > 0.05)
    g3 = bool(best_auc and best_auc > 0.70)
    g4 = bool(best_r2 > 0.05)
    g5 = bool(best_shuffle_delta_reg > 0.01)
    gates_passed = sum([g1, g2, g3, g4, g5])

    return {
        "symbol": f"{symbol}/USDT",
        "binary_f1": round(best_f1, 4),
        "binary_null_f1": round(best_null_f1, 4),
        "binary_auc": round(best_auc, 4) if best_auc else None,
        "binary_shuffle_delta": round(best_shuffle_delta, 4),
        "regression_r2": round(best_r2, 4),
        "regression_null_r2": round(best_null_r2, 4),
        "regression_shuffle_delta": round(best_shuffle_delta_reg, 4),
        "g1_beats_null": g1,
        "g2_shuffle_delta_gt_005": g2,
        "g3_auc_gt_070": g3 if best_auc else "N/A",
        "g4_r2_gt_005": g4,
        "g5_shuffle_reg_gt_001": g5,
        "gates_passed": f"{gates_passed}/5",
        "all_gates_passed": gates_passed >= 4,
    }


def verdict(results: dict) -> str:
    btc = results.get("btc", {})
    eth = results.get("eth", {})
    sol = results.get("sol", {})

    btc_pass = btc.get("all_gates_passed", False) if btc else False
    eth_pass = eth.get("all_gates_passed", False) if eth else False
    sol_pass = sol.get("all_gates_passed", False) if sol else False

    if not btc_pass:
        return "DATASET ARTIFACT"

    if eth_pass and sol_pass:
        return "STRUCTURAL ALPHA"

    if eth_pass or sol_pass:
        return "PARTIAL GENERALIZATION"

    return "BTC-SPECIFIC ALPHA"


def describe_verdict(v: str) -> str:
    descriptions = {
        "STRUCTURAL ALPHA": (
            "The predictive signal discovered in BTC/USDT generalizes robustly to ETH and SOL. "
            "This is strong evidence that MTF+Funding features capture structural crypto market dynamics "
            "independent of the specific asset. Recommend: production deployment with multi-asset support."
        ),
        "BTC-SPECIFIC ALPHA": (
            "The signal is unique to BTC/USDT and does not generalize to ETH or SOL. "
            "This may be due to BTC's unique market microstructure, liquidity profile, or derivative dynamics. "
            "Recommend: BTC-only strategy, investigate ETH/SOL-specific features."
        ),
        "PARTIAL GENERALIZATION": (
            "Mixed results: the signal generalizes to some altcoins but not others. "
            "This may indicate asset-specific feature relevance or data quality differences. "
            "Recommend: further investigation per asset, consider asset-specific feature sets."
        ),
        "DATASET ARTIFACT": (
            "BTC/USDT results from Phase 3.5 could not be reproduced. "
            "This suggests a pipeline bug, data mismatch, or random seed sensitivity. "
            "DO NOT deploy. Investigate immediately."
        ),
    }
    return descriptions.get(v, "Unknown verdict.")


def main():
    print("=" * 70)
    print("QV2 PHASE 3.75 — Cross-Market Validation Summary")
    print("=" * 70)

    master = load_master()
    print(f"\nExperiment: {master.get('experiment', '')}")
    proto = master.get("protocol", {})
    print(f"Protocol:   lookahead={proto.get('lookahead')}, stride={proto.get('stride')}, embargo={proto.get('embargo')}")
    print(f"Elapsed:    {master.get('elapsed_seconds', '?')}s")

    results = {}
    for sym in ["btc", "eth", "sol"]:
        ev = evaluate_symbol(master, sym)
        results[sym] = ev
        if ev:
            print(f"\n{'─' * 50}")
            print(f"  {ev['symbol']}")
            print(f"  {'─' * 30}")
            print(f"    Binary F1:         {ev['binary_f1']:.4f}  (null: {ev['binary_null_f1']:.4f})")
            print(f"    Binary AUC:        {ev['binary_auc']}")
            print(f"    Shuffle Δ (bin):   {ev['binary_shuffle_delta']:+.4f}")
            print(f"    Regression R²:     {ev['regression_r2']:.4f}  (null: {ev['regression_null_r2']:.4f})")
            print(f"    Shuffle Δ (reg):   {ev['regression_shuffle_delta']:+.4f}")
            print(f"    Gates:             {ev['gates_passed']}" + (" ✅" if ev['all_gates_passed'] else " ❌"))
        else:
            print(f"\n  {sym.upper():6s}  ❌ No data available")

    v = verdict(results)
    print(f"\n{'=' * 70}")
    print(f"  VERDICT: {v}")
    print(f"{'=' * 70}")
    print(f"\n{describe_verdict(v)}")

    master.update({
        "evaluation": results,
        "verdict": v,
        "verdict_description": describe_verdict(v),
    })

    out_path = REPORT_DIR / "summary.json"
    with open(out_path, "w") as f:
        json.dump(master, f, indent=2, default=str)
    print(f"\n[summary] saved to {out_path}")


if __name__ == "__main__":
    main()
