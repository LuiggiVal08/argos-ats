# ARGOS Smoke Test Report — 2026-06-19

## ⏱️ Status: 🔴 DEGRADED (No full pipeline executed)

| Item | Result | Detail |
|---|---|---|
| **Test duration** | 45 min (setup + diagnostics) | Pipeline never fully executed |
| **LIVE_SIMULATION mode** | ⚠️ Partial | Config updated, container stuck at PAPER_TRADING (needs restart) |
| **Ticks flowing** | ✅ 59/s | 4.4M ticks in 20h, stable |
| **Candle builder** | ✅ 42 x 5m candles | Buffer at 100%, building continuously |
| **Model checkpoint** | ✅ Found | Path `/nonexistent/.novaquant/checkpoints/BTC_USDT/v1.0.1781356847` |
| **Pipeline loaded** | ❌ Failed | Needs tensorflow, xgboost, sklearn — not in Docker image |
| **Signals produced** | ❌ 0 | Pipeline never loaded |
| **Orders executed** | ❌ 0 | No signals → no execution |
| **Fills received** | ❌ 0 | No orders → no fills |
| **Circuit breaker** | 🔴 HALTED | Drawdown check returning True (in-memory state) |
| **Contract violations** | ✅ 0 | No contract streams running to violate |
| **Redis streams** | ⚠️ Only 2 | `ticks:btcusdt` + `notifications:events` — no contract streams |
| **Live drift watchdog** | ❌ Error | Binance testnet/sandbox not supported for futures anymore |

## 🔍 Critical Issues Found

### 1. Missing ML Dependencies in Docker Image
```log
lstm_load_failed -> TensorFlow is required
xgboost_load_failed -> XGBoost is required
calibrator_load_failed -> No module named 'sklearn'
```
**Fix**: Add `tensorflow`, `xgboost`, `scikit-learn` to `pyproject.toml` and rebuild image.

### 2. Circuit Breaker Halted at Boot
`drawdown_halted_skipping_position_check` fires every 5s from startup.
**Cause**: `InMemorySnapshotRepo` may have stale state or `is_halted()` returning True incorrectly.
**Diagnosis needed**: Check if snapshot repo has persisted drawdown >5% from previous session.

### 3. Binance Testnet Futures Deprecation
```log
fetch_positions_failed: BTC/USDT: binance testnet/sandbox mode is not supported for futures anymore
```
**Fix**: Use Binance demo trading endpoint or switch to a different testnet provider.

### 4. Contract Pipeline Not Wired
Only `ticks:btcusdt` and `notifications:events` streams exist in Redis.
Missing streams: `market:candles:*`, `signals:trading`, `orders:execution`, `fills:execution`, `system:*`.
**Fix**: Integrate RCE modules (redis-candle-publisher, redis-order-consumer, etc.) into NestJS bootstrap.

### 5. LIVE_SIMULATION Mode Requires Restart
`config.json` and `.env` updated but running processes are in PAPER_TRADING.
**Fix**: `docker compose restart analytics-engine` after fixing #1.

## ✅ What Works

### Infrastructure
- Data Engine: OK (200) — producing ticks from Binance WS at 59/s
- Analytics Engine: OK (200) — FastAPI serving health, running 7 streaming loops
- Broker (Redis 7.4.9): OK — 4.4M ticks, 10 connections, 672MB used
- All 3 containers healthy and stable for 21h

### Streaming Loops (AE)
| Loop | Status |
|---|---|
| `_tick_to_candle_loop` | ✅ Active — building 1m candles from ticks |
| `_decision_loop` | ⚠️ Running — blocked by pipeline not loaded |
| `_position_monitor_loop` | ⚠️ Running — reports halted |
| `_health_snapshot_loop` | ✅ Running — emits every 10min |
| `_stream_idle_check_loop` | ✅ Running — detects bursts/activity |
| `_phase_b_loop` | ✅ Running — tracking 0 trades |
| `_system_diagnostics_loop` | ✅ Running — reports degraded state |

### System Health Snapshot (last)
```json
{
  "inference_health": "no_model",
  "signals": 0,
  "executions": 0,
  "buffer_pct": 100.0,
  "stream_lag_ms": 5000,
  "hold_ratio": 0.0,
  "signal_entropy": 0.0,
  "exec_activity": "low"
}
```

## 🛑 Safety Gates Review

| Gate | Status | Notes |
|---|---|---|
| Contract violation rate | N/A | No contract streams running |
| 5 consecutive exec errors | N/A | No execution attempted |
| Redis disconnect | ✅ Pass | Redis stable for 20h |
| AE/DE crash | ✅ Pass | Both containers healthy |
| Fill timeout | N/A | No orders sent |
| Checkpoint update | N/A | Checkpoint dir exists, but no writer active |

## 📋 Action Items

1. **Rebuild Docker image** with ML dependencies (`tensorflow`, `xgboost`, `scikit-learn`)
2. **Fix circuit breaker** — investigate why `is_halted()` returns True at fresh boot
3. **Wire contract pipeline** — integrate `redis-candle-publisher` + `redis-order-consumer` + `redis-fill-publisher` into DE NestJS bootstrap
4. **Switch to Binance demo** — replace deprecated testnet/sandbox endpoint
5. **Run full smoke test** after items 1-4 are resolved, with LIVE_SIMULATION mode and 15-min duration

## 📁 Output Files

| File | Content |
|---|---|
| `system_metrics.json` | Redis info, tick count, memory |
| `system_health.json` | DE + AE health endpoints, container status |
| `latency.json` | Recent tick samples from Redis |
| `ae_log_summary.json` | AE log event counts (errors, warnings, triggers) |
| `state_check.json` | Checkpoint schema and report files |
| `summary.md` | This file |
