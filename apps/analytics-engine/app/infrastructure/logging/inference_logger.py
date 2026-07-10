"""Inference forensics logger — per-prediction structured logging.

Every inference cycle emits a structured JSON record containing all
fields required to reproduce the prediction offline::

    {
        "event": "model_inference",
        "timestamp": "2026-06-26T12:00:00.000000Z",
        "symbol": "BTC/USDT",
        "model_version": "qv2_v1",
        "model_checksum": "a1b2c3d4e5f6",
        "scaler_checksum": "f6e5d4c3b2a1",
        "metadata_checksum": "c3d4e5f6a1b2",
        "feature_checksum": "b2a1f6e5d4c3",
        "lookahead": 3,
        "buy_threshold": 0.50,
        "sell_threshold": 0.50,
        "prob_sell": 0.12,
        "prob_hold": 0.23,
        "prob_buy": 0.65,
        "predicted_class": 2,
        "final_signal": "BUY",
        "risk_decision": "APPROVED",
        "risk_reason": "all_checks_passed",
        "position_size": 0.015,
        "stop_loss": 65432.10,
        "take_profit": 71234.50,
        "inference_latency_ms": 4.2,
        "feature_hash": "sha256:abc...",
        "features": [
            {"name": "rsi", "value": 62.5},
            {"name": "macd_hist", "value": 0.0032},
            ...
        ],
        "correlation": { ... }
    }
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import numpy as np

from .logging_config import get_inference_logger
from .correlation import bind_correlation_context

INFERENCE_LOG = get_inference_logger()
_FEATURE_HASH = hashlib.sha256


def _compute_feature_hash(
    feature_names: list[str],
    feature_values: np.ndarray,
) -> str:
    """Compute SHA256 of the full feature vector for reproducibility."""
    h = _FEATURE_HASH()
    h.update(json.dumps(feature_names, sort_keys=True).encode())
    h.update(feature_values.tobytes())
    return h.hexdigest()[:16]


def log_inference(
    *,
    symbol: str,
    model_version: str,
    model_checksum: str,
    scaler_checksum: str,
    metadata_checksum: str | None = None,
    feature_names: list[str],
    feature_values: np.ndarray | None = None,
    lookahead: int,
    buy_threshold: float,
    sell_threshold: float,
    prob_sell: float,
    prob_hold: float,
    prob_buy: float,
    predicted_class: int,
    final_signal: str,
    risk_decision: str,
    risk_reason: str = "",
    position_size: float = 0.0,
    stop_loss: float = 0.0,
    take_profit: float = 0.0,
    inference_latency_ms: float = 0.0,
    feature_hash: str | None = None,
    top_n_features: int = 10,
    inference_sequence_id: str = "",
    **extra: Any,
) -> None:
    """Log a complete inference forensic record.

    Args:
        symbol: Trading pair.
        model_version: Model version string.
        model_checksum: Model binary hash.
        scaler_checksum: Scaler binary hash.
        metadata_checksum: Metadata JSON hash.
        feature_names: List of feature names in model order.
        feature_values: Raw feature vector (before scaling) for this inference.
        lookahead: Target lookahead in candles.
        buy_threshold: Minimum probability for BUY signal.
        sell_threshold: Minimum probability for SELL signal.
        prob_sell: Predicted probability of SELL class.
        prob_hold: Predicted probability of HOLD class.
        prob_buy: Predicted probability of BUY class.
        predicted_class: Argmax class index (0=SELL, 1=HOLD, 2=BUY).
        final_signal: Final signal after risk checks (BUY/SELL/HOLD).
        risk_decision: RiskEngine verdict.
        risk_reason: RiskEngine reason.
        position_size: Position size in base asset (0 if no entry).
        stop_loss: Stop loss price (0 if no entry).
        take_profit: Take profit price (0 if no entry).
        inference_latency_ms: End-to-end inference latency.
        feature_hash: Pre-computed feature hash (or None to auto-compute).
        top_n_features: Number of top features to include by absolute value.
        **extra: Additional fields to include.
    """
    payload: dict[str, Any] = {
        "event": "model_inference",
        "symbol": symbol,
        "model_version": model_version,
        "model_checksum": model_checksum,
        "scaler_checksum": scaler_checksum,
        "metadata_checksum": metadata_checksum or "",
        "feature_checksum": feature_hash or "",
        "inference_sequence_id": inference_sequence_id,
        "lookahead": lookahead,
        "buy_threshold": buy_threshold,
        "sell_threshold": sell_threshold,
        "prob_sell": round(prob_sell, 6),
        "prob_hold": round(prob_hold, 6),
        "prob_buy": round(prob_buy, 6),
        "predicted_class": int(predicted_class),
        "final_signal": final_signal,
        "risk_decision": risk_decision,
        "risk_reason": risk_reason,
        "position_size": round(position_size, 8),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "inference_latency_ms": round(inference_latency_ms, 3),
    }

    # Feature values — hash + top N
    if feature_values is not None and len(feature_names) > 0:
        if feature_hash is None:
            fhash = _compute_feature_hash(feature_names, feature_values)
        else:
            fhash = feature_hash
        payload["feature_hash"] = fhash

        flat = np.asarray(feature_values).flatten()
        n = min(len(feature_names), len(flat))
        top_n = min(top_n_features, n)
        if n > 0:
            pairs = [(abs(flat[i]), i) for i in range(n)]
            pairs.sort(reverse=True)
            payload["features"] = [
                {"name": feature_names[idx], "value": round(float(flat[idx]), 6)}
                for _, idx in pairs[:top_n]
            ]

    payload.update(extra)
    INFERENCE_LOG.info(**payload)


def log_inference_error(
    *,
    symbol: str,
    model_version: str = "",
    error: str,
    inference_latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    """Log an inference failure."""
    payload = {
        "event": "model_inference_error",
        "symbol": symbol,
        "model_version": model_version,
        "error": error,
        "inference_latency_ms": round(inference_latency_ms, 3),
    }
    payload.update(extra)
    INFERENCE_LOG.error(**payload)
