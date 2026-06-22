#!/usr/bin/env python3
"""H₀₂ BlockNull — Certification Suite.

Three properties (spec Section 18.4(2)):

    1. SYMMETRY   — P(H₀₂a > H₀₂b) ≈ 0.5 independent seeds
    2. NON-DEGEN  — unique trajectories, U(τ) spread, no collapse
    3. STABILITY  — Φ variance across seeds is bounded

Usage:
    python scripts/validate_h02.py
"""

from __future__ import annotations

import csv
import hashlib
import math
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BTC_CSV = PROJECT_ROOT / "forward_test" / "trades_btc.csv"

sys.path.insert(0, str(PROJECT_ROOT / "apps" / "analytics-engine" / "app"))

from domain.edl.inference.nulls.block_null import BlockNull, compute_regimes_roc20
from domain.edl.inference import EdgeInferenceEngine


# ── I/O ────────────────────────────────────────────────────────────

def load_trades(path: str | Path = BTC_CSV) -> list[dict]:
    trades: list[dict] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            d: dict = dict(row)
            raw = d.get("side", "0")
            d["side"] = 1 if raw in ("LONG", "1") else -1 if raw in ("SHORT", "-1") else 0
            for k in ("entry_price", "exit_price", "size", "gross_pnl",
                      "costs", "net_pnl", "duration_bars"):
                if k in d and d[k] is not None:
                    d[k] = float(d[k])
            trades.append(d)
    return trades


# ── Utility (order-dependent → BlockNull changes this) ────────────

def trajectory_utility(trajectory: list[dict]) -> float:
    """U(τ) = -(max drawdown of cumulative net PnL).

    Order-dependent → BlockNull transforms change this value.
    Higher = better (less downside).
    """
    if not trajectory:
        return 0.0
    pnl = np.array([float(t.get("net_pnl", 0.0)) for t in trajectory])
    if len(pnl) == 0:
        return 0.0
    cumsum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    return -float(abs(dd.min()))


def trajectory_fingerprint(trajectory: list[dict]) -> str:
    ids = [str(t.get("trade_id", t.get("net_pnl", 0.0))) for t in trajectory]
    return hashlib.sha256(",".join(ids).encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════
# 1. SYMMETRY
# ═══════════════════════════════════════════════════════════════════

def test_symmetry(trades: list[dict], block_size: int = 10, n_pairs: int = 2000) -> dict:
    rng = np.random.default_rng(0)
    wins_a, ties = 0, 0
    u_vals: list[float] = []
    t0 = time.time()

    for i in range(n_pairs):
        seed_a = int(rng.integers(0, 2 ** 31))
        seed_b = int(rng.integers(0, 2 ** 31))
        ta = BlockNull(block_size=block_size, seed=seed_a).transform(trades)
        tb = BlockNull(block_size=block_size, seed=seed_b).transform(trades)
        ua, ub = trajectory_utility(ta), trajectory_utility(tb)
        u_vals.extend([ua, ub])
        if ua > ub:
            wins_a += 1
        elif abs(ua - ub) < 1e-12:
            ties += 1

    n_eff = n_pairs - ties
    p_hat = wins_a / n_eff if n_eff > 0 else 0.5
    se = math.sqrt(0.5 * 0.5 / n_eff) if n_eff > 0 else 0.0
    z = (p_hat - 0.5) / se if se > 0 else 0.0
    p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2.0))))
    elapsed = time.time() - t0

    return {
        "n_pairs": n_pairs, "wins_a": wins_a, "ties": ties,
        "p_hat": round(p_hat, 4), "z_score": round(float(z), 4),
        "p_value": round(p_val, 6),
        "pass_symmetry": p_val > 0.01,
        "u_mean": round(float(np.mean(u_vals)), 4),
        "u_std": round(float(np.std(u_vals)), 4),
        "elapsed_s": round(elapsed, 1),
    }


# ═══════════════════════════════════════════════════════════════════
# 2. NON-DEGENERATION
# ═══════════════════════════════════════════════════════════════════

def test_non_degeneration(trades: list[dict], block_size: int = 10, n_seeds: int = 500) -> dict:
    fps: dict[str, int] = {}
    u_vals: list[float] = []
    t0 = time.time()

    for seed in range(n_seeds):
        traj = BlockNull(block_size=block_size, seed=seed).transform(trades)
        fp = trajectory_fingerprint(traj)
        fps[fp] = fps.get(fp, 0) + 1
        u_vals.append(trajectory_utility(traj))

    u_arr = np.array(u_vals)
    unique_trajs = len(fps)
    sorted_counts = sorted(fps.values(), reverse=True)
    top_share = sorted_counts[0] / n_seeds if sorted_counts else 1.0
    cv = float(u_arr.std() / abs(u_arr.mean())) if abs(u_arr.mean()) > 1e-12 else float("inf")
    elapsed = time.time() - t0

    return {
        "n_seeds": n_seeds, "n_unique_trajectories": unique_trajs,
        "collision_rate": round(1.0 - unique_trajs / n_seeds, 4),
        "top_fingerprint_share": round(top_share, 4),
        "u_mean": round(float(u_arr.mean()), 4),
        "u_std": round(float(u_arr.std()), 4),
        "u_min": round(float(u_arr.min()), 4),
        "u_max": round(float(u_arr.max()), 4),
        "u_coeff_variation": round(float(cv), 4),
        "pass_no_collapse": unique_trajs > n_seeds * 0.5,
        "elapsed_s": round(elapsed, 1),
    }


# ═══════════════════════════════════════════════════════════════════
# 3. STABILITY (lightweight — inference engine is expensive)
# ═══════════════════════════════════════════════════════════════════

