"""StreamingInferencePipeline — inference optimized for real-time candle streams.

Loads a LogisticRegression model checkpoint from ``models/production/{symbol}/``:
  - model.pkl        → sklearn LogisticRegression (must match EXPECTED_CLASSES / EXPECTED_FEATURES)
  - scaler.pkl        → sklearn RobustScaler
  - metadata.json     → config, feature list, version, thresholds
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from ...domain.value_objects.market_regime import RegimeType
from ...domain.value_objects.signal_side import SignalSide
from ...domain.value_objects.trading_signal import TradingSignal
from ..logging.correlation import set_inference_counter_path
from ..logging.inference_logger import log_inference, _compute_feature_hash
from ..tracking.inference_tracker import InferenceTracker

log = structlog.get_logger()

# ── Model contract: all production models MUST match these ──
EXPECTED_CLASSES = [0, 1, 2]
EXPECTED_FEATURES = 30
EXPECTED_MODEL_VERSION_PREFIX = "qv2_target_spec_v1"

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
    inference_sequence_id: str = ""


class StreamingInferencePipeline:
    def __init__(
        self,
        symbol: str = _DEFAULT_SYMBOL,
        inference_timeframe: str = "5m",
        checkpoint_base: str | Path | None = None,
        additional_feature_provider: Any = None,
        state_dir: str | Path | None = None,
        reports_dir: str | Path | None = None,
    ) -> None:
        self._symbol = symbol
        self._inference_tf = inference_timeframe
        self._additional_provider = additional_feature_provider

        base_models = Path(
            os.environ.get(
                "ARGOS_CHECKPOINT_DIR",
                str(Path(__file__).resolve().parent.parent.parent.parent.parent / "models"),
            )
            if checkpoint_base is None
            else str(checkpoint_base)
        )
        self._model_dir = base_models / "production"
        self._symbol_dir = self._model_dir / symbol.split("/")[0].lower()

        self._config: Any = None
        self._model_meta: Any = None
        self._lr_model: Any = None
        self._scaler: Any = None
        self._loaded = False
        self._load_error: str = ""
        self._last_raw_features: dict[str, float] | None = None
        self._model_checksum: str = ""
        self._scaler_checksum: str = ""
        self._metadata_checksum: str = ""

        # ── Inference tracking (longitudinal) ──
        project_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
        resolved_state = Path(state_dir) if state_dir else (project_root / "state")
        resolved_reports = Path(reports_dir) if reports_dir else (project_root / "reports")
        timeline_path = resolved_reports / "active" / "paper_trading" / "inference_timeline.csv"
        snapshots_dir = resolved_reports / "active" / "paper_trading" / "snapshots"
        self._tracker = InferenceTracker(
            timeline_path=timeline_path,
            snapshots_dir=snapshots_dir,
            state_dir=resolved_state,
            symbol=symbol,
        )
        set_inference_counter_path(resolved_state / "inference_counter.json")

    async def load_checkpoint(self) -> bool:
        try:
            model_dir = self._symbol_dir
            if not model_dir.exists():
                log.warning(
                    "checkpoint_dir_not_found",
                    path=str(self._symbol_dir),
                    hint="create models/production/<symbol>/ with model.pkl, scaler.pkl, metadata.json",
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

            model_bytes = model_path.read_bytes()
            scaler_bytes = scaler_path.read_bytes()
            self._lr_model = pickle.loads(model_bytes)
            self._scaler = pickle.loads(scaler_bytes)
            self._model_checksum = hashlib.sha256(model_bytes).hexdigest()[:16]
            self._scaler_checksum = hashlib.sha256(scaler_bytes).hexdigest()[:16]
            self._metadata_checksum = hashlib.sha256(
                json.dumps(metadata, sort_keys=True).encode()
            ).hexdigest()[:16]

            # ── Model contract assertions ──
            if self._lr_model.classes_.tolist() != EXPECTED_CLASSES:
                raise RuntimeError(
                    f"Expected {EXPECTED_CLASSES}, got {self._lr_model.classes_.tolist()}. "
                    f"Only models trained with TARGET_SPEC_V1 encoding are accepted."
                )
            if self._lr_model.n_features_in_ != EXPECTED_FEATURES:
                raise RuntimeError(
                    f"Expected {EXPECTED_FEATURES} features, got {self._lr_model.n_features_in_}. "
                    f"Only models with TARGET_SPEC_V1 feature set are accepted."
                )
            mv = metadata.get("model_version", "")
            if not mv.startswith(EXPECTED_MODEL_VERSION_PREFIX):
                raise RuntimeError(
                    f"Unexpected model_version '{mv}'. "
                    f"Expected prefix '{EXPECTED_MODEL_VERSION_PREFIX}'."
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
            inference_start = time.monotonic()

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

            inference_sequence_id = self._tracker.next_sequence_id()

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

            try:
                import ta as ta_lib
                df = pd.DataFrame(ohlcv_buffer)
                adx_val = float(
                    ta_lib.trend.ADXIndicator(
                        df["high"], df["low"], df["close"], window=14,
                    ).adx().iloc[-1]
                )
                regime = "TRENDING" if adx_val >= 25.0 else "RANGING"
            except Exception:
                adx_val = -1.0
                regime = "UNKNOWN"

            log.info(
                "regime_detection",
                extra={
                    "adx": round(adx_val, 2),
                    "regime": regime,
                    "candles": len(ohlcv_buffer),
                }
            )

            # ── Longitudinal tracking ──
            try:
                timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                candle_open = float(ohlcv_buffer[-1].get("open", 0)) if ohlcv_buffer else 0.0
                features_map = self._last_raw_features or {}
                self._tracker.append(
                    sequence_id=inference_sequence_id,
                    timestamp_utc=timestamp_utc,
                    candle_open=candle_open,
                    candle_close=candle_close,
                    prob_sell=prob_sell,
                    prob_hold=prob_hold,
                    prob_buy=prob_buy,
                    decision=side.value if side != SignalSide.HOLD else "HOLD",
                    ema_fast=features_map.get("ema_fast"),
                    bb_middle=features_map.get("bb_middle"),
                    volume_sma=features_map.get("volume_sma"),
                    obv=features_map.get("obv"),
                    market_regime=regime,
                    position_open=False,
                    model_version=self._model_meta.get("model_version", ""),
                )
            except Exception:
                log.warning("tracking_append_failed")

            threshold_buy = float(
                self._model_meta.get("parameters", {}).get("thresholds", {}).get("BUY", 0.6)
            )
            threshold_sell = float(
                self._model_meta.get("parameters", {}).get("thresholds", {}).get("SELL", 0.4)
            )
            try:
                inference_elapsed_ms = (time.monotonic() - inference_start) * 1000
                log_inference(
                    symbol=self._symbol,
                    model_version=self._model_meta.get("model_version", ""),
                    model_checksum=self._model_checksum,
                    scaler_checksum=self._scaler_checksum,
                    metadata_checksum=self._metadata_checksum,
                    feature_names=list(cfg.features),
                    feature_values=features_raw[-1:],
                    lookahead=cfg.target_lookahead,
                    buy_threshold=threshold_buy,
                    sell_threshold=threshold_sell,
                    prob_sell=prob_sell,
                    prob_hold=prob_hold,
                    prob_buy=prob_buy,
                    predicted_class=class_idx,
                    final_signal=side.value if side != SignalSide.HOLD else "HOLD",
                    risk_decision="PENDING",
                    risk_reason="risk_evaluation_pending",
                    inference_latency_ms=inference_elapsed_ms,
                    inference_sequence_id=inference_sequence_id,
                    candle_close=candle_close,
                    candle_ts=candle_ts,
                    regime=regime,
                    position_open=False,
                )
            except Exception:
                log.warning("inference_log_write_failed")

            if side == SignalSide.HOLD:
                return StreamingInferenceResult(
                    signal=None,
                    ensemble_confidence=confidence,
                    model_version=self._model_meta.get("model_version", ""),
                    raw_probs=[prob_buy, prob_sell, prob_hold],
                    candle_close=candle_close,
                    candle_ts=candle_ts,
                    regime=regime,
                    inference_sequence_id=inference_sequence_id,
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
                inference_sequence_id=inference_sequence_id,
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
