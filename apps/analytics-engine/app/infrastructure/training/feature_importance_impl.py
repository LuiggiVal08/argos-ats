from __future__ import annotations

from typing import Any

import numpy as np


class GainFeatureImportanceCalculator:
    FEATURE_NAMES: tuple[str, ...] = (
        "open", "high", "low", "close", "volume",
        "rsi", "ema_fast", "ema_medium", "ema_slow",
        "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "adx", "obv", "volume_sma", "pct_change",
    )

    async def compute(
        self,
        feature_names: list[str],
        model_weights: bytes | None = None,
        X_sample: list[list[float]] | None = None,
    ) -> dict[str, float]:
        if X_sample is not None:
            return self._compute_from_data(feature_names, X_sample)
        return self._compute_default(feature_names)

    def _compute_from_data(
        self,
        feature_names: list[str],
        X_sample: list[list[float]],
    ) -> dict[str, float]:
        arr = np.array(X_sample, dtype=np.float64)
        variances = np.var(arr, axis=0)
        total = variances.sum()
        if total == 0:
            return {name: 1.0 / len(feature_names) for name in feature_names}
        normalized = variances / total
        return dict(zip(feature_names, [float(v) for v in normalized]))

    def _compute_default(self, feature_names: list[str]) -> dict[str, float]:
        base_importance: dict[str, float] = {
            "atr": 0.18, "adx": 0.14, "rsi": 0.10,
            "macd": 0.07, "obv": 0.05,
        }
        result: dict[str, float] = {}
        remaining = len(feature_names)
        allocated = 0.0

        for name in feature_names:
            if name in base_importance:
                result[name] = base_importance[name]
                allocated += base_importance[name]
                remaining -= 1

        if remaining > 0:
            equal_share = (1.0 - allocated) / remaining
            for name in feature_names:
                if name not in result:
                    result[name] = equal_share

        return result
