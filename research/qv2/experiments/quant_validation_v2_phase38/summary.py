"""QV2 Phase 3.8 — Cross-Exchange Summary.

Loads phase38_master.json and individual {symbol}_{exchange}.json files.
Evaluates degradation patterns across exchanges.

Verdicts:
  EXCHANGE-INVARIANT ALPHA   — Balanced metrics across all exchanges
  PARTIAL INVARIANCE          — Systematic degradation on some exchanges
  EXCHANGE-SPECIFIC ALPHA     — Signal only in one exchange
  MICROSTRUCTURE ARTIFACT     — Control fails or signal collapses

Usage:
  python3 -m experiments.quant_validation_v2_phase38.summary

Output:
  reports/quant_validation_v2_phase38/summary.json
"""

from __future__ import annotations

import json
from pathlib import Path

REPORT_DIR = Path(__file__).parent.parent.parent / "reports" / "quant_validation_v2_phase38"

SYMBOL_LIST = ["BTC", "ETH", "SOL"]
EXCHANGE_LIST = ["binance", "bybit", "okx"]

# Phase 3.5 baseline (Binance averages across assets)
BASELINE = {
    "binary_f1": 0.734,   # avg of BTC=0.744, ETH=0.733, SOL=0.723
    "binary_auc": 0.822,  # avg of BTC=0.826, ETH=0.833, SOL=0.807
    "regression_r2": 0.276,  # avg of BTC=0.308, ETH=0.276, SOL=0.243
}


def load_result(symbol: str, exchange: str) -> dict | None:
    path = REPORT_DIR / f"{symbol.lower()}_{exchange}.json"
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def evaluate_combination(r: dict) -> dict:
    phases = r.get("phases", {})
    p3b = phases.get("3B_binary", {})
    p3c = phases.get("3C_regression", {})

    f1 = p3b.get("best_f1", 0)
    null_f1 = p3b.get("best_null_f1", 0)
    auc = p3b.get("best_auc", 0) or 0
    sd_bin = p3b.get("best_shuffle_delta", 0)
    r2 = p3c.get("best_r2", 0) or 0
    null_r2 = p3c.get("best_null_r2", 0) or 0
    sd_reg = p3c.get("best_shuffle_delta", 0)

    # Gates
    g1 = f1 > 0.60
    g2 = sd_bin > 0.01  # shuffle delta positive
    g3 = auc > 0.70
    g4 = r2 > 0.05
    g5 = sd_reg > 0.01
    gates_passed = sum([g1, g2, g3, g4, g5])

    # Severity of degradation vs baseline
    f1_degradation = 1.0 - (f1 / BASELINE["binary_f1"]) if BASELINE["binary_f1"] > 0 else 0
    r2_degradation = 1.0 - (r2 / BASELINE["regression_r2"]) if BASELINE["regression_r2"] > 0 else 0

    return {
        "binary_f1": round(f1, 4),
        "binary_null_f1": round(null_f1, 4),
        "binary_auc": round(auc, 4),
        "binary_shuffle_delta": round(sd_bin, 4),
        "regression_r2": round(r2, 4),
        "regression_null_r2": round(null_r2, 4),
        "regression_shuffle_delta": round(sd_reg, 4),
        "g1_binary_f1_gt_060": g1,
        "g2_shuffle_delta_pos": g2,
        "g3_auc_gt_070": g3,
        "g4_r2_gt_005": g4,
        "g5_shuffle_reg_pos": g5,
        "gates_passed": f"{gates_passed}/5",
        "all_gates_passed": gates_passed >= 4,
        "f1_vs_baseline": round(f1 - BASELINE["binary_f1"], 4),
        "r2_vs_baseline": round(r2 - BASELINE["regression_r2"], 4),
    }


