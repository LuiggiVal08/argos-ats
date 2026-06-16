"""Value Object: ModelConfig — configuración de NovaQuant.

Define los hiperparámetros de la red LSTM y del pipeline:
- lookback: cuántas velas hacia atrás usa la red para predecir.
- confidence_threshold: probabilidad mínima para ejecutar una señal.
- layers: arquitectura de la red (número de neuronas por capa LSTM/Dense).
- features: lista de nombres de features a usar en el entrenamiento.
- target_lookahead: cuántas velas adelante se mira para etiquetar (BUY/SELL/HOLD).
- target_return_pct: % de cambio mínimo para considerar BUY o SELL.

Per spec section 5 Historia 6 (NovaQuant): el modelo se configura
antes del entrenamiento y se almacena junto con los pesos en el
checkpoint. Un cambio de configuración invalida los pesos existentes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Fronteras mínimas para validación (ver spec section 5 Historia 6)
MIN_LOOKBACK: int = 10
MAX_LOOKBACK: int = 500
MIN_CONFIDENCE: float = 0.5
MAX_CONFIDENCE: float = 1.0


@dataclass(frozen=True)
class ModelConfig:
    """Configuración completa del modelo NovaQuant.

    Attributes:
        lookback:              Velas hacia atrás para formar cada muestra (default 60).
        confidence_threshold:  Probabilidad mínima para considerar una señal
                               como accionable (default 0.7).
        layers:                Lista con el tamaño de cada capa.
                               Última capa (Dense con softmax) se añade automáticamente.
        features:              Lista de nombres de features a calcular.
        target_lookahead:      Velas hacia adelante para calcular el target (default 5).
        target_return_pct:     % de cambio para etiquetar BUY/SELL (default 1.0).
        dropout_rate:          Dropout entre capas LSTM (default 0.2).
        batch_size:            Tamaño del batch para entrenamiento (default 32).
        max_epochs:            Épocas máximas de entrenamiento (default 200).
        early_stop_patience:   Épocas sin mejora para early stopping (default 10).
    """

    lookback: int = 60
    confidence_threshold: float = 0.7
    layers: tuple[int, ...] = (128, 64, 32, 16)
    features: tuple[str, ...] = (
        "open", "high", "low", "close", "volume",
        "rsi", "ema_fast", "ema_medium", "ema_slow",
        "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "adx", "obv", "volume_sma", "pct_change",
    )
    target_lookahead: int = 5
    target_return_pct: float = 1.0
    dropout_rate: float = 0.2
    batch_size: int = 32
    max_epochs: int = 200
    early_stop_patience: int = 10

    def __post_init__(self) -> None:
        if not MIN_LOOKBACK <= self.lookback <= MAX_LOOKBACK:
            raise ValueError(
                f"lookback must be in [{MIN_LOOKBACK}, {MAX_LOOKBACK}], "
                f"got {self.lookback}"
            )
        if not MIN_CONFIDENCE <= self.confidence_threshold <= MAX_CONFIDENCE:
            raise ValueError(
                f"confidence_threshold must be in "
                f"[{MIN_CONFIDENCE}, {MAX_CONFIDENCE}], "
                f"got {self.confidence_threshold}"
            )
        if len(self.layers) < 2:
            raise ValueError(
                f"need at least 2 layers (LSTM + Dense), got {len(self.layers)}"
            )
        if not self.features:
            raise ValueError("features list cannot be empty")
        if self.target_lookahead < 1:
            raise ValueError(
                f"target_lookahead must be >= 1, got {self.target_lookahead}"
            )
        if not 0 < self.target_return_pct <= 100:
            raise ValueError(
                f"target_return_pct must be in (0, 100], got {self.target_return_pct}"
            )
        if not 0 <= self.dropout_rate <= 0.5:
            raise ValueError(
                f"dropout_rate must be in [0, 0.5], got {self.dropout_rate}"
            )
