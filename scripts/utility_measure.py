#!/usr/bin/env python3
"""Utility Measure Specification — Section 14 implementation.

FORMAL INVARIANTS:
1. U(tau) is FUNCTIONAL: same function applied to model, baselines, and H0.
   No branching or conditional logic based on input source.
2. U(tau) is SYMMETRIC: identical computation for every trajectory,
   regardless of origin (model / baseline / H0).
3. U(tau) is INVARIANT TO IMPLEMENTATION: depends only on equity curve.
   No access to features, model state, signals, or system internals.
4. UtilityConfig alpha weights are FIXED for the duration of the experiment.
   Not learned, not regime-dependent, not calibrated online.
5. H0Generator generates STOCHASTIC trajectory populations, not scores.
   Never called as deterministic function; always returns distribution.
6. No cross-layer communication: this module does not import trajectory_model,
   control_layer, or external_reference. Zero knowledge of other layers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("utility_measure")

# ── Constants ──────────────────────────────────────────────────────────────
TRADING_DAYS_PER_YEAR = 252
HOURS_PER_DAY = 24
HOURS_PER_YEAR = TRADING_DAYS_PER_YEAR * HOURS_PER_DAY
PROJECT_ROOT = Path(__file__).parent.parent
FT_DIR = PROJECT_ROOT / "forward_test"
INITIAL_CAPITAL = 100_000.0


# ── Config ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class UtilityConfig:
    """Fixed alpha weights for U(tau) functional.

    Invariant: these weights are FIXED for the experiment duration.
    Not learned, not regime-dependent, not calibrated online.
    """
    alpha_return: float = 1.0
    alpha_drawdown: float = 2.0
    alpha_volatility: float = 0.5
    alpha_tail_loss: float = 1.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.alpha_return, self.alpha_drawdown,
                self.alpha_volatility, self.alpha_tail_loss)


DEFAULT_CONFIG = UtilityConfig()


# ── U(tau) Functional ──────────────────────────────────────────────────────
def compute_cagr(equity: pd.Series) -> float:
    """Compound Annual Growth Rate from daily equity curve."""
    if len(equity) < 2:
        return 0.0
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return 0.0
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def compute_max_dd(equity: pd.Series) -> float:
    """Maximum drawdown from peak, as positive fraction."""
    if len(equity) < 2:
        return 0.0
    peak = equity.expanding().max()
    dd = (equity - peak) / peak
    return float(abs(dd.min()))


def compute_volatility(equity: pd.Series) -> float:
    """Annualized volatility of daily returns."""
    if len(equity) < 5:
        return 0.0
    daily_returns = equity.pct_change().dropna()
    if len(daily_returns) < 2:
        return 0.0
    return float(daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def compute_cvar95(equity: pd.Series) -> float:
    """Conditional VaR at 95%: mean of worst 5% daily returns, positive."""
    if len(equity) < 20:
        return 0.0
    daily_returns = equity.pct_change().dropna()
    if len(daily_returns) < 20:
        return 0.0
    threshold = np.percentile(daily_returns, 5)
    tail = daily_returns[daily_returns <= threshold]
    if len(tail) == 0:
        return 0.0
    return float(abs(tail.mean()))


def compute_components(equity: pd.Series) -> dict[str, float]:
    """Compute all four U(tau) components from daily equity curve."""
    return {
        "cagr": compute_cagr(equity),
        "max_dd": compute_max_dd(equity),
        "volatility": compute_volatility(equity),
        "cvar95": compute_cvar95(equity),
    }


def resample_equity_daily(equity: pd.Series) -> pd.Series:
    """Resample hourly equity curve to daily (last value per day)."""
    if equity.index.dtype.kind == "M":
        return equity.resample("D").last().dropna()
    return equity


def normalize_components(
    components_list: list[dict[str, float]],
) -> list[dict[str, float]]:
    """Min-max normalize each component across an ensemble of trajectories."""
    if not components_list:
        return components_list

    keys = ["cagr", "max_dd", "volatility", "cvar95"]
    arr = np.array([[c[k] for k in keys] for c in components_list])
    mins = arr.min(axis=0)
    maxs = arr.max(axis=0)
    ranges = maxs - mins
    ranges[ranges < 1e-10] = 1.0

    normalized = (arr - mins) / ranges
    result = []
    for row in normalized:
        result.append(dict(zip(keys, row)))
    return result


def evaluate_u(
    equity: pd.Series,
    config: UtilityConfig = DEFAULT_CONFIG,
    ensemble: list[pd.Series] | None = None,
) -> dict:
    """Compute U(tau) for a single equity curve.

    If ensemble is provided, normalizes components across the ensemble.
    If not, uses raw (unnormalized) components with a warning.
    """
    daily_equity = resample_equity_daily(equity)
    raw = compute_components(daily_equity)

    if ensemble is not None:
        all_raw = [compute_components(resample_equity_daily(e)) for e in ensemble]
        all_raw.append(raw)
        normed = normalize_components(all_raw)
        comp = normed[-1]
    else:
        logger.warning("No ensemble provided — using unnormalized components. "
                       "U(tau) will not be comparable across simulations.")
        comp = raw

    u = (config.alpha_return * comp["cagr"]
         - config.alpha_drawdown * comp["max_dd"]
         - config.alpha_volatility * comp["volatility"]
         - config.alpha_tail_loss * comp["cvar95"])

    return {
        "utility": u,
        "components_raw": raw,
        "components_norm": comp,
        "config": asdict(config),
    }


# ── H0 Generator ───────────────────────────────────────────────────────────
def load_trades_df(symbol: str) -> pd.DataFrame:
    """Load trades from forward test CSV."""
    path = FT_DIR / f"trades_{symbol.lower()}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Trades not found: {path}")
    df = pd.read_csv(path)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"])
    df["exit_ts"] = pd.to_datetime(df["exit_ts"])
    # Normalize side: "LONG"/"SHORT" strings OR numeric ±1
    if "side" in df.columns:
        side_strs = df["side"].astype(str).str.strip().str.upper()
        df["side"] = np.where(side_strs == "LONG", 1,
                              np.where(side_strs == "SHORT", -1,
                                       pd.to_numeric(df["side"], errors="coerce")))
    # Ensure remaining numeric columns (CSV may store some as strings)
    for col in ["entry_price", "exit_price", "size", "gross_pnl",
                "costs", "net_pnl", "duration_bars"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("entry_ts").reset_index(drop=True)
    return df


def load_equity_df(symbol: str) -> pd.DataFrame:
    """Load equity curve from forward test CSV."""
    path = FT_DIR / f"equity_{symbol.lower()}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Equity not found: {path}")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def equity_from_trades(trades: pd.DataFrame,
                       initial_capital: float = INITIAL_CAPITAL) -> pd.Series:
    """Reconstruct equity curve step-function from trade list.

    Returns Series indexed by exit timestamp, cumulative equity after each trade.
    """
    if len(trades) == 0:
        return pd.Series([initial_capital], index=[pd.Timestamp.now()])

    equity = initial_capital + trades["net_pnl"].cumsum()
    timestamps = trades["exit_ts"]
    series = pd.Series(equity.values, index=timestamps)
    series.index.name = "exit_ts"
    return series


def compute_noise_pnl(trade_row: pd.Series, new_side: int) -> float:
    """Compute net PnL for a trade with a different side.

    new_gross_pnl = new_side * original_side * original_gross_pnl
    net_pnl = new_gross_pnl - costs
    """
    original_side = trade_row["side"]
    original_gross = trade_row["gross_pnl"]
    costs = trade_row["costs"]
    new_gross = new_side * original_side * original_gross
    return new_gross - costs


def generate_h0_trajectory(trades_df: pd.DataFrame,
                           rng: np.random.Generator,
                           initial_capital: float = INITIAL_CAPITAL) -> pd.Series:
    """Generate a single H0 trajectory by permuting trade sides.

    Invariant: H0 preserves trade frequency, size distribution, entry/exit
    timing, and cost structure. Only breaks signal-market correlation by
    randomly reassigning BUY/SELL direction.

    Vectorized implementation (no apply/lambda for performance).
    """
    if len(trades_df) == 0:
        return pd.Series([initial_capital], index=[pd.Timestamp.now()])

    n = len(trades_df)
    new_sides = rng.choice(np.array([1, -1]), size=n)
    original_side = trades_df["side"].values
    original_gross = trades_df["gross_pnl"].values
    costs = trades_df["costs"].values

    new_gross = new_sides * original_side * original_gross
    net_pnl = new_gross - costs

    equity = initial_capital + np.cumsum(net_pnl)
    return pd.Series(equity, index=trades_df["exit_ts"].values, name="equity")


class H0Generator:
    """Generates H0 (no-edge) trajectory population.

    H0 is a stochastic generative process, not a deterministic scorer.
    Always returns a population of trajectories, never a single score.

    Invariant: this is a GENERATOR, not an evaluator.
    It produces populations; U(tau) evaluation is handled by UtilityMeasure.
    """

    def __init__(self, trades_df: pd.DataFrame):
        self._trades = trades_df.sort_values("entry_ts").reset_index(drop=True)
        self._n_trades = len(self._trades)
        if self._n_trades > 0:
            self._trade_dates = pd.date_range(
                start=self._trades["entry_ts"].min(),
                end=self._trades["exit_ts"].max(),
                freq="D",
            )

    @property
    def n_trades(self) -> int:
        return self._n_trades

    @property
    def date_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        if self._n_trades == 0:
            return (pd.Timestamp.now(), pd.Timestamp.now())
        return (self._trades["entry_ts"].min(), self._trades["exit_ts"].max())

    def generate(self, n_paths: int = 10000,
                 rng: np.random.Generator | None = None) -> list[pd.Series]:
        """Generate M H0 trajectories.

        Returns list of equity curve Series (one per simulated trajectory).
        """
        if rng is None:
            rng = np.random.default_rng(42)

        trajectories = []
        for _ in range(n_paths):
            eq = generate_h0_trajectory(self._trades, rng)
            trajectories.append(eq)

        return trajectories


# ── UtilityMeasure Class ────────────────────────────────────────────────────
class UtilityMeasure:
    """Computes U(tau) for any trajectory.

    Symmetric: applies the SAME function regardless of input origin.
    Functional: depends only on equity curve, not on system internals.
    """

    def __init__(self, config: UtilityConfig = DEFAULT_CONFIG):
        self.config = config

    def evaluate(self, equity: pd.Series,
                 ensemble: list[pd.Series] | None = None) -> dict:
        """Compute U(tau) for a single equity curve."""
        return evaluate_u(equity, config=self.config, ensemble=ensemble)

    def evaluate_many(self,
                      trajectories: list[pd.Series]) -> list[dict]:
        """Compute U(tau) for multiple trajectories with shared normalization."""
        if not trajectories:
            return []

        all_raw = [compute_components(resample_equity_daily(eq))
                   for eq in trajectories]
        normed = normalize_components(all_raw)

        results = []
        for i, (traj, comp) in enumerate(zip(trajectories, normed)):
            raw = all_raw[i]
            u = (self.config.alpha_return * comp["cagr"]
                 - self.config.alpha_drawdown * comp["max_dd"]
                 - self.config.alpha_volatility * comp["volatility"]
                 - self.config.alpha_tail_loss * comp["cvar95"])
            results.append({
                "utility": u,
                "components_raw": raw,
                "components_norm": comp,
            })
        return results

    def compare(self, model_equity: pd.Series,
                baselines: dict[str, pd.Series],
                h0_equity: pd.Series | None = None) -> dict:
        """Compare model utility against baselines and H0 in the same space."""
        ensemble = [model_equity] + list(baselines.values())
        if h0_equity is not None:
            ensemble.append(h0_equity)

        results = self.evaluate_many(ensemble)
        model_result = results[0]

        report = {
            "utility_model": round(model_result["utility"], 4),
            "components": model_result["components_raw"],
            "baselines": {},
        }

        for i, (name, _) in enumerate(baselines.items(), start=1):
            report["baselines"][name] = {
                "utility": round(results[i]["utility"], 4),
                "superior": bool(results[i]["utility"] < model_result["utility"]),
                "gap": round(model_result["utility"] - results[i]["utility"], 4),
            }

        if h0_equity is not None:
            h0_result = results[-1]
            report["h0"] = {
                "utility": round(h0_result["utility"], 4),
                "superior": bool(h0_result["utility"] < model_result["utility"]),
                "gap": round(model_result["utility"] - h0_result["utility"], 4),
            }

        return report
