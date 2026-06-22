"""H₀₃ — Execution Noise Null.

Transformation: inject synthetic execution noise (slippage, latency,
cost distortion) while preserving net PnL expectation.

Spec reference: Section 18.4(4).
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from .base_null import BaseNull


class ExecutionNoiseNull(BaseNull):
    """H₀₃: execution-noise injection null.

    Adds zero-mean noise to trade returns and costs, simulating
    what the model would achieve under degraded execution conditions.
    Preserves net PnL expectation while destroying fine-grained
    signal consistency.

    Preserves:
    - Net PnL expectation (sum of noise ≈ 0)
    - Cost structure (base costs preserved, noise added)
    - Regime path

    Breaks:
    - Fine-grained return consistency
    - Entry/exit precision advantage
    """

    def __init__(
        self,
        slippage_std: float = 0.001,
        cost_noise_std: float = 0.1,
        seed: int | None = None,
    ) -> None:
        if slippage_std < 0:
            raise ValueError(f"slippage_std must be >= 0, got {slippage_std}")
        if cost_noise_std < 0:
            raise ValueError(f"cost_noise_std must be >= 0, got {cost_noise_std}")
        self._slippage_std = slippage_std
        self._cost_noise_std = cost_noise_std
        self._seed = seed

    def name(self) -> str:
        return "execution_noise_null"

    def transform(self, trajectory: Any, rng: Any = None) -> Any:
        if rng is None:
            rng = np.random.default_rng(self._seed)

        trades = list(trajectory)
        if not trades:
            return []

        result = copy.deepcopy(trades)
        n = len(result)

        # Generate zero-mean noise for gross PnL and costs
        gross_noise = rng.normal(0, self._slippage_std, size=n)
        cost_noise = rng.normal(0, self._cost_noise_std, size=n)

        # Force zero-mean constraint (preserve net PnL expectation)
        gross_noise -= gross_noise.mean()
        cost_noise -= cost_noise.mean()

        for i, trade in enumerate(result):
            trade["gross_pnl"] = trade.get("gross_pnl", 0.0) + gross_noise[i]
            trade["costs"] = max(0.0, trade.get("costs", 0.0) + cost_noise[i])
            trade["net_pnl"] = trade["gross_pnl"] - trade["costs"]

        return result