def aggregate_by_exchange(results: dict[str, dict]) -> dict:
    """Compute average metrics per exchange across all assets."""
    by_exchange: dict[str, list] = {ex: [] for ex in EXCHANGE_LIST}
    for sym in SYMBOL_LIST:
        for ex in EXCHANGE_LIST:
            key = f"{sym.lower()}_{ex}"
            r = results.get(key)
            if r:
                by_exchange[ex].append(r)

    agg = {}
    for ex, items in by_exchange.items():
        if not items:
            agg[ex] = {"avg_f1": 0, "avg_auc": 0, "avg_r2": 0, "gates_avg": 0, "n": 0}
            continue
        agg[ex] = {
            "avg_f1": round(sum(i["binary_f1"] for i in items) / len(items), 4),
            "avg_auc": round(sum(i["binary_auc"] for i in items) / len(items), 4),
            "avg_r2": round(sum(i["regression_r2"] for i in items) / len(items), 4),
            "gates_avg": round(sum(int(i["gates_passed"].split("/")[0]) for i in items) / len(items), 2),
            "n": len(items),
        }
    return agg


def determine_verdict(results: dict[str, dict], control_pass: bool) -> tuple[str, str]:
    """Pattern-based verdict focusing on degradation patterns."""

    if not control_pass:
        return "MICROSTRUCTURE ARTIFACT", "Control (BTC+Binance) failed to reproduce Phase 3.5."

    combos = list(results.values())
    all_pass = sum(1 for c in combos if c and c["all_gates_passed"])
    total = len(combos)

    if all_pass <= 1:
        return "MICROSTRUCTURE ARTIFACT", f"Signal collapses outside control: only {all_pass}/{total} combos pass."

    # Check per-exchange degradation
    by_ex = aggregate_by_exchange(results)
    bin_f1 = by_ex.get("binance", {}).get("avg_f1", 0)
    byb_f1 = by_ex.get("bybit", {}).get("avg_f1", 0)
    okx_f1 = by_ex.get("okx", {}).get("avg_f1", 0)

    bin_r2 = by_ex.get("binance", {}).get("avg_r2", 0)
    byb_r2 = by_ex.get("bybit", {}).get("avg_r2", 0)
    okx_r2 = by_ex.get("okx", {}).get("avg_r2", 0)

    f1_values = [v for v in [byb_f1, okx_f1] if v > 0]
    r2_values = [v for v in [byb_r2, okx_r2] if v > 0]

    # How much do non-Binance exchanges degrade?
    avg_nonbin_f1 = sum(f1_values) / len(f1_values) if f1_values else 0
    avg_nonbin_r2 = sum(r2_values) / len(r2_values) if r2_values else 0

    f1_degradation = 1.0 - (avg_nonbin_f1 / bin_f1) if bin_f1 > 0 else 1.0
    r2_degradation = 1.0 - (avg_nonbin_r2 / bin_r2) if bin_r2 > 0 else 1.0

    if f1_degradation < 0.10 and r2_degradation < 0.30:
        description = (
            f"Metrics degrade <10%% F1 and <30%% R² outside Binance "
            f"(F1: {bin_f1:.3f}→{avg_nonbin_f1:.3f}, R²: {bin_r2:.3f}→{avg_nonbin_r2:.3f}). "
            f"{all_pass}/{total} combos pass all gates. "
            f"Signal structure is exchange-invariant."
        )
        return "EXCHANGE-INVARIANT ALPHA", description

    if f1_degradation < 0.20 and r2_degradation < 0.50:
        description = (
            f"Non-trivial degradation outside Binance "
            f"(F1: {bin_f1:.3f}→{avg_nonbin_f1:.3f}, R²: {bin_r2:.3f}→{avg_nonbin_r2:.3f}) "
            f"but signal survives in {all_pass}/{total} combos. "
            f"Alpha is real but sensitive to microstructure."
        )
        return "PARTIAL INVARIANCE", description

    if f1_degradation < 0.40:
        description = (
            f"Strong degradation outside Binance "
            f"(F1: {bin_f1:.3f}→{avg_nonbin_f1:.3f}, R²: {bin_r2:.3f}→{avg_nonbin_r2:.3f}). "
            f"Signal is largely exchange-specific with partial generalization."
        )
        return "EXCHANGE-SPECIFIC ALPHA", description

    description = (
        f"Signal collapses outside Binance "
        f"(F1: {bin_f1:.3f}→{avg_nonbin_f1:.3f}, R²: {bin_r2:.3f}→{avg_nonbin_r2:.3f}). "
        f"Only {all_pass}/{total} combos pass. Microstructure artifact."
    )
    return "MICROSTRUCTURE ARTIFACT", description