def test_stability(
    trades: list[dict],
    block_size: int = 10,
    regimes: list[int] | None = None,
    n_seeds: int = 15,
    n_null_samples: int = 80,
    n_bootstrap: int = 30,
) -> dict:
    components = ["directional", "timing", "execution", "structural"]
    means: dict[str, list[float]] = {c: [] for c in components}
    idents: dict[str, list[bool]] = {c: [] for c in components}
    t0 = time.time()

    for seed in range(n_seeds):
        bn = BlockNull(block_size=block_size, regimes=regimes, seed=seed + 1000)
        engine = EdgeInferenceEngine(
            null_suite=[bn],
            utility_fn=trajectory_utility,
            n_null_samples=n_null_samples,
            n_bootstrap=n_bootstrap,
            seed=seed + 1000,
        )
        phi = engine.infer(trades).to_dict()
        for c in components:
            means[c].append(phi[c]["mean"])
            idents[c].append(phi[c].get("identifiable", False))

    result: dict = {}
    for c in components:
        arr = np.array(means[c])
        id_arr = np.array(idents[c])
        result[c] = {
            "mean_across_seeds": round(float(arr.mean()), 6),
            "std_across_seeds": round(float(arr.std()), 6),
            "min": round(float(arr.min()), 6),
            "max": round(float(arr.max()), 6),
            "range": round(float(arr.max() - arr.min()), 6),
            "n_identifiable": int(id_arr.sum()),
        }
    result["n_seeds"] = n_seeds
    result["pass_stability"] = all(result[c]["std_across_seeds"] < 0.1 for c in components)
    result["elapsed_s"] = round(time.time() - t0, 1)
    return result


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def print_banner(msg: str) -> None:
    print(f"\n{'─' * 72}\n  {msg}\n{'─' * 72}")


def main() -> int:
    print("═" * 72)
    print("  H₀₂ BlockNull — Certification Suite")
    print("═" * 72)

    trades = load_trades()
    regimes = compute_regimes_roc20(trades)
    n_up = sum(1 for r in regimes if r == 0)
    n_down = sum(1 for r in regimes if r == 1)
    n_ranging = sum(1 for r in regimes if r == 2)
    print(f"  Trades: {len(trades)} BTC")
    print(f"  Regimes (ROC20): {n_up} UP / {n_down} DOWN / {n_ranging} RANGING")
    print(f"  Regime blocks: {len([r for r in regimes if r != regimes[0]])} transitions")

    B = 10  # block_size

    # ── 1. Symmetry ────────────────────────────────────────────────
    print_banner("1. SYMMETRY — P(H₀₂a > H₀₂b) ≈ 0.5")
    sym = test_symmetry(trades, B, n_pairs=2000)
    print(f"     Pairs     : {sym['n_pairs']}  ({sym['elapsed_s']}s)")
    print(f"     Wins(a)   : {sym['wins_a']}  Ties: {sym['ties']}")
    print(f"     P̂(a > b)  : {sym['p_hat']}   Z: {sym['z_score']}")
    print(f"     P-value   : {sym['p_value']}")
    s = "✅ PASS" if sym["pass_symmetry"] else "❌ FAIL"
    print(f"     Status    : {s}")
    print(f"     U(τ)      : μ={sym['u_mean']} σ={sym['u_std']}")

    # ── 2. Non-degeneration ────────────────────────────────────────
    print_banner("2. NON-DEGENERATION — uniqueness + U(τ) spread")
    nd = test_non_degeneration(trades, B, n_seeds=500)
    print(f"     Seeds        : {nd['n_seeds']}  ({nd['elapsed_s']}s)")
    print(f"     Unique trajs : {nd['n_unique_trajectories']}")
    print(f"     Collisions   : {nd['collision_rate']}")
    print(f"     Top fp share : {nd['top_fingerprint_share']}")
    print(f"     U(τ)         : μ={nd['u_mean']} σ={nd['u_std']}  "
          f"[{nd['u_min']}, {nd['u_max']}]  CV={nd['u_coeff_variation']}")
    s = "✅ PASS" if nd["pass_no_collapse"] else "❌ FAIL"
    print(f"     Status       : {s}")

    # ── 3. Stability ───────────────────────────────────────────────
    print_banner("3. STABILITY — Φ variance across seeds (inference engine)")
    print(f"     (n_seeds=15, n_null=80, bootstrap=30)")
    st = test_stability(trades, B, regimes,
                        n_seeds=15, n_null_samples=80, n_bootstrap=30)
    print(f"     Seeds: {st['n_seeds']}  ({st['elapsed_s']}s)")
    print(f"     {'Component':<15} {'μ(Φ)':>10} {'σ(Φ)':>10} {'Range':>10} {'Id':>6}")
    print(f"     {'─' * 53}")
    for c in ["directional", "timing", "execution", "structural"]:
        s = st[c]
        print(f"     {c:<15} {s['mean_across_seeds']:>10.6f} {s['std_across_seeds']:>10.6f} "
              f"{s['range']:>10.6f} {s['n_identifiable']:>6}/{st['n_seeds']}")
    s = "✅ PASS" if st["pass_stability"] else "❌ FAIL"
    print(f"     Status : {s}")

    # ── Summary ────────────────────────────────────────────────────
    all_pass = sym["pass_symmetry"] and nd["pass_no_collapse"] and st["pass_stability"]
    print(f"\n{'═' * 72}")
    print(f"  H₀₂ CERTIFICATION: {'✅ ALL PASS' if all_pass else '❌ SOME FAILED'}")
    print(f"                     Symmetry={sym['pass_symmetry']}"
          f"  NonDegen={nd['pass_no_collapse']}"
          f"  Stability={st['pass_stability']}")
    print(f"{'═' * 72}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
