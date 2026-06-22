"""H₀₂ — Block-Structured Null (regime-aware).

Destroys global sequencing while preserving local structure.

Two modes:
    1. FIXED-SIZE blocks: divide trajectory into explicit-size chunks
       and permute them. Tests: does global order matter?

    2. REGIME-CONSISTENT blocks: group contiguous trades sharing the
       same regime label, then permute blocks. Tests: does the
       regime-to-regime transition sequence carry signal?

Regime labels can be pre-computed externally (e.g. via ROC(20) on
equity curve) and passed via the `regimes` parameter. This keeps
the regime detection OBSERVABLE and AUDITABLE — never hidden inside
the null operator.

Spec reference: Section 18.4(2).
"""

from __future__ import annotations

import copy
from typing import Any

import numpy as np

from .base_null import BaseNull


# ── Regime detection (ROC(20)) ──────────────────────────────────────

ROC_WINDOW = 20
ROC_THRESHOLD_UP = 0.03    # +3% rate of change → TRENDING_UP
ROC_THRESHOLD_DOWN = -0.03  # -3% → TRENDING_DOWN


def compute_regimes_roc20(
    trajectory: list[dict[str, Any]],
    field: str = "net_pnl",
    window: int = ROC_WINDOW,
    threshold_up: float = ROC_THRESHOLD_UP,
    threshold_down: float = ROC_THRESHOLD_DOWN,
) -> list[int]:
    """Label each trade with a regime based on ROC(window) of cumulative field.

    Regime codes:
        0 = TRENDING_UP
        1 = TRENDING_DOWN
        2 = RANGING

    The cumulative field (e.g. net_pnl) serves as a proxy for the equity
    curve at trade-close granularity.

    ROC(t) = (cumsum[t] - cumsum[t - window]) / cumsum[t - window]

    First `window` trades are labeled RANGING (insufficient data).
    """
    n = len(trajectory)
    if n == 0:
        return []

    values = np.array([float(t.get(field, 0.0)) for t in trajectory])
    cumsum = np.cumsum(values)

    regimes: list[int] = []
    for i in range(n):
        if i < window or cumsum[i - window] == 0.0:
            regimes.append(2)  # RANGING
        else:
            roc = (cumsum[i] - cumsum[i - window]) / abs(cumsum[i - window])
            if roc > threshold_up:
                regimes.append(0)  # TRENDING_UP
            elif roc < threshold_down:
                regimes.append(1)  # TRENDING_DOWN
            else:
                regimes.append(2)  # RANGING

    return regimes


# ── Block Null ──────────────────────────────────────────────────────


class BlockNull(BaseNull):
    """H₀₂: block-structured null.

    Parameters
    ----------
    block_size : int
        Target number of trades per block (explicit, not learned).
        Final blocks may be smaller (remainder) or merged (regime
        mode with tiny regime pockets).
    regimes : list[int] | None
        Pre-computed regime label per trade (0/1/2). If provided,
        blocks are built from CONTIGUOUS trades sharing the same
        regime label, with size guided by block_size. If None,
        blocks are fixed-size contiguous chunks.
    min_block_size : int
        Minimum trades per block. Smaller blocks are merged into
        the preceding block. Only applies in regime mode.
    seed : int | None
        RNG seed for reproducibility.

    Transformation (both modes):
        τ = [B1][B2][B3]...[Bk]
        τ̃ = [Bπ(1)][Bπ(2)][Bπ(3)]...[Bπ(k)]

    Preserves:
    - Within-block trade order, side clustering, timing gaps, costs
    - Block-internal autocorrelation

    Breaks:
    - Global block sequencing
    - Regime-to-regime transition pattern (regime mode)
    - Cross-block correlation structure
    """

    def __init__(
        self,
        block_size: int = 10,
        regimes: list[int] | None = None,
        min_block_size: int = 3,
        seed: int | None = None,
    ) -> None:
        if block_size < 1:
            raise ValueError(f"block_size must be >= 1, got {block_size}")
        self._block_size = block_size
        self._regimes = regimes
        self._min_block_size = min_block_size
        self._seed = seed

    def name(self) -> str:
        return "block_null"

    # ── Block building ─────────────────────────────────────────────

    def _build_blocks_regime(self, trades: list[dict]) -> list[list[dict]]:
        """Build regime-consistent blocks.

        Group contiguous trades with same regime label.
        Blocks smaller than min_block_size merge into preceding block.
        """
        if not trades or self._regimes is None:
            return []

        target = self._block_size
        blocks: list[list[dict]] = []
        current: list[dict] = []
        current_regime: int | None = None

        for t, r in zip(trades, self._regimes):
            if current_regime is None:
                current_regime = r

            if r == current_regime and len(current) < target:
                current.append(t)
            else:
                if len(current) >= self._min_block_size:
                    blocks.append(current)
                elif blocks:
                    blocks[-1].extend(current)
                else:
                    blocks.append(current)
                current = [t]
                current_regime = r

        if current:
            if len(current) >= self._min_block_size:
                blocks.append(current)
            elif blocks:
                blocks[-1].extend(current)
            else:
                blocks.append(current)

        return blocks

    def _build_blocks_fixed(self, trades: list[dict]) -> list[list[dict]]:
        """Build fixed-size blocks."""
        sz = self._block_size
        return [list(trades[i:i + sz]) for i in range(0, len(trades), sz)]

    # ── Main transformation ────────────────────────────────────────

    def transform(self, trajectory: Any, rng: Any = None) -> Any:
        if rng is None:
            rng = np.random.default_rng(self._seed)

        trades = list(trajectory)
        if not trades:
            return []

        # Build blocks
        if self._regimes is not None and len(self._regimes) == len(trades):
            blocks = self._build_blocks_regime(trades)
        else:
            blocks = self._build_blocks_fixed(trades)

        if len(blocks) <= 1:
            return copy.deepcopy(trades)

        # Permute block order
        indices = list(range(len(blocks)))
        rng.shuffle(indices)

        permuted: list[dict] = []
        for idx in indices:
            permuted.extend(copy.deepcopy(blocks[idx]))

        return permuted
