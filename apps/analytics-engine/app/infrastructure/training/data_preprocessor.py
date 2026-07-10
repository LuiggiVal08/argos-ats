"""Feature engineering pipeline for NovaQuant.

Convierte OHLCV crudo en ventanas normalizadas con features
tecnicas listas para la red LSTM.

Pipeline completo:
   1. build_features:  calcula features base + opcionalmente MTF
   2. normalize:       z-score con medias/std dadas o calculadas
   3. create_windows:  sliding window de tamano lookback
   4. create_targets:  etiquetas BUY/SELL/HOLD segun return forward

Stack: pandas, numpy, ta (Technical Analysis Library).

Las 20 features base coinciden con el dataset original de Colab:
  open, high, low, close, volume,
  rsi, ema_fast, ema_medium, ema_slow,
  macd, macd_signal, macd_hist,
  bb_upper, bb_middle, bb_lower,
  atr, adx, obv, volume_sma, pct_change

Cuando ModelConfig.features incluye nombres adicionales (ej. htf_rsi_4h),
build_features computa automaticamente indicadores multi-timeframe y los
apendiza al tensor, manteniendo el orden exacto de config.features.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import ta

from ...application.ports.data_preprocessor import (
    DataPreprocessor,
    InsufficientDataError,
    PreprocessingError,
)
from ...domain.entities.multi_timeframe_aligner import MultiTimeframeAligner
from ...domain.value_objects.model_config import ModelConfig
from ...domain.value_objects.scaler_type import ScalerType

# ── Feature contract: semantic classification ──────────────────────────────
# A feature is "MTF" (multi-timeframe) if its name matches any known
# htf_{indicator}_{tf} pattern. This registry is the single source of truth
# for distinguishing base vs MTF features, regardless of their position in
# ModelConfig.features. Position-based slicing was the root cause of
# INCIDENT-001 follow-up (MTF features interleaved in reduced_33 set).
MTF_FEATURE_REGISTRY: frozenset[str] = frozenset(
    f"htf_{ind}_{tf}"
    for tf in ("4h", "1d")
    for ind in MultiTimeframeAligner.INDICATOR_COLS
)


def is_mtf_feature(name: str) -> bool:
    """Returns True if *name* is a known multi-timeframe feature.

    Classification is purely semantic (naming convention + registry),
    not positional. A feature starting with ``htf_`` that exists in
    ``MTF_FEATURE_REGISTRY`` is MTF regardless of where it appears
    in the model's feature list.
    """
    return name.startswith("htf_") and name in MTF_FEATURE_REGISTRY


class TaDataPreprocessor:
    """Preprocesador de OHLCV usando pandas + ta + numpy.

    Implementa el port DataPreprocessor.
    """

    # 20 features base en orden estricto del tensor (coincide con dataset de Colab)
    FEATURE_NAMES: tuple[str, ...] = (
        "open", "high", "low", "close", "volume",
        "rsi", "ema_fast", "ema_medium", "ema_slow",
        "macd", "macd_signal", "macd_hist",
        "bb_upper", "bb_middle", "bb_lower",
        "atr", "adx", "obv", "volume_sma", "pct_change",
    )

    async def build_features(
        self,
        ohlcv: list[dict],
        config: ModelConfig,
    ) -> np.ndarray:
        """Calcula todas las features desde OHLCV.

        Siempre computa las 20 features base. Si config.features incluye
        nombres adicionales (ej. htf_rsi_4h), computa indicadores MTF
        y los apendiza manteniendo el orden exacto de config.features.

        Args:
            ohlcv: lista de dicts con keys timestamp, open, high, low, close, volume.
            config: config del modelo (dicta que features calcular).

        Returns:
            Array (n_velas, n_features) donde n_features = len(config.features).
        """
        try:
            df = pd.DataFrame(ohlcv)
            df = _ensure_numeric(df)
            # Remove duplicate timestamps (keep last)
            if "timestamp" in df.columns:
                df = df.drop_duplicates(subset=["timestamp"], keep="last")

            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"]

            # ── 1-5: OHLCV raw (passthrough) ─────────────────────────
            features = [
                df["open"],
                high,
                low,
                close,
                volume,
            ]

            # ── 6: RSI(14) ────────────────────────────────────────────
            features.append(ta.momentum.RSIIndicator(close, window=14).rsi())

            # ── 7-9: EMAs (9, 21, 50) ────────────────────────────────
            features.append(ta.trend.EMAIndicator(close, window=9).ema_indicator())
            features.append(ta.trend.EMAIndicator(close, window=21).ema_indicator())
            features.append(ta.trend.EMAIndicator(close, window=50).ema_indicator())

            # ── 10-12: MACD (12, 26, 9) ──────────────────────────────
            macd = ta.trend.MACD(close)
            features.append(macd.macd())              # macd
            features.append(macd.macd_signal())       # macd_signal
            features.append(macd.macd_diff())         # macd_hist

            # ── 13-15: Bollinger Bands (20, 2) ───────────────────────
            bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
            features.append(bb.bollinger_hband())      # bb_upper
            features.append(bb.bollinger_mavg())       # bb_middle
            features.append(bb.bollinger_lband())      # bb_lower

            # ── 16: ATR(14) ──────────────────────────────────────────
            features.append(
                ta.volatility.AverageTrueRange(
                    high, low, close, window=14
                ).average_true_range()
            )

            # ── 17: ADX(14) ──────────────────────────────────────────
            features.append(
                ta.trend.ADXIndicator(high, low, close, window=14).adx()
            )

            # ── 18: OBV ──────────────────────────────────────────────
            features.append(
                ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
            )

            # ── 19: Volume SMA(20) ───────────────────────────────────
            features.append(volume.rolling(20).mean())

            # ── 20: Price change % ───────────────────────────────────
            features.append(close.pct_change() * 100.0)

            # Combinar base features
            base_count = len(self.FEATURE_NAMES)
            result = pd.concat(features, axis=1)
            result.columns = self.FEATURE_NAMES

            # ── MTF features (semantic, not positional) ──────────────
            # Resolve each feature in config.features by its type:
            #   - base: already in result (from FEATURE_NAMES)
            #   - MTF:  extracted from MultiTimeframeAligner output
            #   - funding: filled with 0.0 later
            # This replaces the old positional slice (features[base_count:])
            # which silently skipped MTF features interleaved within the
            # first 20 slots. (INCIDENT-001 follow-up)
            mtf_needed = [f for f in config.features if is_mtf_feature(f)]
            if mtf_needed:
                mtf_df = MultiTimeframeAligner.compute(ohlcv)
                available_mtf = [f for f in mtf_needed if f in mtf_df.columns]
                if available_mtf:
                    result = pd.concat([result, mtf_df[available_mtf]], axis=1)

            # Rellenar NaN (primeros valores donde los indicadores no tienen historia)
            result = result.bfill().ffill().fillna(0.0)

            # Validar que el array tenga las columnas esperadas
            expected_cols = list(config.features)
            missing = [c for c in expected_cols if c not in result.columns]

            allowed_missing = {
                "funding_rate",
                "funding_momentum",
                "funding_change",
            }

            unexpected = set(missing) - allowed_missing
            if unexpected:
                raise ValueError(
                    f"Unexpected missing features: {unexpected}"
                )

            for col in allowed_missing:
                if col not in result.columns:
                    result[col] = 0.0

            if list(result.columns) != expected_cols:
                result = result[expected_cols]

            return result.values.astype(np.float64)

        except Exception as e:
            raise PreprocessingError(f"build_features failed: {e}") from e

    async def normalize(
        self,
        features: np.ndarray,
        means: tuple[float, ...] | None = None,
        stds: tuple[float, ...] | None = None,
        scaler_type: ScalerType = ScalerType.STANDARD,
    ) -> tuple[np.ndarray, tuple[float, ...], tuple[float, ...]]:
        if means is None or stds is None:
            if scaler_type == ScalerType.STANDARD:
                means = tuple(float(v) for v in features.mean(axis=0))
                stds = tuple(float(v) for v in features.std(axis=0).clip(min=1e-10))
            elif scaler_type == ScalerType.MINMAX:
                mins = features.min(axis=0)
                maxs = features.max(axis=0)
                ranges = (maxs - mins).clip(min=1e-10)
                means = tuple(float(v) for v in mins)
                stds = tuple(float(v) for v in ranges)
            elif scaler_type == ScalerType.ROBUST:
                q1 = np.percentile(features, 25, axis=0)
                q3 = np.percentile(features, 75, axis=0)
                medians = np.median(features, axis=0)
                iqrs = (q3 - q1).clip(min=1e-10)
                means = tuple(float(v) for v in medians)
                stds = tuple(float(v) for v in iqrs)
            else:
                raise ValueError(f"unknown scaler: {scaler_type}")

        means_arr = np.array(means, dtype=np.float64)
        stds_arr = np.array(stds, dtype=np.float64)

        if scaler_type == ScalerType.MINMAX:
            normalized = (features - means_arr) / stds_arr
        else:
            normalized = (features - means_arr) / stds_arr

        return normalized, means, stds

    async def create_windows(
        self,
        features: np.ndarray,
        lookback: int,
    ) -> np.ndarray:
        """Crea ventanas deslizantes.

        Returns:
            Array (n_ventanas, lookback, n_features).

        Raises InsufficientDataError si features es muy corto.
        """
        n = len(features)
        if n < lookback + 1:
            raise InsufficientDataError(
                f"need at least {lookback + 1} rows, got {n}"
            )

        windows = np.lib.stride_tricks.sliding_window_view(
            features, window_shape=lookback, axis=0
        )
        # sliding_window_view da (n - lookback + 1, lookback, n_features)
        # pero transpuesto: queremos (n_muestras, lookback, n_features)
        return windows.transpose(0, 2, 1)

    async def create_targets(
        self,
        ohlcv: list[dict],
        config: ModelConfig,
        atr_values: np.ndarray | None = None,
    ) -> np.ndarray:
        try:
            df = pd.DataFrame(ohlcv)
            df = _ensure_numeric(df)
            close = df["close"].values

            n = len(close)
            lookahead = config.target_lookahead
            use_atr = atr_values is not None and len(atr_values) >= n

            targets = np.zeros((n, 3), dtype=np.float64)

            for i in range(n - lookahead):
                ret = (close[i + lookahead] - close[i]) / close[i]
                if use_atr and atr_values is not None:
                    atr = atr_values[i]
                    if np.isnan(atr) or atr <= 0:
                        targets[i] = [0.0, 1.0, 0.0]
                        continue
                    threshold = 1.5 * atr / close[i]
                else:
                    threshold = config.target_return_pct / 100.0

                if ret > threshold:
                    targets[i] = [0.0, 0.0, 1.0]
                elif ret < -threshold:
                    targets[i] = [1.0, 0.0, 0.0]
                else:
                    targets[i] = [0.0, 1.0, 0.0]

            targets[-lookahead:] = [0.0, 1.0, 0.0]

            return targets

        except Exception as e:
            raise PreprocessingError(f"create_targets failed: {e}") from e

    async def create_targets_triple_barrier(
        self,
        ohlcv: list[dict],
        atr_values: np.ndarray,
        atr_multiplier: float = 1.5,
        max_holding: int = 5,
    ) -> np.ndarray:
        """Triple Barrier labeling: BUY if TP hit first, SELL if SL hit first.

        Barriers are placed at entry ± atr_multiplier × ATR.
        If neither barrier is hit within max_holding candles → HOLD.
        Uses high/low for barrier crossing (not just close).

        Returns one-hot array (n, 3): BUY=[1,0,0], SELL=[0,1,0], HOLD=[0,0,1].
        """
        try:
            df = pd.DataFrame(ohlcv)
            df = _ensure_numeric(df)
            close = df["close"].values.astype(np.float64)
            high = df["high"].values.astype(np.float64)
            low = df["low"].values.astype(np.float64)
            atr = atr_values.astype(np.float64)

            n = len(close)
            targets = np.zeros((n, 3), dtype=np.float64)

            for i in range(n - max_holding):
                atr_i = atr[i]
                if np.isnan(atr_i) or atr_i <= 0:
                    targets[i] = [0.0, 0.0, 1.0]
                    continue

                tp = close[i] + atr_multiplier * atr_i
                sl = close[i] - atr_multiplier * atr_i
                hit = False

                for t in range(i + 1, min(i + max_holding + 1, n)):
                    if high[t] >= tp:
                        targets[i] = [1.0, 0.0, 0.0]
                        hit = True
                        break
                    if low[t] <= sl:
                        targets[i] = [0.0, 1.0, 0.0]
                        hit = True
                        break

                if not hit:
                    targets[i] = [0.0, 0.0, 1.0]

            targets[-max_holding:] = [0.0, 0.0, 1.0]
            return targets

        except Exception as e:
            raise PreprocessingError(f"create_targets_triple_barrier failed: {e}") from e


def _ensure_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte columnas numericas a float64, maneja nulos."""
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df
