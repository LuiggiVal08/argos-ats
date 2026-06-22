#!/usr/bin/env python3
"""Extended SRV — Live → Replay → H₀₁ + H₀₂ → Φ, verify drift = 0.

Also produces the first real edge component map: [E_d, E_t, E_e, E_s]
under a complete null suite, decomposing the strategy signal by source.

Usage:
    python scripts/srv_extended.py
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BTC_CSV = PROJECT_ROOT / "forward_test" / "trades_btc.csv"

sys.path.insert(0, str(PROJECT_ROOT / "apps" / "analytics-engine" / "app"))

from domain.edl.inference import EdgeInferenceEngine
from domain.edl.inference.edge_tensor import EdgeTensor
from domain.edl.inference.nulls import PermutationNull, BlockNull, BaseNull
from domain.edl.inference.nulls.block_null import compute_regimes_roc20

sys.path.insert(0, str(PROJECT_ROOT))
from validation.srv.tensor_comparator import TensorComparator, EPSILON


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


# ── Utility ────────────────────────────────────────────────────────

def trajectory_utility(trajectory: list[dict]) -> float:
    """U(τ) = -(max drawdown of cumulative net PnL). Higher = better."""
    if not trajectory:
        return 0.0
    pnl = np.array([float(t.get("net_pnl", 0.0)) for t in trajectory])
    if len(pnl) == 0:
        return 0.0
    cumsum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    return -float(abs(dd.min()))


# ── Create inference engine with dual-null suite ───────────────────

def make_engine(
    trades: list[dict],
    seed: int = 42,
    n_null_samples: int = 250,
    n_bootstrap: int = 100,
) -> EdgeInferenceEngine:
    regimes = compute_regimes_roc20(trades)
    null_suite: list[BaseNull] = [
        PermutationNull(seed=seed),
        BlockNull(block_size=10, regimes=regimes, seed=seed),
    ]
    return EdgeInferenceEngine(
        null_suite=null_suite,
        utility_fn=trajectory_utility,
        n_null_samples=n_null_samples,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )


# ── Report formatters ──────────────────────────────────────────────

def fmt_component(name: str, c: dict) -> str:
    id_flag = "🟢" if c.get("identifiable") else "⚪"
    return (f"      {name:<15} μ={c['mean']:>10.6f} σ={c['std']:>10.6f}  "
            f"CI=[{c['ci'][0]:>.6f}, {c['ci'][1]:>.6f}]  {id_flag}")


def fmt_tensor(title: str, phi: dict) -> None:
    print(f"  {title}")
    print(f"    identifiability: {phi['identifiability']}")
    print(f"    has_any_edge: {phi['has_any_edge']}  has_full_edge: {phi['has_full_edge']}")
    for c in ["directional", "timing", "execution", "structural"]:
        print(fmt_component(c, phi[c]))


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    print("═" * 72)
    print("  Extended SRV — H₀₁ + H₀₂ Dual-Null Consistency & Edge Map")
    print("═" * 72)

    t0 = time.time()
    trades = load_trades()
    print(f"\n  Trades: {len(trades)} BTC\n")

    # ── Live inference ──────────────────────────────────────────────
    print("─" * 72)
    print("  🔴 LIVE — EdgeInferenceEngine(H₀₁ + H₀₂)")
    print(f"  (n_null_samples=250, n_bootstrap=100)")
    print("─" * 72)

    engine_live = make_engine(trades, seed=42)
    phi_live_t = time.time()
    phi_live = engine_live.infer(trades)
    phi_live_t = time.time() - phi_live_t
    phi_live_d = phi_live.to_dict()
    fmt_tensor(f"Φ_live ({phi_live_t:.0f}s):", phi_live_d)

    # ── Replay inference (same trades — simulates Truth Store) ─────
    print(f"\n{'─' * 72}")
    print("  🔵 REPLAY — same trades, same engine")
    print("─" * 72)

    engine_replay = make_engine(trades, seed=42)
    phi_replay_t = time.time()
    phi_replay = engine_replay.infer(trades)
    phi_replay_t = time.time() - phi_replay_t
    phi_replay_d = phi_replay.to_dict()
    fmt_tensor(f"Φ_replay ({phi_replay_t:.0f}s):", phi_replay_d)

    # ── Tensor comparison ─────────────────────────────────────────
    print(f"\n{'─' * 72}")
    print("  🧪 TensorComparator — Φ_live vs Φ_replay")
    print(f"  EPSILON = {EPSILON}")
    print("─" * 72)

    comp = TensorComparator.compare(phi_live, phi_replay, epsilon=EPSILON)
    for td in comp["components"]:
        icon = "✅" if td.within_tolerance else "❌"
        id_icon = "✅" if td.identifiability_match else "❌"
        print(f"    {td.component:<15} drift={td.drift:.2e}  "
              f"tol={icon}  id={id_icon}")

    print(f"\n    all_within_tolerance : {comp['all_within_tolerance']}")
    print(f"    all_id_match         : {comp['all_identifiability_match']}")
    print(f"    total_drift          : {comp['total_drift']:.2e}")
    print(f"    n_failures           : {comp['n_failures']}")

    replay_pass = (comp["all_within_tolerance"]
                   and comp["all_identifiability_match"]
                   and comp["n_failures"] == 0)
    status_replay = "✅ PASS" if replay_pass else "❌ FAIL"
    print(f"\n    SRV(Replay) Status   : {status_replay}")

    # ── Edge component map ─────────────────────────────────────────
    print(f"\n{'─' * 72}")
    print("  🗺️  EDGE COMPONENT MAP — First complete decomposition")
    print("─" * 72)

    phi = phi_live_d
    id_map = phi.get("identifiability", {})

    interpretations = {
        "directional": "Skill in predicting trade direction (side ±1)",
        "timing": "Entry/exit precision within temporal block structure",
        "execution": "Cost-structure exploitation (spread, fees, slippage)",
        "structural": "Regime-clustering signal (temporal block ordering)",
    }

    print()
    print(f"    {'Component':<15} {'μ':>10} {'σ':>10} {'SNR':>8} {'Id?':>5}  {'Signal?'}")
    print(f"    {'─' * 68}")
    for c in ["directional", "timing", "execution", "structural"]:
        pc = phi[c]
        mean = pc["mean"]
        std = pc["std"]
        id_ = id_map.get(c, False)
        snr = abs(mean) / std if std > 0 else float("inf")
        nonzero = id_ and (abs(mean) > 2.0 * std if std > 0 else abs(mean) > 0)
        id_str = "✅" if id_ else "❌"
        sig_str = "🟢" if nonzero else ("⚪" if id_ else "⚫")
        print(f"    {c:<15} {mean:>10.6f} {std:>10.6f} {snr:>8.2f}  {id_str}    {sig_str}  {interpretations[c]}")

    print()
    for c in ["directional", "timing", "execution", "structural"]:
        pc = phi[c]
        print(f"    {c:<15} 95% CI = [{pc['ci'][0]:.6f}, {pc['ci'][1]:.6f}]")

    # ── Summary ────────────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f"\n{'═' * 72}")
    print(f"  EXTENDED SRV: {status_replay}  ({elapsed:.0f}s)")
    print(f"  Null suite: permutation_null + block_null")
    print(f"  Zero drift across all 4 Φ components: {'YES' if replay_pass else 'NO'}")
    print(f"{'═' * 72}")

    # Detailed edge map JSON
    print(f"\n  Edge Tensor (live):")
    print(json.dumps(phi_live_d, indent=4))

    return 0 if replay_pass else 1


if __name__ == "__main__":
    sys.exit(main())
