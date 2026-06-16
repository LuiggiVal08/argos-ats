"""Keras LSTM model for NovaQuant.

Implementa los ports ModelTrainer y ModelPredictor usando
TensorFlow/Keras.

Arquitectura:
  Input: (lookback, n_features)
    -> LSTM(128, return_sequences=True) + Dropout(0.2)
    -> LSTM(64, return_sequences=True) + Dropout(0.2)
    -> LSTM(32) + Dropout(0.2)
    -> Dense(16, relu)
    -> Dense(3, softmax) -> [BUY, SELL, HOLD]

Entrenamiento: Adam, categorical_crossentropy, early stopping
con patience=10, max 200 epochs, batch_size=32.
Checkpoint del mejor val_loss se guarda en memoria.
"""
from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ...application.ports.model_predictor import (
    ModelPredictor,
    PredictionError,
)
from ...application.ports.model_trainer import (
    ModelTrainer,
    TrainingError,
)
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal

# Intentar importar Keras; si no esta, el error es claro
try:
    import tensorflow as tf

    _TF_AVAILABLE = True
except ImportError:
    _TF_AVAILABLE = False


# Directorio por defecto para checkpoints temporales
_DEFAULT_CHECKPOINT_DIR = Path(tempfile.gettempdir()) / "novaquant_checkpoints"


