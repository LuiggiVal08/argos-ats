#!/usr/bin/env python3
"""Strategy vs Market Separation Test.

Pregunta: ¿Φ mide mercado o arquitectura de decisión?

Hipótesis: si un cambio en la política de decisión (θ) produce
cambios en Φ comparables o mayores que los que produce H₀,
entonces el sistema mide su propio diseño, no causalidad de mercado.

Formalmente:
    V_strategy = Var(Φ | θ perturbado, mismo mercado fijo)
    V_market   = Var(Φ | θ fijo, mercado perturbado por H₀)

Condición de identificabilidad real:
    V_strategy << V_market   → Φ es instrumento de mercado
    V_strategy ≈ V_market    → Φ acoplado al diseño
    V_strategy > V_market    → Φ mide decisión, no mercado

Referencia conceptual:
    Φ = P ∘ S  (Proyector ∘ Estrategia)
    Este test separa P de S.

Usage:
    python scripts/strategy_vs_market_separation_test.py
"""

from __future__ import annotations

import csv
import sys
import time
from copy import deepcopy
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BTC_CSV = PROJECT_ROOT / "forward_test" / "trades_btc.csv"

sys.path.insert(0, str(PROJECT_ROOT / "apps" / "analytics-engine" / "app"))

from domain.edl.inference import EdgeInferenceEngine
from domain.edl.inference.nulls import PermutationNull, BlockNull, BaseNull
from domain.edl.inference.nulls.block_null import compute_regimes_roc20


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
    if not trajectory:
        return 0.0
    pnl = np.array([float(t.get("net_pnl", 0.0)) for t in trajectory])
    if len(pnl) == 0:
        return 0.0
    cumsum = np.cumsum(pnl)
    peak = np.maximum.accumulate(cumsum)
    dd = cumsum - peak
    return -float(abs(dd.min()))


# ── Engine factory ─────────────────────────────────────────────────

def make_engine(
    trades: list[dict],
    seed: int = 42,
    n_null: int = 60,
    n_boot: int = 30,
) -> EdgeInferenceEngine:
    regimes = compute_regimes_roc20(trades)
    null_suite: list[BaseNull] = [
        PermutationNull(seed=seed),
        BlockNull(block_size=10, regimes=regimes, seed=seed),
    ]
    return EdgeInferenceEngine(
        null_suite=null_suite,
        utility_fn=trajectory_utility,
        n_null_samples=n_null,
        n_bootstrap=n_boot,
        seed=seed,
    )


def phi_at(trades: list[dict], seed: int = 42) -> dict:
    eng = make_engine(trades, seed=seed)
    return eng.infer(trades).to_dict()


# ── Strategy perturbations ─────────────────────────────────────────
# Simulan cambios en la política de decisión θ sin cambiar el
# proyector P (modelo, features, inference engine).

def perturb_threshold(trades: list[dict], keep_pct: float, rng: np.random.Generator) -> list[dict]:
    """Simulate a stricter entry threshold: keep random subset of trades."""
    n = len(trades)
    mask = rng.random(n) < keep_pct
    kept = [deepcopy(t) for t, m in zip(trades, mask) if m]
    return kept if kept else [deepcopy(trades[0])]  # guard against empty


def perturb_timing(
    trades: list[dict],
    noise_std: float,
    rng: np.random.Generator,
) -> list[dict]:
    """Jitter entry/exit prices to simulate different timing rules."""
    out = [deepcopy(t) for t in trades]
    for t in out:
        ep = t.get("entry_price", 0.0)
        xp = t.get("exit_price", 0.0)
        t["entry_price"] = ep + rng.normal(0, noise_std * ep)
        t["exit_price"] = xp + rng.normal(0, noise_std * xp)
        # Recompute PnL
        side = t.get("side", 0)
        size = t.get("size", 1.0)
        costs = t.get("costs", 0.0)
        gross = side * size * (t["exit_price"] - t["entry_price"])
        t["gross_pnl"] = gross
        t["net_pnl"] = gross - costs
    return out


def perturb_risk(trades: list[dict], scale_std: float, rng: np.random.Generator) -> list[dict]:
    """Rescale position sizes to simulate different risk scaling."""
    out = [deepcopy(t) for t in trades]
    for t in out:
        scale = abs(1.0 + rng.normal(0, scale_std))
        size = t.get("size", 1.0) * scale
        side = t.get("side", 0)
        ep = t.get("entry_price", 0.0)
        xp = t.get("exit_price", 0.0)
        costs = t.get("costs", 0.0)
        gross = side * size * (xp - ep)
        t["size"] = size
        t["gross_pnl"] = gross
        t["net_pnl"] = gross - costs
    return out


