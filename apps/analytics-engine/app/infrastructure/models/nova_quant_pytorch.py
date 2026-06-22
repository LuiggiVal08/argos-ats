"""PyTorch LSTM model for NovaQuant.

Implementa el port ModelPredictor usando PyTorch.

Arquitectura (entrenada externamente en Colab):
  Input: (lookback, 19 features)
    -> LSTM(hidden_dim=128, num_layers=1, batch_first=True)
    -> Dense(1, sigmoid) -> [trade_probability]

El modelo se entrena en Colab y se carga desde .pt + .pkl.
Este adapter solo hace inferencia (no entrenamiento).

Mapeo binario -> 3 clases (TradingSignal):
  sigmoid > confidence_threshold -> SignalSide.BUY
  otherwise                      -> SignalSide.HOLD
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ...application.ports.model_predictor import (
    ModelPredictor,
    PredictionError,
)
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal

try:
    import torch
    import torch.nn as nn

    _TORCH_AVAILABLE = True
except ImportError:
    _TORCH_AVAILABLE = False

log = structlog.get_logger()


class _ArgosLSTM(nn.Module):
    """Arquitectura LSTM replicada del entrenamiento en Colab.

    La arquitectura exacta se infiere del state_dict al cargar:
      - input_size  -> state_dict['lstm.weight_ih_l0'].shape[1]
      - hidden_dim  -> state_dict['lstm.weight_hh_l0'].shape[0]
      - num_layers  -> cantidad de pares weight_ih / weight_hh en state_dict
    """

    def __init__(
        self,
        n_features: int,
        hidden_dim: int = 128,
        num_layers: int = 1,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
        )
        self.fc = nn.Linear(hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, lookback, n_features)
        _, (h_n, _) = self.lstm(x)
        out = self.fc(h_n[-1])
        return torch.sigmoid(out)


class NovaQuantPyTorchModel(ModelPredictor):
    """Modelo LSTM en PyTorch para inferencia.

    Carga pesos desde .pt y scaler desde .pkl.
    No soporta entrenamiento (se entrena en Colab).

    Uso:
        model = NovaQuantPyTorchModel()
        model.load_checkpoint("best_argos_lstm.pt", "scaler_argos.pkl", config)
        signal = await model.predict(window)
    """

    def __init__(self) -> None:
        if not _TORCH_AVAILABLE:
            raise RuntimeError(
                "PyTorch is required for NovaQuantPyTorchModel. "
                "Install it via: pip install torch>=2.1"
            )

        self._model: nn.Module | None = None
        self._config: ModelConfig | None = None
        self._feature_means: np.ndarray | None = None
        self._feature_stds: np.ndarray | None = None

    # ── ModelPredictor ────────────────────────────────────────────

    async def predict(
        self,
        window: np.ndarray,
        confidence_threshold: float = 0.55,
    ) -> TradingSignal:
        """Forward pass con PyTorch.

        Args:
            window: (lookback, n_features) normalizada.
            confidence_threshold: minimo para considerar accionable.

        Returns:
            TradingSignal con side=BUY si prob > threshold, HOLD si no.

        Raises PredictionError si no hay modelo cargado o falla.
        """
        if self._model is None:
            raise PredictionError(
                "no_model_loaded: call load_checkpoint() or load_weights_from_bytes() first"
            )

        try:
            tensor = torch.from_numpy(window.copy()).float().unsqueeze(0)
            # tensor: (1, lookback, n_features)

            self._model.eval()
            with torch.no_grad():
                prob = self._model(tensor).item()

            side = SignalSide.BUY if prob > confidence_threshold else SignalSide.HOLD

            return TradingSignal(
                side=side,
                confidence=prob,
                timestamp=datetime.now(timezone.utc),
                model_version=self._config_info(),
                metadata={
                    "probabilities": {
                        "trade": prob,
                        "no_trade": 1.0 - prob,
                    }
                },
            )

        except Exception as e:
            raise PredictionError(f"pytorch_prediction_failed: {e}") from e

    # ── Metodos publicos ──────────────────────────────────────────

    def load_checkpoint(
        self,
        pt_path: str | Path,
        scaler_path: str | Path | None = None,
        config: ModelConfig | None = None,
    ) -> None:
        """Carga pesos + scaler desde archivos.

        Args:
            pt_path: ruta a best_argos_lstm.pt.
            scaler_path: ruta a scaler_argos.pkl (opcional).
            config: ModelConfig. Si es None, usa defaults con n_features inferido.
        """
        device = torch.device("cpu")
        state = torch.load(pt_path, map_location=device, weights_only=True)

        n_features, hidden_dim, num_layers = _infer_architecture(state)

        self._config = config or ModelConfig(
            lookback=60,
            confidence_threshold=0.55,
            layers=(hidden_dim, 1),
            features=(
                "open", "high", "low", "close", "volume",
                "rsi", "ema_fast", "ema_medium", "ema_slow",
                "macd", "macd_signal", "macd_hist",
                "bb_upper", "bb_middle", "bb_lower",
                "atr", "obv", "volume_sma", "pct_change",
            ),
        )

        model = _ArgosLSTM(
            n_features=n_features,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
        )
        model.load_state_dict(state)
        model.eval()
        self._model = model

        if scaler_path:
            self._load_scaler_from_json(scaler_path)

    def load_weights_from_bytes(
        self,
        pt_bytes: bytes,
        n_features: int,
        config: ModelConfig,
        scaler_bytes: bytes | None = None,
    ) -> None:
        """Carga pesos desde bytes (para checkpoint repo).

        Args:
            pt_bytes: pesos serializados .pt en bytes.
            n_features: numero de features de entrada.
            config: configuracion del modelo.
            scaler_bytes: scaler serializado en bytes (opcional).
        """
        device = torch.device("cpu")
        state = torch.load(
            io.BytesIO(pt_bytes), map_location=device, weights_only=True
        )

        _nf, hidden_dim, num_layers = _infer_architecture(state)
        assert _nf == n_features, (
            f"n_features mismatch: state_dict has {_nf}, expected {n_features}"
        )

        self._config = config
        model = _ArgosLSTM(
            n_features=n_features,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
        )
        model.load_state_dict(state)
        model.eval()
        self._model = model

        if scaler_bytes:
            self._load_scaler_from_json_bytes(scaler_bytes)

    def is_loaded(self) -> bool:
        return self._model is not None

    def get_weights_bytes(self) -> bytes:
        """Serializa pesos a bytes."""
        if self._model is None:
            raise PredictionError("no_model_loaded")
        buf = io.BytesIO()
        torch.save(self._model.state_dict(), buf)
        return buf.getvalue()

    def get_scaler_bytes(self) -> bytes:
        """Serializa scaler a JSON bytes (no pickle)."""
        if self._feature_means is None:
            return b""
        data = {
            "version": 1,
            "mean_": self._feature_means.tolist(),
            "var_": (self._feature_stds ** 2).tolist(),
        }
        return json.dumps(data).encode("utf-8")

    # ── Scaler JSON loading (replaces pickle) ─────────────────────

    def _load_scaler_from_json(self, path: str | Path) -> None:
        """Load scaler mean/std from JSON file. Rejects legacy pickle."""
        raw = Path(path).read_bytes()
        self._load_scaler_from_json_bytes(raw)

    def _load_scaler_from_json_bytes(self, raw: bytes) -> None:
        """Load scaler mean/std from JSON bytes. Rejects legacy pickle."""
        if len(raw) > 0 and raw[0] == 0x80:
            log.error("scaler_load_rejected", reason="legacy_pickle_checkpoint_rejected_for_security")
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            self._feature_means = np.array(data["mean_"], dtype=np.float64)
            self._feature_stds = np.sqrt(np.array(data["var_"], dtype=np.float64))
        except Exception as e:
            log.warning("scaler_load_failed", error=str(e))

    # ── Privado ───────────────────────────────────────────────────

    def _config_info(self) -> str:
        if self._config is None:
            return "unknown"
        return f"pytorch_lstm_h128_lookback={self._config.lookback}"


# ── Helpers ──────────────────────────────────────────────────────


def _infer_architecture(
    state: dict[str, torch.Tensor],
) -> tuple[int, int, int]:
    """Infere n_features, hidden_dim, num_layers del state_dict.

    Las keys de PyTorch LSTM siguen el patron:
      lstm.weight_ih_l{layer}  -> (4*hidden, input_size)  para layer=0
      lstm.weight_hh_l{layer}  -> (4*hidden, hidden)
      lstm.bias_ih_l{layer}
      lstm.bias_hh_l{layer}
    """
    n_features = state["lstm.weight_ih_l0"].shape[1]
    hidden_dim = state["lstm.weight_hh_l0"].shape[0] // 4

    num_layers = 1
    while f"lstm.weight_ih_l{num_layers}" in state:
        num_layers += 1

    return n_features, hidden_dim, num_layers
