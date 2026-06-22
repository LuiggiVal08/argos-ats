#!/usr/bin/env python3
"""External Reference Layer — Sections 13-14 implementation.

FORMAL INVARIANTS:
1. H0 is a STOCHASTIC GENERATIVE PROCESS, not a deterministic scorer.
   Always returns a population of trajectories, never a single score.
2. H0 generates under the constraint: no predictability beyond lag-1 noise.
   Signals are permuted to break signal-market correlation.
3. P(edge_abs|D) = P(U(tau_model) > U(tau_H0)) via Monte Carlo.
   Single comparison, single process. No ensemble of tests.
4. Edge relative vs baselines is a SEPARATE computation from edge absolute.
   Never combined into a composite metric. Both reported independently.
5. This module depends on utility_measure.py and trajectory_model.py.
   It does NOT import control_layer; evaluation is separate from control.
6. Report includes BOTH p_edge_absolute and p_edge_relative.
   Never collapses them into a single verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from scripts.utility_measure import (
    UtilityMeasure,
    UtilityConfig,
    H0Generator,
    DEFAULT_CONFIG,
    load_trades_df,
)
from scripts.trajectory_model import (
    TrajectoryModel,
    DEFAULT_M_SIMULATIONS,
)

logger = logging.getLogger("external_reference")


@dataclass
class EdgeReport:
    """Complete edge report with absolute and relative measures."""
    symbol: str
    p_edge_absolute: float
    p_edge_relative: float
    utility_model: float
    utility_h0_median: float
    utility_h0_percentiles: dict
    baselines: dict
    n_trades: int
    n_h0_paths: int
    n_simulations: int
    regime: str = "global"

    def to_dict(self) -> dict:
        return asdict(self)


class ExternalReferenceLayer:
    """External Reference Layer (ERL).

    Computes P(edge_abs|D) and P(edge_rel|D) as SEPARATE quantities.
    Never combines them. Never produces a single verdict.

    This is the "tribunal" layer: it provides the external anchor
    that prevents self-referential validation loops.
    """

    def __init__(
        self,
        symbol: str,
        ohlcv: pd.DataFrame | None = None,
        config: UtilityConfig = DEFAULT_CONFIG,
        n_simulations: int = DEFAULT_M_SIMULATIONS,
        n_h0_paths: int = 10000,
    ):
        self.symbol = symbol
        self.config = config
        self.n_simulations = n_simulations
        self.n_h0_paths = n_h0_paths
        self.ohlcv = ohlcv

        self._utility = UtilityMeasure(config)
        self._trades = load_trades_df(symbol)
        self._h0_gen = H0Generator(self._trades)
        self._tm = TrajectoryModel(
            symbol=symbol,
            ohlcv=ohlcv,
            config=config,
            n_simulations=n_simulations,
        )

    def _load_ohlcv(self) -> pd.DataFrame | None:
        """Load OHLCV for baseline computation if not provided."""
        if self.ohlcv is not None:
            return self.ohlcv
        try:
            from scripts.forward_test import load_ohlcv
            return load_ohlcv(self.symbol)
        except Exception:
            logger.warning("Could not load OHLCV — baselines will use only equity data")
            return None

    def compute_edge_absolute(self) -> dict:
        """Compute P(edge_abs|D) = P(U(model) > U(H0)).

        Single comparison, single generative process.
        No ensemble of tests, no redundant nulls.
        """
        h0_paths = self._h0_gen.generate(self.n_h0_paths)

        # Evaluate model in ensemble with H0 paths
        all_paths = [self._tm.equity] + h0_paths
        all_results = self._utility.evaluate_many(all_paths)
        model_result = all_results[0]
        h0_results = all_results[1:]

        u_model = model_result["utility"]
        n_superior = sum(1 for r in h0_results if r["utility"] < u_model)
        p_edge = n_superior / len(h0_results) if h0_results else 0.0

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
            "componentes_modelo": model_result["components_raw"],
            "n_h0_paths": len(h0_paths),
            "n_model_superior": n_superior,
        }

    def compute_edge_relative(self) -> dict:
        """Compute P(edge_rel|D) against baselines.

        This is a SEPARATE computation from edge_absolute.
        Never combined into a composite metric.
        """
        ohlcv = self._load_ohlcv()
        if ohlcv is None:
            return {"baselines": {}, "p_edge_relative": None}

        from scripts.trajectory_model import compute_baseline_equity

        bh = compute_baseline_equity(ohlcv, "buy_and_hold")
        ema = compute_baseline_equity(ohlcv, "ema_cross")

        report = self._utility.compare(
            self._tm.equity, {"buy_and_hold": bh, "ema_cross": ema}
        )

        # Count how many baselines the model outperforms
        n_baselines = len(report["baselines"])
        if n_baselines > 0:
            n_superior = sum(
                1 for b in report["baselines"].values() if b["superior"]
            )
            p_rel = n_superior / n_baselines
        else:
            p_rel = 0.0

        report["p_edge_relative"] = round(p_rel, 4)
        return report

    def compute_h_externo(self, edge_abs_result: dict | None = None) -> float:
        """Compute external hazard for control layer.

        h_externo = P(U(H0) > U(model) | D)
                  = 1 - P(edge_abs | D)

        This is the ONLY external signal the control layer receives.
        """
        if edge_abs_result is None:
            edge_abs_result = self.compute_edge_absolute()
        return round(1.0 - edge_abs_result["p_edge_absolute"], 4)

    def full_report(self) -> EdgeReport:
        """Generate complete external reference report.

        Returns BOTH p_edge_absolute and p_edge_relative independently.
        """
        edge_abs = self.compute_edge_absolute()
        edge_rel = self.compute_edge_relative()

        return EdgeReport(
            symbol=self.symbol,
            p_edge_absolute=edge_abs["p_edge_absolute"],
            p_edge_relative=edge_rel.get("p_edge_relative", 0.0),
            utility_model=edge_abs["utility_model"],
            utility_h0_median=edge_abs["utility_h0_median"],
            utility_h0_percentiles=edge_abs["utility_h0_percentiles"],
            baselines=edge_rel.get("baselines", {}),
            n_trades=len(self._trades),
            n_h0_paths=self.n_h0_paths,
            n_simulations=self.n_simulations,
        )

    def reset(self):
        """Re-initialize with fresh data."""
        self._trades = load_trades_df(self.symbol)
        self._h0_gen = H0Generator(self._trades)
        self._tm = TrajectoryModel(
            symbol=self.symbol,
            ohlcv=self.ohlcv,
            config=self.config,
            n_simulations=self.n_simulations,
        )
