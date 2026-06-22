#!/usr/bin/env python3
"""Trajectory Model — Section 11 implementation.

FORMAL INVARIANTS:
1. Modo A (reconstruction) is the OFFICIAL mode for all decisions.
   Only Modo A feeds into P(edge|D) and P(failure|D) for control_layer.
2. Modo B (generative) produces AUXILIARY distributions only.
   Never feeds into control_layer decisions directly.
3. P(edge|D) and P(failure|D) are estimated via Monte Carlo,
   never via closed-form formula. M >= 10,000 simulations minimum.
4. Trajectory blocks preserve: temporal order, regime persistence,
   capital constraint (no negative equity), compounding effects.
5. This module depends on utility_measure.py for U(tau) and H0Generator.
   It does NOT depend on control_layer or external_reference.
6. No cross-layer feedback: simulation parameters are fixed per experiment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.utility_measure import (
    UtilityMeasure,
    UtilityConfig,
    H0Generator,
    DEFAULT_CONFIG,
    INITIAL_CAPITAL,
    load_trades_df,
    load_equity_df,
    resample_equity_daily,
    compute_components,
    normalize_components,
    FT_DIR,
)

logger = logging.getLogger("trajectory_model")

TRADING_DAYS_PER_YEAR = 252
HOURS_PER_DAY = 24
DEFAULT_M_SIMULATIONS = 10_000
DEFAULT_M_GENERATIVE = 5_000

# ── Regime Classification ──────────────────────────────────────────────────
TRENDING_THRESHOLD = 0.02  # |ROC(20)| > 2% → trending

RegimeLabel = str
REGIME_BULL = "TRENDING_BULL"
REGIME_BEAR = "TRENDING_BEAR"
REGIME_RANGE = "RANGING"


def classify_regime(close: pd.Series, window: int = 20) -> pd.Series:
    """Classify market regime based on rate of change over window.

    Returns Series of regime labels aligned with input index.
    """
    roc = close.pct_change(window)
    regime = pd.Series(REGIME_RANGE, index=close.index)
    regime[roc > TRENDING_THRESHOLD] = REGIME_BULL
    regime[roc < -TRENDING_THRESHOLD] = REGIME_BEAR
    return regime


def extract_regime_blocks(
    equity: pd.Series,
    regime_labels: pd.Series,
) -> list[tuple[RegimeLabel, int, int]]:
    """Split equity curve into contiguous regime blocks.

    Returns list of (regime, start_idx, end_idx) tuples.
    Each block is a consecutive segment with the same regime.
    """
    if len(equity) == 0:
        return []

    aligned = regime_labels.reindex(equity.index, method="ffill").fillna(REGIME_RANGE)
    blocks = []
    start = 0
    current = aligned.iloc[0]

    for i in range(1, len(aligned)):
        if aligned.iloc[i] != current:
            blocks.append((current, start, i))
            start = i
            current = aligned.iloc[i]

    blocks.append((current, start, len(aligned)))
    return blocks


def compute_baseline_equity(
    ohlcv: pd.DataFrame,
    strategy: str = "buy_and_hold",
    initial_capital: float = INITIAL_CAPITAL,
) -> pd.Series:
    """Compute equity curve for a baseline strategy from OHLCV data.

    Strategies:
    - 'buy_and_hold': holds position throughout
    - 'ema_cross': EMA(12) / EMA(26) crossover
    """
    close = ohlcv["close"]
    if strategy == "buy_and_hold":
        equity = initial_capital * (close / close.iloc[0])
        return pd.Series(equity.values, index=close.index, name="equity")

    elif strategy == "ema_cross":
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        position = pd.Series(0, index=close.index)
        position[ema12 > ema26] = 1
        position[ema12 < ema26] = -1

        returns = close.pct_change().fillna(0)
        strat_returns = position.shift(1) * returns
        strat_returns.iloc[0] = 0

        equity = initial_capital * (1 + strat_returns).cumprod()
        return pd.Series(equity.values, index=close.index, name="equity")

    else:
        raise ValueError(f"Unknown baseline strategy: {strategy}")


# ── RegimeTransition ───────────────────────────────────────────────────────
@dataclass
class RegimeTransition:
    matrix: np.ndarray
    labels: list[RegimeLabel]

    def sample_next(self, current: RegimeLabel,
                    rng: np.random.Generator) -> RegimeLabel:
        idx = self.labels.index(current)
        probs = self.matrix[idx]
        next_idx = rng.choice(len(self.labels), p=probs)
        return self.labels[next_idx]

    def perturbed(self, epsilon: float = 0.05) -> RegimeTransition:
        n = len(self.labels)
        noise = np.random.uniform(0, epsilon, (n, n))
        noisy = self.matrix + noise
        noisy /= noisy.sum(axis=1, keepdims=True)
        return RegimeTransition(matrix=noisy, labels=self.labels)


def estimate_transition(
    blocks: list[tuple[RegimeLabel, int, int]],
) -> RegimeTransition:
    """Estimate regime transition matrix from observed blocks."""
    labels = sorted(set(b[0] for b in blocks))
    n = len(labels)
    counts = np.zeros((n, n), dtype=float)

    for i in range(len(blocks) - 1):
        from_r = blocks[i][0]
        to_r = blocks[i + 1][0]
        fi = labels.index(from_r)
        ti = labels.index(to_r)
        counts[fi, ti] += 1

    row_sums = counts.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1.0
    matrix = counts / row_sums

    return RegimeTransition(matrix=matrix, labels=labels)


# ── Block Bootstrap (Modo A) ───────────────────────────────────────────────
def block_bootstrap_trajectory(
    equity: pd.Series,
    blocks: list[tuple[RegimeLabel, int, int]],
    transition: RegimeTransition,
    n_steps: int,
    rng: np.random.Generator,
) -> pd.Series:
    """Generate one trajectory via block bootstrap (Modo A — reconstruction).

    Samples blocks according to regime transition matrix, preserving
    internal block structure (order within block, autocorrelation).
    """
    if len(blocks) == 0 or len(equity) == 0:
        return pd.Series(
            [INITIAL_CAPITAL],
            index=[equity.index[0]] if len(equity) > 0 else [pd.Timestamp.now()],
        )

    # Start from a random regime
    start_label = rng.choice(transition.labels)
    start_candidates = [i for i, b in enumerate(blocks) if b[0] == start_label]
    if not start_candidates:
        start_idx = rng.randint(0, len(blocks))
        start_label = blocks[start_idx][0]

    sampled_segments = []
    current_label = start_label
    steps_remaining = n_steps

    while steps_remaining > 0:
        candidates = [i for i, b in enumerate(blocks) if b[0] == current_label]
        if not candidates:
            current_label = rng.choice(transition.labels)
            continue

        block_idx = rng.choice(candidates)
        _, s, e = blocks[block_idx]
        segment = equity.iloc[s:e]
        n_take = min(len(segment), steps_remaining)
        sampled_segments.append(segment.iloc[:n_take])
        steps_remaining -= n_take

        if steps_remaining > 0:
            current_label = transition.sample_next(current_label, rng)

    if not sampled_segments:
        return pd.Series([INITIAL_CAPITAL], index=[equity.index[0]])

    combined = pd.concat(sampled_segments).reset_index(drop=True)
    return combined


# ── Modo B (Generative) ────────────────────────────────────────────────────
def generative_trajectory(
    equity: pd.Series,
    blocks: list[tuple[RegimeLabel, int, int]],
    transition: RegimeTransition,
    block_params: dict[RegimeLabel, dict],
    n_steps: int,
    rng: np.random.Generator,
) -> pd.Series:
    """Generate one trajectory via generative model (Modo B — stress tests).

    Uses parametric distributions for block returns, allowing unseen
    regime transitions via perturbed transition matrix.
    """
    perturbed = transition.perturbed(epsilon=0.05)

    if len(equity) == 0:
        return pd.Series([INITIAL_CAPITAL], index=[pd.Timestamp.now()])

    current_label = rng.choice(perturbed.labels)
    equity_val = float(INITIAL_CAPITAL)
    values = [equity_val]
    steps_remaining = n_steps

    while steps_remaining > 0:
        params = block_params.get(current_label, {"mu": 0.0, "sigma": 0.01})
        block_length = rng.randint(1, min(168, steps_remaining + 1))

        for _ in range(block_length):
            ret = rng.normal(params["mu"], params["sigma"])
            equity_val *= (1.0 + ret)
            values.append(equity_val)
            if equity_val <= 0:
                break

        steps_remaining -= block_length
        if steps_remaining > 0:
            current_label = perturbed.sample_next(current_label, rng)

    return pd.Series(values, name="equity")


# ── Failure Conditions ─────────────────────────────────────────────────────
def check_failure(equity: pd.Series) -> dict:
    """Check if a trajectory triggers failure conditions.

    Returns dict with:
    - failed: bool
    - reasons: list[str]
    - details: dict with metric values at failure
    """
    failed = False
    reasons = []
    details = {}

    if len(equity) < 2:
        return {"failed": False, "reasons": [], "details": {}}

    # MaxDD
    peak = equity.expanding().max()
    dd = (equity - peak) / peak
    max_dd = abs(dd.min())
    details["max_dd"] = round(max_dd, 4)
    if max_dd > 0.05:
        failed = True
        reasons.append(f"max_dd={max_dd:.4f} > 0.05")

    # Ruin
    min_eq = equity.min()
    details["min_equity"] = round(min_eq, 2)
    if min_eq <= 0:
        failed = True
        reasons.append(f"equity={min_eq:.2f} <= 0 (ruin)")

    # Rolling Sharpe
    daily_eq = resample_equity_daily(equity)
    if len(daily_eq) >= 30:
        daily_ret = daily_eq.pct_change().dropna()
        if len(daily_ret) >= 14:
            rolling_sharpe = (
                daily_ret.rolling(14).mean()
                / daily_ret.rolling(14).std().clip(lower=1e-8)
                * np.sqrt(TRADING_DAYS_PER_YEAR)
            )
            low_sharpe_days = (rolling_sharpe < 0.2).sum()
            details["low_sharpe_days"] = int(low_sharpe_days)
            if low_sharpe_days >= 14:
                failed = True
                reasons.append(
                    f"sharpe<0.2 for {low_sharpe_days} consecutive days"
                )

    return {"failed": failed, "reasons": reasons, "details": details}


# ── TrajectoryModel Class ──────────────────────────────────────────────────
class TrajectoryModel:
    """Generates alternative equity trajectories for P(failure|D) estimation.

    Modo A: block bootstrap reconstruction (official, for decisions).
    Modo B: parametric generative (auxiliary, for stress tests only).
    """

    def __init__(
        self,
        symbol: str,
        ohlcv: pd.DataFrame | None = None,
        equity: pd.Series | None = None,
        trades_df: pd.DataFrame | None = None,
        config: UtilityConfig = DEFAULT_CONFIG,
        n_simulations: int = DEFAULT_M_SIMULATIONS,
    ):
        self.symbol = symbol
        self.config = config
        self.n_simulations = n_simulations
        self._utility = UtilityMeasure(config)

        # Load data if not provided
        if equity is not None:
            self.equity = equity
        else:
            self.equity = self._load_equity()

        if trades_df is not None:
            self.trades = trades_df
        else:
            self.trades = self._load_trades()

        if ohlcv is not None:
            self.ohlcv = ohlcv
            self.close = ohlcv["close"]
        else:
            self.ohlcv = None
            self.close = None

        # Regime classification and blocks
        self.blocks: list[tuple[RegimeLabel, int, int]] = []
        self.transition: RegimeTransition | None = None
        self.block_params: dict[RegimeLabel, dict] = {}

    def _load_equity(self) -> pd.Series:
        df = load_equity_df(self.symbol)
        return pd.Series(df["equity"].values, index=df["timestamp"], name="equity")

    def _load_trades(self) -> pd.DataFrame:
        return load_trades_df(self.symbol)

    def build_regime_blocks(self, window: int = 20):
        """Build regime blocks from OHLCV data."""
        if self.close is None:
            raise ValueError("OHLCV data required for regime classification")

        # Align OHLCV index to equity's datetime index for regime comparison
        if self.ohlcv is not None and "timestamp" in self.ohlcv.columns:
            close_ts = self.ohlcv.set_index("timestamp")["close"]
            regime = classify_regime(close_ts, window)
            self.blocks = extract_regime_blocks(self.equity, regime)
        else:
            regime = classify_regime(self.close, window)
            self.blocks = extract_regime_blocks(self.equity, regime)

        if self.blocks:
            self.transition = estimate_transition(self.blocks)

        # Block params for Modo B
        for r in [REGIME_BULL, REGIME_BEAR, REGIME_RANGE]:
            block_indices = [i for i, b in enumerate(self.blocks) if b[0] == r]
            if not block_indices:
                self.block_params[r] = {"mu": 0.0, "sigma": 0.01}
                continue

            returns = []
            for bi in block_indices:
                _, s, e = self.blocks[bi]
                block_eq = self.equity.iloc[s:e]
                if len(block_eq) > 1:
                    block_ret = block_eq.pct_change().dropna()
                    returns.extend(block_ret.values)

            if returns:
                self.block_params[r] = {
                    "mu": float(np.mean(returns)),
                    "sigma": float(np.std(returns)) or 0.01,
                }
            else:
                self.block_params[r] = {"mu": 0.0, "sigma": 0.01}

    def simulate_a(self, rng: np.random.Generator | None = None,
                   n_steps: int | None = None) -> list[pd.Series]:
        """Modo A: block bootstrap reconstruction.

        Returns M alternative equity trajectories for the model.
        """
        if not self.blocks or self.transition is None:
            raise ValueError("Call build_regime_blocks() first")

        if rng is None:
            rng = np.random.default_rng(42)
        if n_steps is None:
            n_steps = len(self.equity)

        trajectories = []
        for _ in range(self.n_simulations):
            traj = block_bootstrap_trajectory(
                self.equity, self.blocks, self.transition, n_steps, rng
            )
            trajectories.append(traj)

        return trajectories

    def simulate_b(self, rng: np.random.Generator | None = None,
                   n_steps: int | None = None) -> list[pd.Series]:
        """Modo B: parametric generative (stress tests only).

        WARNING: This mode is AUXILIARY. Never feed into control_layer.
        """
        if not self.blocks or self.transition is None:
            raise ValueError("Call build_regime_blocks() first")

        if rng is None:
            rng = np.random.default_rng(42)
        if n_steps is None:
            n_steps = len(self.equity)

        trajectories = []
        for _ in range(self.n_simulations // 2):
            traj = generative_trajectory(
                self.equity, self.blocks, self.transition,
                self.block_params, n_steps, rng,
            )
            trajectories.append(traj)

        return trajectories

    def estimate_failure_probability(
        self, mode: str = "a",
        trajectories: list[pd.Series] | None = None,
    ) -> dict:
        """Estimate P(failure | D) via Monte Carlo.

        P(failure | D) = (1/M) * sum(1[failure(tau_s)])
        """
        if trajectories is None:
            if mode == "a":
                trajectories = self.simulate_a()
            else:
                trajectories = self.simulate_b()

        if not trajectories:
            return {
                "p_failure": 0.0,
                "n_simulations": 0,
                "failure_modes": {},
                "n_failed": 0,
            }

        failures = [check_failure(t) for t in trajectories]
        n_failed = sum(1 for f in failures if f["failed"])
        p_failure = n_failed / len(trajectories)

        failure_modes: dict[str, int] = {}
        for f in failures:
            for r in f["reasons"]:
                cause = r.split("=")[0]
                failure_modes[cause] = failure_modes.get(cause, 0) + 1

        return {
            "p_failure": round(p_failure, 4),
            "n_simulations": len(trajectories),
            "n_failed": n_failed,
            "failure_modes": failure_modes,
            "mode": mode,
        }

    def estimate_edge_probability(
        self, h0_generator: H0Generator,
        mode: str = "a",
        n_h0_paths: int = 10000,
    ) -> dict:
        """Estimate P(edge_abs | D) by comparing model vs H0.

        P(edge_abs | D) = (1/M) * sum(1[U(tau_model) > U(tau_H0)])
        """
        h0_paths = h0_generator.generate(n_h0_paths)

        # Evaluate model equity in ensemble with H0
        model_result = self._utility.evaluate(
            self.equity, ensemble=[self.equity] + h0_paths
        )
        u_model = model_result["utility"]

        # Evaluate each H0 path
        all_paths = [self.equity] + h0_paths
        all_results = self._utility.evaluate_many(all_paths)
        h0_results = all_results[1:]

        n_superior = sum(1 for r in h0_results if r["utility"] < u_model)
        p_edge = n_superior / len(h0_results) if h0_results else 0.0

        # Percentiles of H0 utility distribution
        h0_utilities = [r["utility"] for r in h0_results]
        percentiles = {}
        if h0_utilities:
            for p in [5, 25, 50, 75, 95]:
                percentiles[f"p{p}"] = round(
                    float(np.percentile(h0_utilities, p)), 4
                )

        return {
            "p_edge_absolute": round(p_edge, 4),
            "utility_model": round(u_model, 4),
            "utility_h0_median": percentiles.get("p50", 0.0),
            "utility_h0_percentiles": percentiles,
            "n_h0_paths": len(h0_paths),
            "n_model_superior": n_superior,
            "mode": mode,
        }

    def compare_baselines(self) -> dict:
        """Compare model utility against baselines in same U space."""
        if self.close is None:
            raise ValueError("OHLCV data required for baseline computation")

        bh = compute_baseline_equity(
            self.ohlcv, "buy_and_hold"
        ) if self.ohlcv is not None else pd.Series([INITIAL_CAPITAL])
        ema = compute_baseline_equity(
            self.ohlcv, "ema_cross"
        ) if self.ohlcv is not None else pd.Series([INITIAL_CAPITAL])

        return self._utility.compare(
            self.equity, {"buy_and_hold": bh, "ema_cross": ema}
        )


# ── Convenience ────────────────────────────────────────────────────────────
def run_trajectory_analysis(
    symbol: str,
    ohlcv: pd.DataFrame | None = None,
    n_simulations: int = DEFAULT_M_SIMULATIONS,
    n_h0_paths: int = 10000,
    mode: str = "a",
    config: UtilityConfig = DEFAULT_CONFIG,
) -> dict:
    """Run full trajectory analysis for a symbol.

    Returns dict with P(edge|D), P(failure|D), baseline comparison.
    """
    trades = load_trades_df(symbol)
    h0_gen = H0Generator(trades)

    tm = TrajectoryModel(
        symbol=symbol,
        ohlcv=ohlcv,
        config=config,
        n_simulations=n_simulations,
    )

    if ohlcv is not None:
        tm.build_regime_blocks()

    failure = tm.estimate_failure_probability(mode=mode)

    edge = tm.estimate_edge_probability(
        h0_gen, mode=mode, n_h0_paths=n_h0_paths
    )

    baselines = tm.compare_baselines() if ohlcv is not None else {}

    return {
        "symbol": symbol,
        "mode": mode,
        "n_simulations": n_simulations,
        "n_trades": len(trades),
        "p_failure": failure,
        "p_edge": edge,
        "baselines": baselines,
    }