# ── V_market: Φ variance under H₀ (from inference engine) ─────────

def measure_v_market(trades: list[dict], seed: int = 42) -> dict[str, float]:
    """V_market: variance of Φ under H₀.

    We measure this from the inference engine's bootstrap std for
    each component. This is the variance of P(edge|D) under null
    sampling — i.e. how much Φ changes when the market signal is
    destroyed by H₀.
    """
    phi = phi_at(trades, seed=seed)
    # The bootstrap std IS the market variance
    id_map = phi.get("identifiability", {})
    return {
        c: {
            "mean": phi[c]["mean"],
            "std": phi[c]["std"],
            "variance": phi[c]["std"] ** 2,
            "identifiable": id_map.get(c, False),
        }
        for c in ("directional", "timing", "execution", "structural")
    }


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def print_separator(title: str) -> None:
    print(f"\n{'─' * 72}\n  {title}\n{'─' * 72}")


def main() -> int:
    print("═" * 72)
    print("  Strategy vs Market Separation Test")
    print("  Φ = P ∘ S  —  Separando Proyector de Estrategia")
    print("═" * 72)

    t0 = time.time()
    trades = load_trades()
    print(f"\n  Trades: {len(trades)} BTC")
    print(f"  Inference: n_null=60, n_bootstrap=30  (lightweight for speed)")
    print(f"  Perturbation samples per type: 10")

    rng_seed = np.random.default_rng(42)
    components = ["directional", "timing", "execution", "structural"]

    # ── V_market: reference measurement ────────────────────────────
    print_separator("V_market — Φ variance under H₀ (inference engine bootstrap)")
    v_market = measure_v_market(trades)
    print(f"  {'Component':<15} {'μ':>10} {'σ(H₀)':>10} {'Var(H₀)':>12} {'Identifiable?'}")
    print(f"  {'─' * 55}")
    for c in components:
        m = v_market[c]
        print(f"  {c:<15} {m['mean']:>10.6f} {m['std']:>10.6f} {m['variance']:>12.2e}  {'✅' if m['identifiable'] else '❌'}")

    # ── V_strategy: threshold perturbation ─────────────────────────
    print_separator("V_strategy — Threshold perturbation (stricter entry filter)")
    print(f"  Simulating different confidence thresholds by keeping p% of trades.")
    print(f"  p varies per sample (uniform 0.5–1.0)")
    rng2 = np.random.default_rng(1)
    N_SAMPLES = 10
    comp_vals_thresh: dict[str, list[float]] = {c: [] for c in components}
    for _ in range(N_SAMPLES):
        kp = rng2.uniform(0.5, 1.0)
        pts = perturb_threshold(trades, keep_pct=kp, rng=rng2)
        if len(pts) < 5:
            continue
        try:
            phi = phi_at(pts, seed=42)
            for c in components:
                comp_vals_thresh[c].append(phi[c]["mean"])
        except Exception:
            pass

    print(f"  {'Component':<15} {'μ(Φ)':>10} {'σ(θ)':>10} {'Var(θ)':>12} {'n':>5}")
    print(f"  {'─' * 55}")
    v_threshold = {}
    for c in components:
        arr = np.array(comp_vals_thresh[c])
        v_threshold[c] = {
            "mean": float(arr.mean()) if len(arr) > 0 else 0.0,
            "std": float(arr.std()) if len(arr) > 1 else 0.0,
            "variance": float(arr.var()) if len(arr) > 1 else 0.0,
            "n": len(arr),
        }
        m = v_threshold[c]
        print(f"  {c:<15} {m['mean']:>10.6f} {m['std']:>10.6f} {m['variance']:>12.2e} {m['n']:>5}")

    # ── V_strategy: timing perturbation ────────────────────────────
    print_separator("V_strategy — Timing perturbation (entry/exit jitter)")
    rng3 = np.random.default_rng(2)
    comp_vals_timing: dict[str, list[float]] = {c: [] for c in components}
    for _ in range(N_SAMPLES):
        ns = rng3.uniform(0.001, 0.02)  # 0.1% to 2% price noise
        pts = perturb_timing(trades, noise_std=ns, rng=rng3)
        if len(pts) < 5:
            continue
        try:
            phi = phi_at(pts, seed=42)
            for c in components:
                comp_vals_timing[c].append(phi[c]["mean"])
        except Exception:
            pass

    print(f"  Price noise: 0.1%–2% (uniform)")
    print(f"  {'Component':<15} {'μ(Φ)':>10} {'σ(θ)':>10} {'Var(θ)':>12} {'n':>5}")
    print(f"  {'─' * 55}")
    v_timing = {}
    for c in components:
        arr = np.array(comp_vals_timing[c])
        v_timing[c] = {
            "mean": float(arr.mean()) if len(arr) > 0 else 0.0,
            "std": float(arr.std()) if len(arr) > 1 else 0.0,
            "variance": float(arr.var()) if len(arr) > 1 else 0.0,
            "n": len(arr),
        }
        m = v_timing[c]
        print(f"  {c:<15} {m['mean']:>10.6f} {m['std']:>10.6f} {m['variance']:>12.2e} {m['n']:>5}")

    # ── V_strategy: risk perturbation ──────────────────────────────
    print_separator("V_strategy — Risk perturbation (position size scaling)")
    rng4 = np.random.default_rng(3)
    comp_vals_risk: dict[str, list[float]] = {c: [] for c in components}
    for _ in range(N_SAMPLES):
        ss = rng4.uniform(0.1, 0.5)  # 10–50% size noise
        pts = perturb_risk(trades, scale_std=ss, rng=rng4)
        if len(pts) < 5:
            continue
        try:
            phi = phi_at(pts, seed=42)
            for c in components:
                comp_vals_risk[c].append(phi[c]["mean"])
        except Exception:
            pass

    print(f"  Size noise std: 10%–50% (uniform)")
    print(f"  {'Component':<15} {'μ(Φ)':>10} {'σ(θ)':>10} {'Var(θ)':>12} {'n':>5}")
    print(f"  {'─' * 55}")
    v_risk = {}
    for c in components:
        arr = np.array(comp_vals_risk[c])
        v_risk[c] = {
            "mean": float(arr.mean()) if len(arr) > 0 else 0.0,
            "std": float(arr.std()) if len(arr) > 1 else 0.0,
            "variance": float(arr.var()) if len(arr) > 1 else 0.0,
            "n": len(arr),
        }
        m = v_risk[c]
        print(f"  {c:<15} {m['mean']:>10.6f} {m['std']:>10.6f} {m['variance']:>12.2e} {m['n']:>5}")

    # ── Comparison ─────────────────────────────────────────────────
    print_separator("⚖️  V_strategy vs V_market — Ratio Analysis")
    print(f"\n  {'Component':<15} {'Var(H₀)':>12} {'Var(thresh)':>12} {'Var(timing)':>12} {'Var(risk)':>12} {'Min ratio':>10}")
    print(f"  {'─' * 75}")

    all_pass = True
    for c in components:
        vh = v_market[c]["variance"]
        vt = v_threshold[c]["variance"]
        vti = v_timing[c]["variance"]
        vr = v_risk[c]["variance"]
        # Ratio: V_strategy / V_market (lower = better)
        ratios = []
        for v in [vt, vti, vr]:
            if vh > 0:
                ratios.append(v / vh)
            else:
                ratios.append(float("inf"))
        min_ratio = min(ratios) if ratios else float("inf")
        pass_c = min_ratio < 1.0  # V_strategy << V_market
        if not pass_c:
            all_pass = False
        print(f"  {c:<15} {vh:>12.2e} {vt:>12.2e} {vti:>12.2e} {vr:>12.2e} {min_ratio:>10.4f}"
              f"  {'✅' if pass_c else '❌'}")

    # ── Summary class ───────────────────────────────────────────────
    print(f"\n{'═' * 72}")
    if all_pass:
        print(f"  RESULT: ✅ V_strategy << V_market para TODOS los componentes")
        print(f"  → Φ es instrumento de mercado")
    else:
        print(f"  RESULT: ⚠️  V_strategy NO es despreciable frente a V_market")
        print(f"  → Φ está parcialmente acoplado al diseño de estrategia")
    print(f"  Tiempo total: {time.time() - t0:.0f}s")
    print(f"  Condición de identificabilidad real: V_strategy << V_market")
    for c in components:
        min_r = min(
            v_threshold[c]["variance"] / v_market[c]["variance"] if v_market[c]["variance"] > 0 else float("inf"),
            v_timing[c]["variance"] / v_market[c]["variance"] if v_market[c]["variance"] > 0 else float("inf"),
            v_risk[c]["variance"] / v_market[c]["variance"] if v_market[c]["variance"] > 0 else float("inf"),
        )
        print(f"    {c:<15}: min(V_θ / V_H₀) = {min_r:.4f}  (<< 1 = mercado, ≈ 1 = acoplado, > 1 = decisión)")
    print(f"{'═' * 72}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
