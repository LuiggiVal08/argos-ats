"""StreamingInferencePipeline — inference optimized for real-time candle streams.

Loads a LogisticRegression model checkpoint from ``models/{symbol}/``:
  - model.pkl        → sklearn LogisticRegression
  - scaler.pkl        → sklearn RobustScaler
  - metadata.json     → config, feature list, version, thresholds
"""
from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ...domain.value_objects.market_regime import RegimeType
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal

log = structlog.get_logger()

_DEFAULT_SYMBOL = "BTC/USDT"
_ALLOWED_MISSING = {"funding_rate", "funding_momentum", "funding_change"}


@dataclass
class StreamingInferenceResult:
    signal: TradingSignal | None = None
    lstm_confidence: float = 0.0
    xgb_confidence: float = 0.0
    ensemble_confidence: float = 0.0
    model_version: str = ""
    raw_probs: list[float] | None = None
    candle_close: float = 0.0
    candle_ts: int = 0
    regime: str = "UNKNOWN"
    error: str = ""


class StreamingInferencePipeline:
    def __init__(
        self,
        symbol: str = _DEFAULT_SYMBOL,
        inference_timeframe: str = "5m",
        checkpoint_base: str | Path | None = None,
        additional_feature_provider: Any = None,
    ) -> None:
        self._symbol = symbol
        self._inference_tf = inference_timeframe
        self._additional_provider = additional_feature_provider

        if checkpoint_base is None:
            self._model_dir = Path(os.environ.get(
                "ARGOS_CHECKPOINT_DIR",
                str(Path(__file__).resolve().parent.parent.parent.parent.parent / "models"),
            ))
        else:
            self._model_dir = Path(checkpoint_base)

        self._pair_dir_name = symbol.replace("/", "_").replace("-", "_").lower()
        self._base_symbol = symbol.split("/")[0].lower()
        self._symbol_dir = self._model_dir / self._pair_dir_name

        self._config: Any = None
        self._model_meta: Any = None
        self._lr_model: Any = None
        self._scaler: Any = None
        self._loaded = False
        self._load_error: str = ""
        self._last_raw_features: dict[str, float] | None = None

    async def load_checkpoint(self) -> bool:
        try:
            model_dir = self._symbol_dir
            if not model_dir.exists():
                fallback = self._model_dir / self._base_symbol
                if fallback.exists():
                    model_dir = fallback
                else:
                    log.warning(
                        "checkpoint_dir_not_found",
                        pair_path=str(self._symbol_dir),
                        base_path=str(fallback),
                        hint="create models/<symbol>/ with model.pkl, scaler.pkl, metadata.json",
                    )
                    return False

            metadata = await self._read_json(model_dir / "metadata.json")
            if metadata is None:
                log.warning("checkpoint_incomplete", path=str(model_dir), missing="metadata.json")
                return False

            self._model_meta = metadata
            self._config = self._make_config(metadata)

            model_path = model_dir / "model.pkl"
            scaler_path = model_dir / "scaler.pkl"

            if not model_path.exists():
                log.warning("checkpoint_incomplete", path=str(model_dir), missing="model.pkl")
                return False
            if not scaler_path.exists():
                log.warning("checkpoint_incomplete", path=str(model_dir), missing="scaler.pkl")
                return False

            self._lr_model = pickle.loads(model_path.read_bytes())
            self._scaler = pickle.loads(scaler_path.read_bytes())

            if self._lr_model.classes_.tolist() != [0, 1, 2]:
                raise RuntimeError(
                    f"Unexpected class encoding: {self._lr_model.classes_}. "
                    f"Expected [0, 1, 2] per global encoding standard "
                    f"(0=SELL, 1=HOLD, 2=BUY)."
                )

            self._loaded = True
            log.info(
                "checkpoint_loaded",
                symbol=self._symbol,
                version=metadata.get("model_version", "unknown"),
                model_type=type(self._lr_model).__name__,
                features=len(metadata.get("feature_names", [])),
            )
            return True

        except Exception as e:
            self._load_error = str(e)
            log.error("checkpoint_load_failed", symbol=self._symbol, error=str(e))
            return False

    async def predict(
        self,
        ohlcv_buffer: list[dict[str, float | int]],
        lookback: int | None = None,
    ) -> StreamingInferenceResult:
        candle_ts = int(ohlcv_buffer[-1].get("timestamp", 0)) if ohlcv_buffer else 0
        candle_close = float(ohlcv_buffer[-1].get("close", 0)) if ohlcv_buffer else 0.0

        if not self._loaded or self._config is None or self._model_meta is None:
            log.warning(
                "inference_fallback_triggered",
                reason="checkpoint_not_loaded",
                load_error=self._load_error,
            )
            return StreamingInferenceResult(
                signal=None, error="checkpoint_not_loaded",
                candle_close=candle_close, candle_ts=candle_ts,
            )

        if len(ohlcv_buffer) < 30:
            log.warning(
                "inference_fallback_triggered",
                reason="insufficient_candles",
                count=len(ohlcv_buffer),
            )
            return StreamingInferenceResult(
                signal=None, error=f"insufficient_candles: {len(ohlcv_buffer)} < 30",
                candle_close=candle_close, candle_ts=candle_ts,
            )

        try:
            import pandas as pd

            from ...infrastructure.training.data_preprocessor import TaDataPreprocessor

            preprocessor = TaDataPreprocessor()
            cfg = self._make_config(self._model_meta)

            try:
                features_raw = await preprocessor.build_features(
                    [dict(r) for r in ohlcv_buffer], cfg
                )
            except Exception as e:
                log.warning(
                    "inference_fallback_triggered",
                    reason="feature_generation_failed",
                    error=str(e),
                )
                return StreamingInferenceResult(
                    signal=None, error=f"feature_generation_failed: {e}",
                    candle_close=candle_close, candle_ts=candle_ts,
                )

            if np.any(np.isnan(features_raw)) or np.any(np.isinf(features_raw)):
                log.warning(
                    "inference_fallback_triggered",
                    reason="nan_or_inf_in_features",
                    nan_count=int(np.sum(np.isnan(features_raw))),
                    inf_count=int(np.sum(np.isinf(features_raw))),
                )
                return StreamingInferenceResult(
                    signal=None, error="nan_or_inf_in_features",
                    candle_close=candle_close, candle_ts=candle_ts,
                )

            if features_raw.shape[1] != len(cfg.features):
                log.warning(
                    "inference_fallback_triggered",
                    reason="feature_mismatch",
                    got=features_raw.shape[1],
                    expected=len(cfg.features),
                )
                return StreamingInferenceResult(
                    signal=None,
                    error=f"feature_mismatch: got {features_raw.shape[1]}, expected {len(cfg.features)}",
                    candle_close=candle_close, candle_ts=candle_ts,
                )

            self._last_raw_features = {
                name: float(features_raw[-1, i])
                for i, name in enumerate(cfg.features)
            }

            if self._additional_provider is not None:
                try:
                    extra = await self._additional_provider.get_features(self._symbol)
                    feature_names = list(cfg.features)
                    for i, name in enumerate(feature_names):
                        if name in extra:
                            features_raw[-1, i] = extra[name]
                except Exception as exc:
                    log.warning("additional_features_failed", error=str(exc))

            features_norm = self._scaler.transform(features_raw)

            if np.any(np.isnan(features_norm)) or np.any(np.isinf(features_norm)):
                log.warning(
                    "inference_fallback_triggered",
                    reason="nan_or_inf_after_scaling",
                )
                return StreamingInferenceResult(
                    signal=None, error="nan_or_inf_after_scaling",
                    candle_close=candle_close, candle_ts=candle_ts,
                )

            last_row = features_norm[-1:]
            probs = self._lr_model.predict_proba(last_row)[0]

            class_idx = int(np.argmax(probs))
            prob_sell = float(probs[0]) if len(probs) > 0 else 0.0
            prob_hold = float(probs[1]) if len(probs) > 1 else 0.0
            prob_buy = float(probs[2]) if len(probs) > 2 else 0.0

            side = [SignalSide.SELL, SignalSide.HOLD, SignalSide.BUY][class_idx]
            confidence = float(probs[class_idx])

            expected_side = {0: SignalSide.SELL, 1: SignalSide.HOLD, 2: SignalSide.BUY}
            selected_class = int(self._lr_model.classes_[class_idx])
            if side != expected_side[selected_class]:
                raise RuntimeError(
                    f"Class encoding mismatch: model class={selected_class} "
                    f"(expected {expected_side[selected_class]}) but side={side}. "
                    f"Check SignalSide list ordering / prob variable mapping."
                )

            thresholds = self._model_meta.get("parameters", {}).get("thresholds", {})
            buy_threshold = thresholds.get("BUY", 0.6)
            sell_threshold = thresholds.get("SELL", 0.4)

            if side == SignalSide.BUY and confidence < buy_threshold:
                side = SignalSide.HOLD
                confidence = prob_hold

            if side == SignalSide.SELL and confidence < sell_threshold:
                side = SignalSide.HOLD
                confidence = prob_hold

            regime = self._detect_regime(features_raw, cfg)

            if side == SignalSide.HOLD:
                return StreamingInferenceResult(
                    signal=None,
                    ensemble_confidence=confidence,
                    model_version=self._model_meta.get("model_version", ""),
                    raw_probs=[prob_buy, prob_sell, prob_hold],
                    candle_close=candle_close,
                    candle_ts=candle_ts,
                    regime=regime,
                )

            trading_signal = TradingSignal(
                side=side,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc),
                model_version=self._model_meta.get("model_version", ""),
                metadata={
                    "probabilities": {
                        "buy": prob_buy,
                        "sell": prob_sell,
                        "hold": prob_hold,
                    },
                    "regime": regime,
                    "inference_timeframe": self._inference_tf,
                },
            )

            return StreamingInferenceResult(
                signal=trading_signal,
                ensemble_confidence=confidence,
                model_version=self._model_meta.get("model_version", ""),
                raw_probs=[prob_buy, prob_sell, prob_hold],
                candle_close=candle_close,
                candle_ts=candle_ts,
                regime=regime,
            )

        except Exception as e:
            log.warning("inference_fallback_triggered", reason="unexpected_error", error=str(e))
            return StreamingInferenceResult(
                signal=None, error=str(e),
                candle_close=candle_close, candle_ts=candle_ts,
            )

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def last_raw_features(self) -> dict[str, float] | None:
        return self._last_raw_features

    async def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            if path.exists():
                return json.loads(path.read_text())
        except Exception as e:
            log.warning("read_json_failed", path=str(path), error=str(e))
        return None

    def _make_config(self, meta: dict) -> Any:
        from ...domain.value_objects.model_config import ModelConfig

        feature_names = meta.get("feature_names", [])
        params = meta.get("parameters", {})
        model_tf = params.get("timeframe")
        if model_tf is not None and model_tf != self._inference_tf:
            log.error(
                "timeframe_mismatch",
                model_timeframe=model_tf,
                inference_timeframe=self._inference_tf,
            )
        return ModelConfig(
            lookback=params.get("lookback", 20),
            confidence_threshold=params.get("confidence_threshold", 0.6),
            features=tuple(feature_names),
            target_lookahead=params.get("lookahead", 5),
        )

    @staticmethod
    def _detect_regime(
        features_raw: np.ndarray,
        config: Any,
    ) -> str:
        if features_raw.shape[0] == 0:
            return "UNKNOWN"
        last = features_raw[-1]
        if hasattr(config, "features"):
            feature_names = list(config.features)
        elif isinstance(config, dict):
            feature_names = list(config.get("features", []))
        else:
            return "UNKNOWN"
        if not feature_names or features_raw.shape[1] < len(feature_names):
            return "UNKNOWN"
        try:
            adx_idx = feature_names.index("adx") if "adx" in feature_names else -1
            if adx_idx >= 0 and adx_idx < len(last):
                adx = float(last[adx_idx])
                return "TRENDING" if adx >= 25 else "RANGING"
        except (ValueError, IndexError):
            pass
        return "UNKNOWN"