def main():
    print("=" * 70)
    print("QV2 PHASE 3.8 — Cross-Exchange Validation Summary")
    print("=" * 70)

    # Load master
    master_path = REPORT_DIR / "phase38_master.json"
    if not master_path.exists():
        print("❌ phase38_master.json not found. Run run.py first.")
        return
    with open(master_path) as f:
        master = json.load(f)

    if master.get("aborted"):
        print("\n⚠ Experiment was ABORTED during control check.")
        print("Verdict: MICROSTRUCTURE ARTIFACT (control failed)")
        return

    print(f"\nProtocol: lookahead={master.get('protocol', {}).get('lookahead')}, "
          f"stride={master.get('protocol', {}).get('stride')}, "
          f"embargo={master.get('protocol', {}).get('embargo')}")
    print(f"Elapsed: {master.get('elapsed_seconds', '?')}s")

    # Evaluate each combination
    results = {}
    for sym in SYMBOL_LIST:
        for ex in EXCHANGE_LIST:
            key = f"{sym.lower()}_{ex}"
            r_raw = load_result(sym, ex)
            if r_raw:
                ev = evaluate_combination(r_raw)
                results[key] = ev
            else:
                results[key] = None

    # Print table
    print(f"\n{'Combination':20s}  {'F1':>6s}  {'AUC':>6s}  {'R²':>6s}  {'Δbin':>7s}  {'Δreg':>7s}  {'Gates':>6s}  {'vsBase':>7s}")
    print("-" * 80)
    for sym in SYMBOL_LIST:
        for ex in EXCHANGE_LIST:
            key = f"{sym.lower()}_{ex}"
            ev = results.get(key)
            if not ev:
                print(f"{f'{sym}/{ex}':20s}  {'MISSING':>50s}")
                continue
            label = f"{sym}/{ex}"
            f1 = ev["binary_f1"]
            auc = ev["binary_auc"]
            r2 = ev["regression_r2"]
            sd_bin = ev["binary_shuffle_delta"]
            sd_reg = ev["regression_shuffle_delta"]
            gates = ev["gates_passed"]
            vb = ev["f1_vs_baseline"]
            ok = "✅" if ev["all_gates_passed"] else "❌"
            print(f"{label:20s}  {f1:6.4f}  {auc:6.4f}  {r2:6.4f}  {sd_bin:7.4f}  {sd_reg:7.4f}  {gates:>5s}  {vb:7.4f}  {ok}")

    # Exchange-level aggregates
    print(f"\n{'─' * 50}")
    print("  Exchange-level aggregates (avg over assets)")
    print(f"{'─' * 50}")
    by_ex = aggregate_by_exchange(results)
    for ex in EXCHANGE_LIST:
        a = by_ex[ex]
        if a["n"] > 0:
            print(f"  {ex:10s}  F1={a['avg_f1']:.4f}  AUC={a['avg_auc']:.4f}  R²={a['avg_r2']:.4f}  gates={a['gates_avg']}/5 (n={a['n']})")

    # Control verification
    btc_binance = results.get("btc_binance", {})
    control_pass = bool(btc_binance and btc_binance["all_gates_passed"])
    if btc_binance:
        f1_ctrl = btc_binance["binary_f1"]
        r2_ctrl = btc_binance["regression_r2"]
        print(f"\n  Control (BTC/Binance): F1={f1_ctrl:.4f}  R²={r2_ctrl:.4f}  "
              f"{'✅ PASS' if control_pass else '❌ FAIL'}")

    # Verdict
    v, desc = determine_verdict(results, control_pass)
    print(f"\n{'=' * 70}")
    print(f"  VERDICT: {v}")
    print(f"{'=' * 70}")
    print(f"\n  {desc}")

    # Save
    summary = {
        "experiment": "QV2_Phase38",
        "protocol": {"lookahead": 5, "stride": 5, "embargo": 1},
        "evaluation": results,
        "exchange_aggregates": by_ex,
        "control_pass": control_pass,
        "verdict": v,
        "verdict_description": desc,
    }
    out_path = REPORT_DIR / "summary.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[summary] saved to {out_path}")


if __name__ == "__main__":
    main()