class NovaQuantKerasModel(ModelTrainer, ModelPredictor):
    """Modelo LSTM implementado en Keras.

    Sirve como trainer y predictor. Una vez entrenado, el modelo
    se mantiene en memoria para inferencia rapida.

    Uso:
        model = NovaQuantKerasModel()
        metrics = await model.train(config, x_train, y_train, x_val, y_val)
        signal = await model.predict(window)

    Nota: Este adapter requiere TensorFlow instalado (ver pyproject.toml
    extras [ml]).
    """

    def __init__(self, checkpoint_dir: str | Path | None = None) -> None:
        if not _TF_AVAILABLE:
            raise RuntimeError(
                "TensorFlow is required for NovaQuantKerasModel. "
                "Install it via: pip install tensorflow>=2.15"
            )

        self._checkpoint_dir = Path(checkpoint_dir or _DEFAULT_CHECKPOINT_DIR)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        self._model: tf.keras.Model | None = None
        self._config: ModelConfig | None = None
        self._best_val_loss: float = float("inf")
        self._best_epoch: int = 0
        self._epochs_trained: int = 0

    # ── ModelTrainer ──────────────────────────────────────────────

    async def train(
        self,
        config: ModelConfig,
        x_train: np.ndarray,
        y_train: np.ndarray,
        x_val: np.ndarray,
        y_val: np.ndarray,
    ) -> dict[str, Any]:
        """Entrena el modelo LSTM con early stopping.

        Args:
            config: config del modelo (layers, dropout, etc.).
            x_train: (n_train, lookback, n_features).
            y_train: (n_train, 3) one-hot.
            x_val: (n_val, lookback, n_features).
            y_val: (n_val, 3) one-hot.

        Returns:
            dict con val_loss, val_accuracy, epochs_trained, best_epoch.
        """
        try:
            self._config = config
            n_features = x_train.shape[2]

            # Construir arquitectura
            model = self._build_model(n_features, config)

            # Callbacks
            callbacks = self._build_callbacks(config)

            # Entrenar
            history = model.fit(
                x_train,
                y_train,
                validation_data=(x_val, y_val),
                epochs=config.max_epochs,
                batch_size=config.batch_size,
                callbacks=callbacks,
                verbose=0,
            )

            # Restaurar mejores pesos
            if self._best_epoch > 0:
                best_weights_path = (
                    self._checkpoint_dir / "novaquant_best.weights.h5"
                )
                if best_weights_path.exists():
                    model.load_weights(str(best_weights_path))

            self._model = model
            self._epochs_trained = len(history.history["loss"])

            # Metricas finales
            val_loss = float(min(history.history["val_loss"]))
            val_accuracy = float(
                history.history["val_accuracy"][
                    history.history["val_loss"].index(val_loss)
                ]
            )

            return {
                "val_loss": val_loss,
                "val_accuracy": val_accuracy,
                "epochs_trained": self._epochs_trained,
                "best_epoch": self._best_epoch,
                "train_loss": float(history.history["loss"][-1]),
                "train_accuracy": float(history.history["accuracy"][-1]),
            }

        except Exception as e:
            raise TrainingError(f"keras_training_failed: {e}") from e

    # ── ModelPredictor ────────────────────────────────────────────

    async def predict(
        self,
        window: np.ndarray,
        confidence_threshold: float = 0.7,
    ) -> TradingSignal:
        """Forward pass: window normalizada -> TradingSignal.

        Args:
            window: (lookback, n_features) normalizada.
            confidence_threshold: minimo para accionable.

        Returns:
            TradingSignal con side, confidence, timestamp.

        Raises PredictionError si no hay modelo cargado o falla.
        """
        if self._model is None:
            raise PredictionError("no_model_loaded: call train() or load_weights() first")

        try:
            # Agregar dimension batch: (1, lookback, n_features)
            batch = np.expand_dims(window, axis=0)

            # Forward pass
            probs = self._model.predict(batch, verbose=0)[0]  # (3,)

            # Determinar clase con mayor probabilidad
            class_idx = int(np.argmax(probs))
            confidence = float(probs[class_idx])

            side = [SignalSide.BUY, SignalSide.SELL, SignalSide.HOLD][class_idx]

            return TradingSignal(
                side=side,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc),
                model_version=self._config_info(),
                metadata={
                    "probabilities": {
                        "buy": float(probs[0]),
                        "sell": float(probs[1]),
                        "hold": float(probs[2]),
                    }
                },
            )

        except Exception as e:
            raise PredictionError(f"prediction_failed: {e}") from e

    # ── Metodos publicos auxiliares ───────────────────────────────

    def get_weights(self) -> bytes:
        """Serializa los pesos del modelo a bytes.

        Raises PredictionError si no hay modelo cargado.
        """
        if self._model is None:
            raise PredictionError("no_model_loaded")
        buf = io.BytesIO()
        self._model.save(buf, save_format="keras")
        return buf.getvalue()

    def load_weights(self, weights_bytes: bytes, config: ModelConfig) -> None:
        """Carga pesos desde bytes.

        Args:
            weights_bytes: pesos serializados (formato Keras).
            config: config del modelo (debe coincidir con la de entrenamiento).
        """
        self._config = config
        n_features = len(config.features)
        model = self._build_model(n_features, config)

        buf = io.BytesIO(weights_bytes)
        model.load_weights(buf)
        self._model = model

    def get_model(self) -> tf.keras.Model | None:
        """Expone el modelo Keras interno para uncertainty estimation."""
        return self._model

    def is_loaded(self) -> bool:
        """True si hay un modelo cargado en memoria."""
        return self._model is not None

    # ── Privado ───────────────────────────────────────────────────

    def _build_model(
        self, n_features: int, config: ModelConfig
    ) -> tf.keras.Model:
        """Construye el grafo de la red LSTM."""
        inputs = tf.keras.Input(
            shape=(config.lookback, n_features), name="ohlcv_window"
        )

        x = inputs

        # Capas LSTM con dropout
        # Las primeras N-1 LSTM return_sequences=True para apilar
        lstm_layers = [layer for layer in config.layers[:-1]]  # todas menos la ultima Dense
        for i, units in enumerate(lstm_layers):
            return_seq = i < len(lstm_layers) - 1  # True excepto la ultima LSTM
            x = tf.keras.layers.LSTM(
                units,
                return_sequences=return_seq,
                name=f"lstm_{i}",
            )(x)
            x = tf.keras.layers.Dropout(config.dropout_rate, name=f"dropout_{i}")(x)

        # Capa Dense intermedia
        dense_units = config.layers[-1]
        x = tf.keras.layers.Dense(dense_units, activation="relu", name="dense")(x)
        x = tf.keras.layers.Dropout(config.dropout_rate, name="dropout_dense")(x)

        # Capa de salida
        outputs = tf.keras.layers.Dense(3, activation="softmax", name="output")(x)

        model = tf.keras.Model(inputs=inputs, outputs=outputs, name="NovaQuant")
        model.compile(
            optimizer=tf.keras.optimizers.Adam(),
            loss="categorical_crossentropy",
            metrics=["accuracy"],
        )

        return model

    def _build_callbacks(self, config: ModelConfig) -> list:
        """Construye callbacks de Keras para el entrenamiento."""
        best_path = str(self._checkpoint_dir / "novaquant_best.weights.h5")

        callbacks = [
            tf.keras.callbacks.ModelCheckpoint(
                best_path,
                monitor="val_loss",
                save_best_only=True,
                save_weights_only=True,
                verbose=0,
            ),
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss",
                patience=config.early_stop_patience,
                restore_best_weights=True,
                verbose=0,
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss",
                factor=0.5,
                patience=config.early_stop_patience // 2,
                min_lr=1e-6,
                verbose=0,
            ),
        ]
        return callbacks

    def _config_info(self) -> str:
        """Version info de la config actual."""
        if self._config is None:
            return "unknown"
        return f"lookback={self._config.lookback}_layers={'_'.join(str(l) for l in self._config.layers)}"
