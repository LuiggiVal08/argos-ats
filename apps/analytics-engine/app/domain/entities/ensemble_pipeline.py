"""EnsemblePipeline — inference pipeline orquestador.

Pipeline completo:
  1. LSTM → probs_lstm
  2. XGBoost → probs_xgb
  3. MetaModel stacking (inputs: lstm_probs + xgb_probs + market features)
  4. Probability Calibration (Platt / Isotonic)
  5. (opcional) MC Dropout Uncertainty
  6. ConfidenceFilter (probability >= threshold, uncertainty <= max, regime_ok)
  → TradingSignal

Domain entity: no tiene dependencias de infraestructura, solo define
el contrato y flujo. Los adapters concretos se inyectan via ports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PipelineStep(Enum):
    LSTM = "lstm"
    XGBOOST = "xgboost"
    META = "meta"
    CALIBRATION = "calibration"
    UNCERTAINTY = "uncertainty"
    CONFIDENCE_FILTER = "confidence_filter"


@dataclass(frozen=True)
class EnsembleResult:
    signal_side: str
    confidence: float
    probability_raw: float
    probability_calibrated: float | None = None
    uncertainty: float | None = None
    regime_ok: bool = True
    filtered: bool = False
    filter_reason: str = ""
    steps_executed: tuple[PipelineStep, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EnsemblePipeline:
    """Orquesta la pipeline de inferencia ensemble.

    No ejecuta nada — es un planificador. El use case concreto
    (PredictEnsembleSignalUseCase) inyecta los adapters y llama
    a los métodos en orden.
    """

    REQUIRED_MARKET_FEATURES = frozenset({"adx", "bbw", "atr", "volume", "rsi"})

    @staticmethod
    def build_meta_input(
        lstm_probs: tuple[float, float, float],
        xgb_probs: tuple[float, float, float],
        market_features: dict[str, float],
    ) -> list[float]:
        """Construye el vector de entrada para el MetaModel.

        Args:
            lstm_probs: (buy, sell, hold) del LSTM.
            xgb_probs: (buy, sell, hold) del XGBoost.
            market_features: dict con adx, bbw, atr, volume, rsi.

        Returns:
            Vector plano: [lstm_buy, lstm_sell, lstm_hold,
                          xgb_buy, xgb_sell, xgb_hold,
                          adx, bbw, atr, rsi, volume]
        """
        missing = EnsemblePipeline.REQUIRED_MARKET_FEATURES - set(market_features.keys())
        if missing:
            raise ValueError(f"missing market features: {missing}")

        return [
            *lstm_probs,
            *xgb_probs,
            market_features["adx"],
            market_features["bbw"],
            market_features["atr"],
            market_features["rsi"],
            market_features["volume"],
        ]
