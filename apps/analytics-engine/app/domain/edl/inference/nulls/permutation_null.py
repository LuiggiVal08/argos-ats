"""H₀₁ — Permutation Null (directional null).

Transformation: permute trade side labels while preserving all other
trade-level attributes (entry time, exit time, size, costs).

Spec reference: Section 14.3 (existing), Section 18.4(1).
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from .base_null import BaseNull


class PermutationNull(BaseNull):
    """H₀₁: side-permutation null.

    Preserves:
    - Trade timing and sequencing
    - Position sizing
    - Cost structure
    - Marginal distributions

    Breaks:
    - Directional signal alignment
    """

    def __init__(self, seed: int | None = None) -> None:
        self._seed = seed

    def name(self) -> str:
        return "permutation_null"

    def transform(self, trajectory: Any, rng: Any = None) -> Any:
        if rng is None:
            rng = np.random.default_rng(self._seed)

        trades = list(trajectory)
        n = len(trades)
        if n == 0:
            return []

        # Permute sides, preserving everything else
        permuted = copy.deepcopy(trades)
        sides = np.array([t.get("side", 0) for t in permuted])
        rng.shuffle(sides)

        for trade, new_side in zip(permuted, sides):
            trade["side"] = new_side
            # Recompute net PnL: flip gross by side, keep costs
            original_gross = trade.get("gross_pnl", 0.0)
            costs = trade.get("costs", 0.0)
            # original sign direction is already encoded in gross_pnl
            # (gross_pnl already had sign determined by original side).
            # We recompute net preserving direction vs the NEW side.
            trade["net_pnl"] = new_side * abs(original_gross) - costs

        return permuted
