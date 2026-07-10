# STARTUP_OBSERVABILITY_VALIDATION

**Date**: 2026-06-26T22:34 UTC
**Session**: `phase5_2026_06_26_001`
**Analytics Engine**: 3 containers healthy (analytics, data, broker)

## 1. Log File Creation

| Log File | Created | Lines | Valid JSON |
|----------|---------|-------|------------|
| `system.log` | ✅ | 898 | ✅ |
| `health.log` | ✅ | 9 | ✅ |
| `inference.log` | ✅ | 1 | ✅ |
| `orders.log` | ⬜ | — | No orders yet |
| `trades.log` | ⬜ | — | No trades yet |
| `errors.log` | ⬜ | — | No errors yet |

## 2. Structlog JSON Format

All log entries are valid JSON with ISO8601 UTC timestamps and structured key-value pairs.

### Sample inference record:

```json
{
  "symbol": "BTC/USDT",
  "model_version": "qv2_target_spec_v1_reduced_33_primary",
  "model_checksum": "c08d5ae6e98ebe60",
  "scaler_checksum": "554eccdfc237595c",
  "metadata_checksum": "373b2fa8c66a4898",
  "lookahead": 3,
  "buy_threshold": 0.5,
  "sell_threshold": 0.5,
  "prob_sell": 0.028074,
  "prob_hold": 0.937725,
  "prob_buy": 0.034202,
  "predicted_class": 1,
  "final_signal": "HOLD",
  "risk_decision": "SKIPPED",
  "inference_latency_ms": 476.211,
  "feature_hash": "de22bfd1ea3c85a0",
  "features": [
    {"name": "htf_obv_1d", "value": -10576845.0755},
    {"name": "htf_obv_4h", "value": -5631786.8195},
    {"name": "obv", "value": -4645527.1375},
    {"name": "htf_volume_sma_1d", "value": 1620658.570315},
    {"name": "htf_volume_sma_4h", "value": 371944.16946},
    {"name": "volume_sma", "value": 97324.23611},
    {"name": "close", "value": 59935.1},
    {"name": "bb_middle", "value": 59855.2},
    {"name": "ema_fast", "value": 59847.542501},
    {"name": "volume", "value": 27485.4021}
  ]
}
```

**Fields present**: symbol, model_version, checksums (model/scaler/metadata), lookahead, thresholds, all 3 probabilities, predicted class, final signal, risk decision, latency, feature hash, top 10 features by absolute value.

## 3. Correlation Context

- `session_id` bound at startup
- `inference_id` generated per inference cycle
- `decision_id` propagated through decision loop

## 4. Model Loading

- **Model**: LogisticRegression C=10.0
- **Version**: `qv2_target_spec_v1_reduced_33_primary`
- **Features**: 30 (reduced_33)
- **Scaler**: RobustScaler
- **Checksums**: model=c08d5ae6e98ebe60, scaler=554eccdfc237595c, metadata=373b2fa8c66a4898
- **Events**: `checkpoint_loaded`, `MODEL_LOADED` (fired)

## 5. Startup Events

- `composition_built` — mode=LIVE_SIMULATION
- `SYSTEM_START` — health.log, includes version=0.1.0, components={redis, exchange, streaming}
- `session_started` — session_id propagated

## 6. System Status

- **Mode**: LIVE_SIMULATION (paper trading)
- **Balance**: 5,000 USDT (testnet)
- **Positions**: 0 open
- **Pipeline**: ✅ loaded
- **Exchange**: ✅ connected
- **Redis**: ✅ connected
- **Recovery**: COMPLETED, gate=SAFE, runtime=NORMAL

## 7. Issues Fixed During Startup

| Issue | Fix |
|-------|-----|
| `PermissionError: /app/logs` | Made host logs/ dir world-writable (chmod 777). Added PermissionError fallback to /tmp/argos-logs. Added bind mount `./logs:/app/logs` and `ARGOS_LOG_DIR=/app/logs`. |
| `TypeError: BoundLogger.info() got multiple values for argument 'event'` | Removed duplicative first positional `event` arg from 4 log calls across `inference_logger.py`, `event_logger.py`, `health_logger.py` where the payload dict already contained an `"event"` key. |
| `checkpoint_dir_not_found: /models/btc_usdt` | Fixed `composition.py` to read `ARGOS_CHECKPOINT_DIR` env var (set to `/app/models`). Created `btc_usdt -> btc` symlink in models dir. |

## 8. Verdict

**OBSERVABILITY_VALIDATION: PASS** ✅

All 6 log file slots accounted for. JSON structure valid. Forensic fields populated. Correlation context active. Model loaded with full checksum verification.
