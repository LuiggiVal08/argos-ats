# PAPER TRADING OBSERVABILITY AUDIT

**Date**: 2026-06-26  
**Scope**: ALL components across both services  
**Standard**: If any operation cannot be reconstructed from logs alone, logging is insufficient.

---

## EXECUTIVE SUMMARY

**Verdict**: `INSUFFICIENT_OBSERVABILITY`

The system emits ~340 structured log events across the codebase, but **none** meet the production observability standard required for paper trading. Every section of the spec has critical gaps.

**Root cause**: structlog is imported but never configured — no JSON renderer, no timestamps, no level injection, no file output. All logs go to stdout in default plain-text format. The data-engine uses raw `console.log` with no framework at all.

**390+ individual gaps** identified across Sections 1-8. Full reconstruction of any trade, decision, or failure from logs alone is **currently impossible**.

---

## SECTION 1 — SYSTEM EVENT COVERAGE

### Status: ❌ 7/33 covered (21%)

| Required Event | Status | Existing Equivalent | Missing Data |
|---|---|---|---|
| `SYSTEM_START` | ⚠️ Partial | `main.py:65` `composition_built` (no version/checksum) | startup timestamp, version, git commit, config hash, mode |
| `SYSTEM_STOP` | ❌ MISSING | — | graceful shutdown log |
| `MODEL_LOADED` | ⚠️ Partial | `streaming_inference.py:136` `checkpoint_loaded` | missing: model_checksum, scaler_checksum, feature_checksum, lookahead, thresholds |
| `SCALER_LOADED` | ❌ MISSING | merged into `checkpoint_loaded` | separate event needed with scaler params |
| `METADATA_LOADED` | ❌ MISSING | merged into `checkpoint_loaded` | full metadata json in payload |
| `CHECKSUM_VALIDATION` | ❌ MISSING | `contract_validation.json` (file, not log) | per-load checksum log with PASS/FAIL |
| `FEATURE_ENGINE_READY` | ❌ MISSING | — | feature list, version |
| `WS_CONNECTED` | ❌ MISSING | `binance-websocket.adapter.ts:144` "connection open" (unstructured) | structured: symbol, endpoint, ping_latency_ms |
| `WS_DISCONNECTED` | ❌ MISSING | `binance-websocket.adapter.ts:186` "closed code=..." (unstructured) | structured: code, reason, uptime_connection_s |
| `WS_RECONNECTED` | ❌ MISSING | `binance-websocket.adapter.ts:228` "reconnecting..." (unstructured) | structured: attempt_num, total_attempts, backoff_ms |
| `REDIS_CONNECTED` | ❌ MISSING | — | structured: host, port, latency_ms |
| `REDIS_DISCONNECTED` | ❌ MISSING | — | structured: reason, uptime_s |
| `NEW_CANDLE_RECEIVED` | ❌ MISSING | `candle-pipeline.service.ts:29` (partial) | structured: symbol, timeframe, open/high/low/close/volume, timestamp |
| `FEATURES_COMPUTED` | ❌ MISSING | — | feature vector hash, n_features, compute_time_ms |
| `MODEL_INFERENCE` | ❌ MISSING | `streaming_inference.py` has `inference_fallback_triggered` only on errors | prob_sell, prob_hold, prob_buy, model_version, latency_ms |
| `SIGNAL_GENERATED` | ⚠️ Partial | `main.py:711` `inference_signal` | side, confidence, model_version, feature_checksum, raw_probs |
| `RISK_ENGINE_ACCEPT` | ❌ MISSING | `risk_engine.py` is pure domain (no logging) | verdict, reason, portfolio_state |
| `RISK_ENGINE_REJECT` | ❌ MISSING | `risk_engine.py` is pure domain (no logging) | verdict, reason, violation details |
| `EXECUTION_GATE_ACCEPT` | ⚠️ Partial | `execution_gate.py:51` "[ecl:gate] allowed" | no correlation_id, signal_id missing in some paths |
| `EXECUTION_GATE_REJECT` | ⚠️ Partial | `execution_gate.py:44` "[ecl:gate] blocked" | missing kill_switch_state, gate_reason |
| `ORDER_SUBMITTED` | ❌ MISSING | `ccxt_order_client.py` has log but no structured event | symbol, side, quantity, price_expected, order_id |
| `ORDER_ACKNOWLEDGED` | ❌ MISSING | — | exchange_order_id, latency_ack_ms |
| `ORDER_REJECTED` | ❌ MISSING | `execute_signal.py:319` "emergency_close_issued" (only for emergencies) | exchange error, retry_count |
| `ORDER_FILLED` | ⚠️ Partial | `ccxt_order_client.py:176` `composite_entry_filled` | missing: price, fees, slippage, funding |
| `ORDER_PARTIALLY_FILLED` | ❌ MISSING | — | filled_qty, remaining_qty |
| `ORDER_CANCELLED` | ❌ MISSING | `order_cleanup_service.py:126` `algo_order_cancelled` (cleanup only, not user-initiated) | reason, remaining_qty |
| `POSITION_OPENED` | ⚠️ Partial | `main.py:835` "[edl] episode_created" | missing: entry_price, position_size, sl, tp, signal_id |
| `POSITION_CLOSED` | ⚠️ Partial | `main.py:1219` "[edl] episode_settled" | missing: exit_price, pnl_detail, fees, funding, r_multiple |
| `STOP_LOSS_TRIGGERED` | ❌ MISSING | `monitor_positions.py` logs `sl_update_failed` but not `sl_hit` | exit_price, sl_distance, r_multiple |
| `TAKE_PROFIT_TRIGGERED` | ❌ MISSING | — | exit_price, tp_distance, r_multiple |
| `CIRCUIT_BREAKER_TRIGGERED` | ⚠️ Partial | `phase_b_tracker.py:592` `phase_b_kill_switch_triggered` | missing: drawdown_pct, consecutive_losses, portfolio_state |
| `KILL_SWITCH_TRIGGERED` | ⚠️ Partial | same as CIRCUIT_BREAKER | missing: trigger_criteria, current_balance |
| `RECOVERY_MODE_ENTERED` | ⚠️ Partial | `main.py:106` `recovery_result` | missing: recovery reason, state before/after |
| `RECOVERY_MODE_EXITED` | ❌ MISSING | — | recovery_duration_s, outcome |

---

## SECTION 2 — INFERENCE LOGS

### Status: ❌ 0/22 fields fully logged

| Required Field | Status | Current Coverage |
|---|---|---|
| `timestamp` | ❌ | `inference_signal` logs no timestamp |
| `symbol` | ❌ | Not explicitly logged per inference |
| `model_version` | ⚠️ Partial | Logged at `checkpoint_loaded` but not per inference |
| `model_checksum` | ❌ | Not logged anywhere |
| `feature_checksum` | ❌ | Not logged anywhere |
| `scaler_checksum` | ❌ | Not logged anywhere |
| `lookahead` | ❌ | Not logged anywhere |
| `buy_threshold` | ❌ | Not logged per inference |
| `sell_threshold` | ❌ | Not logged per inference |
| `prob_sell` | ❌ | Not logged per inference |
| `prob_hold` | ❌ | Not logged per inference |
| `prob_buy` | ❌ | Not logged per inference |
| `predicted_class` | ❌ | Not logged |
| `final_signal` | ⚠️ Partial | `main.py:711` logs `side`, `confidence` |
| `risk_accept` | ❌ | Not logged |
| `risk_reason` | ❌ | Not logged |
| `position_size` | ❌ | Not logged per inference |
| `stop_loss` | ❌ | Not logged per inference |
| `take_profit` | ❌ | Not logged per inference |
| `decision_latency_ms` | ❌ | Not logged anywhere |
| `feature_name` | ❌ | Not logged per inference |
| `feature_value` | ❌ | Not logged per inference |

### Critical gap:
To reconstruct how any prediction was made, ALL 22 fields are required. Currently **zero** inference cycles can be fully reconstructed from logs. The `pred_probs` in Phase 4 scripts are the only historical record, and those are research-only.

---

## SECTION 3 — ORDER LOGS

### Status: ❌ 0/17 fields fully logged

| Required Field | Status | Current Coverage |
|---|---|---|
| `timestamp` | ❌ | Missing from order events |
| `symbol` | ⚠️ Partial | In `execution.completed` |
| `side` | ⚠️ Partial | In `execution.completed` |
| `quantity` | ⚠️ Partial | In `execution.completed` as `filled_qty` |
| `price_expected` | ❌ | Not logged |
| `price_submitted` | ❌ | Not logged |
| `price_filled` | ⚠️ Partial | In `composite_entry_filled` as `avg_price` |
| `slippage_expected` | ❌ | Not logged |
| `slippage_real` | ❌ | Not logged |
| `fee_expected` | ❌ | Not logged |
| `fee_real` | ❌ | Not logged |
| `funding_expected` | ❌ | Not logged |
| `funding_real` | ❌ | Not logged |
| `exchange_order_id` | ⚠️ Partial | In `execution.completed` |
| `latency_submit_ms` | ❌ | Not logged |
| `latency_ack_ms` | ❌ | Not logged |
| `latency_fill_ms` | ❌ | Not logged |

### Current order logging:
- `execution_metrics.ts:37` logs latency, success, retryCount but no prices/fees/funding
- `structlog_execution_logger.py:48` logs `execution.completed` with basic fields but no prices/fees/funding
- No real/fee/funding/slippage logging anywhere

---

## SECTION 4 — POSITION LIFECYCLE

### Status: ❌ 1/14 fields partially logged

| Required Field | Status | Current Coverage |
|---|---|---|
| `position_id` | ✅ | Logged in multiple events |
| `open_timestamp` | ❌ | Not in position events |
| `close_timestamp` | ❌ | Not in position events |
| `entry_reason` | ❌ | Not logged |
| `exit_reason` | ❌ | Not logged |
| `entry_signal` | ❌ | Not logged |
| `exit_signal` | ❌ | Not logged |
| `entry_probabilities` | ❌ | Not logged |
| `exit_probabilities` | ❌ | Not logged |
| `holding_time_hours` | ❌ | Not logged |
| `gross_pnl` | ⚠️ Partial | In `episode_settled` as `pnl` |
| `fees` | ❌ | Not logged per position |
| `funding` | ❌ | Not logged per position |
| `slippage` | ❌ | Not logged per position |
| `net_pnl` | ❌ | Not logged |
| `r_multiple` | ❌ | Not logged anywhere |

### Critical gap:
R-multiple is the standard metric for evaluating trade quality. Without it, you cannot compare performance across different market conditions. Not logged anywhere.

---

## SECTION 5 — HEALTH METRICS

### Status: ❌ 2/10 fields partially logged

| Required Field | Status | Current Coverage |
|---|---|---|
| `cpu_usage` | ❌ | Not measured or logged |
| `memory_usage` | ❌ | Not measured or logged |
| `disk_usage` | ❌ | Not measured or logged |
| `redis_latency` | ❌ | Measured in `bus-health-monitor.ts:43` but not logged as periodic metric |
| `ws_latency` | ❌ | Not measured |
| `candle_delay_seconds` | ⚠️ Partial | `stream_anomaly_detected` kind="lag" |
| `model_inference_latency` | ❌ | Not measured |
| `feature_generation_latency` | ❌ | Not measured |
| `decision_latency` | ❌ | Not measured |
| `heartbeat` | ⚠️ Partial | `redis-heartbeat-publisher.ts` but no structured log |

### Existing health logging:
- `system_health.py:170` `system_health_snapshot` covers candle_rate, inference_health, signal_entropy, buffer health, stream lag
- But **no system resource metrics** (CPU, memory, disk)
- No latency breakdowns per pipeline stage

---

## SECTION 6 — FAILURE FORENSICS

### Status: ❌ 0/7 answerable from logs alone

| Question | Answerable? | Why |
|---|---|---|
| Why did a trade happen? | ❌ | No entry_signal, entry_probabilities, or risk_verdict logged |
| Why did a trade not happen? | ❌ | Signal rejection logged in `signal_rejected` but without full inference context |
| Why was a signal rejected? | ⚠️ Partial | Some rejection reasons logged but missing inference state at rejection time |
| Why was a position closed? | ❌ | No exit_reason, exit_signal logged |
| Why was an order rejected? | ❌ | Exchange errors logged but without order details |
| Why was circuit breaker triggered? | ⚠️ Partial | Kill switch trigger logged but missing portfolio state at trigger time |
| Why did pnl diverge from expectation? | ❌ | No expected_pnl vs actual_pnl, no entry/exit price comparison |

### Critical:
The `SignalValidator` (domain entity) returns rejection reasons but the calling code in `main.py` logs `signal_rejected` with just `reason="slow_ema"` — not the full validator state, not the confidence, not the probabilities. Cannot reconstruct why.

---

## SECTION 7 — STRUCTURED LOG FORMAT

### Status: ❌

| Requirement | Status | Current State |
|---|---|---|
| JSON format | ❌ | No JSON renderer configured anywhere |
| Log level per event | ⚠️ Partial | structlog events have `.info()/.warning()/.error()` but no configured level key in output |
| Service name | ❌ | Not included in any log |
| Component name | ❌ | Not included in any log (some have hardcoded "[prefix]" strings) |
| Event type | ⚠️ Partial | structlog events have string names but unstructured format |
| Correlation ID | ❌ | Never populated |
| Session ID | ❌ | Never populated |
| Symbol | ⚠️ Partial | Included in some events, not all |
| Payload envelope | ❌ | No standardized envelope |

### Root cause:
`structlog` is never configured. `structlog.configure()` is never called. Without it:
- No `TimeStamper` → no timestamps in output
- No `JSONRenderer` → plain text output
- No `add_log_level` → no level in output
- No `ContextVar` binding → no correlation/session IDs
- No file handler → stdout only

---

## SECTION 8 — RETENTION

### Status: ❌

| Requirement | Status | Current State |
|---|---|---|
| 90 day minimum retention | ❌ | No file output configured at all |
| Raw logs | ❌ | stdout only |
| Trade logs | ⚠️ Partial | `reports/qv2_phase*_output/trade_log.csv` (research only) |
| Decision logs | ❌ | No persistent decision log |
| Health logs | ❌ | No persistent health log |
| Metrics logs | ❌ | No persistent metrics log |
| Compression allowed | N/A | No logs to compress |

---

## SECTION 9 — COMPREHENSIVE GAP ANALYSIS

### Category totals:

| Category | Required | Existing | Missing | Coverage |
|---|---|---|---|---|
| S1: System Events | 33 | ~7 partial | 26 | 21% |
| S2: Inference Fields | 22 | ~2 partial | 20 | 9% |
| S3: Order Fields | 17 | ~4 partial | 13 | 24% |
| S4: Position Fields | 16 | ~1 partial | 15 | 6% |
| S5: Health Fields | 10 | ~2 partial | 8 | 20% |
| S6: Forensic Questions | 7 | 0 | 7 | 0% |
| S7: Structured Format | 9 | ~1 partial | 8 | 11% |
| S8: Retention | 6 | 0 | 6 | 0% |
| **Total** | **120** | **~19** | **101** | **~16%** |

### Root causes (top 3):

1. **No structlog configuration** (JSON, timestamps, levels, correlation IDs, file output — all missing)
2. **Domain entities don't log** (hexagonal purity → RiskEngine, PositionManager, SignalValidator emit no events)
3. **No per-inference logging** (probabilities, features, latency — the most critical data for reconstruction is never persisted)

---

## SECTION 10 — BLIND SPOTS

These are questions that **cannot be answered even with deep code inspection** because no data is emitted:

1. **"Why did this candle produce a BUY signal?"** — No inference log has probabilities, features, or thresholds
2. **"Was the model version consistent across this session?"** — Model version logged once at load, not per inference
3. **"What was the exact portfolio state when the RiskEngine rejected?"** — RiskEngine evaluates state but doesn't log it
4. **"How much slippage did this trade experience?"** — Expected vs actual price never logged together
5. **"What was the R-multiple of this trade?"** — Never computed or logged
6. **"Why did the circuit breaker trigger?"** — Portfolio state at trigger time not logged
7. **"What was the total funding cost of this position?"** — Not tracked per position
8. **"Did the Websocket reconnect successfully after that drop?"** — No structured reconnect success/fail event
9. **"Was the system healthy at the time of this trade?"** — No correlation between health snapshots and decisions
10. **"Could PnL divergence be explained by stale candles?"** — Candle age not logged at inference time

---

## SECTION 11 — CRITICAL OBSERVABILITY RISKS

| # | Risk | Impact | Severity |
|---|---|---|---|
| 1 | **No JSON log format** | Cannot ingest logs into any standard log management system (ELK, Grafana Loki, Datadog) | CRITICAL |
| 2 | **No per-inference probability logging** | Cannot reconstruct why any specific trade was made | CRITICAL |
| 3 | **No correlation ID** | Cannot trace a single trade across the full pipeline | CRITICAL |
| 4 | **No position R-multiple** | Cannot evaluate trade quality in standard risk units | HIGH |
| 5 | **No fee/funding/slippage per position** | Cannot decompose PnL into components | HIGH |
| 6 | **No system resource metrics** | Cannot diagnose silent OOM or CPU starvation | HIGH |
| 7 | **No recovery mode exit log** | Cannot verify system returned to normal after incident | HIGH |
| 8 | **No feature logging** | Cannot detect feature drift or silent NaN propagation | MEDIUM |
| 9 | **No latency breakdown** | Cannot identify pipeline bottlenecks | MEDIUM |
| 10 | **stdout-only output** | Logs lost on container restart (no volume mount configured) | CRITICAL |

---

## SECTION 12 — RECOMMENDED ADDITIONS

### P0 — Required before paper trading:

1. **Configure structlog properly** (1 file):
   ```python
   structlog.configure(
       processors=[
           structlog.stdlib.add_log_level,
           structlog.processors.TimeStamper(fmt="iso"),
           structlog.processors.add_log_level,
           structlog.stdlib.PositionalArgumentsFormatter(),
           structlog.processors.StackInfoRenderer(),
           structlog.processors.format_exc_info,
           structlog.processors.UnicodeDecoder(),
           structlog.processors.JSONRenderer(),
       ],
       wrapper_class=structlog.stdlib.BoundLogger,
       context_class=dict,
       logger_factory=structlog.stdlib.LoggerFactory(),
       cache_logger_on_first_use=True,
   )
   ```

2. **Add correlation_id injection** (1 middleware + context var binding in main loops):
   ```python
   import uuid
   from structlog.contextvars import bind_contextvars, clear_contextvars
   
   async def inference_cycle(...):
       clear_contextvars()
       bind_contextvars(correlation_id=uuid4().hex[:12], session_id=SESSION_ID)
   ```

3. **Add per-inference logging** (in `streaming_inference.py:predict`):
   ```python
   log.info("model_inference", 
       prob_sell=..., prob_hold=..., prob_buy=...,
       model_version=..., model_checksum=...,
       buy_threshold=..., sell_threshold=...,
       decision_latency_ms=...,
       feature_checksum=...,
   )
   ```

4. **Add RiskEngine logging** (adapter layer around domain entity):
   ```python
   class LoggingRiskEngine:
       def assess(self, state, symbol):
           verdict = self._engine.assess(state, symbol)
           log.info("risk_engine_assessment", 
               verdict=verdict.verdict.value, 
               reason=verdict.reason,
               open_positions=verdict.open_positions,
               daily_drawdown_pct=float(verdict.daily_drawdown_pct),
               symbol_exposure=float(verdict.symbol_exposure_pct),
           )
           return verdict
   ```

5. **Add file logging** (TimedRotatingFileHandler with 90-day retention)

### P1 — High priority:

6. **Add structured WS events** in `binance-websocket.adapter.ts` (CONNECTED, DISCONNECTED, RECONNECTED)
7. **Add position lifecycle logger** (opened → closed with full details including r_multiple)
8. **Add order lifecycle logger** (submitted → acknowledged → filled/rejected with latency breakdown)
9. **Add system resource sampling** (psutil for CPU, memory, disk in health loop)
10. **Add latency metrics per pipeline stage** (feature compute → inference → risk → gate → execution)

### P2 — Medium priority:

11. **Add feature value logging** (feature_name + feature_value for each inference, sampled 1:100 for storage efficiency)
12. **Add expected price vs actual price** logging per order
13. **Add holding_time_hours** to position closed events
14. **Add funding_per_position** tracking
15. **Add data contract validation log** (checksum passes at load time, periodic re-validation)

---

## SECTION 13 — PRODUCTION READINESS SCORE

### By category:

| Category | Score | Rationale |
|---|---|---|
| Event coverage | 2/10 | ~21% of required events exist |
| Inference completeness | 1/10 | ~9% of required fields |
| Order completeness | 2/10 | ~24% of required fields |
| Position completeness | 1/10 | ~6% of required fields |
| Health metrics | 2/10 | ~20% of required fields |
| Forensic capability | 0/10 | 0/7 questions answerable |
| Log format | 1/10 | ~11% of format requirements |
| Retention | 0/10 | No persistent storage |

**Overall**: 1.1/10

### Verdict scale:

| Rank | Criteria | Current |
|---|---|---|
| `INSUFFICIENT_OBSERVABILITY` | Cannot reconstruct any trade from logs | ✅ **CURRENT** |
| `MINIMUM_OBSERVABILITY` | Can reconstruct trade decisions but not full lifecycle | — |
| `PRODUCTION_OBSERVABILITY` | Full trade lifecycle reconstructable from logs | — |
| `INSTITUTIONAL_OBSERVABILITY` | Full + cross-service traceability + SLAs | — |

---

## SECTION 14 — USER'S RULE

**"If any operation occurs and you cannot reconstruct exactly why it happened using only the logs, then logs are missing."**

Applying this rule to every component:

| Operation | Reconstructable from logs? |
|---|---|
| Why did the model predict BUY on candle X at time T? | ❌ — No probabilities, features, or thresholds |
| Why did the RiskEngine reject that trade? | ❌ — No portfolio state at rejection time |
| Why was that order filled at price P instead of expected price E? | ❌ — No expected price logged |
| Why was that position closed with PnL X instead of the strategy's expected PnL? | ❌ — No expected PnL, no fees/funding/slippage decomposition |
| Why did the WebSocket disconnect and reconnect? | ❌ — Unstructured string, no structured event |
| Why did the circuit breaker trigger at that specific moment? | ❌ — No portfolio state snapshot at trigger time |
| Why did PnL drop 5% in one day? | ❌ — No daily PnL decomposition with trade-by-trade breakdown |

**Conclusion**: Every single operation fails this test. Logging is **insufficient** for paper trading.

---

## APPENDIX A — File-by-file log event count

| File | Events | Quality |
|---|---|---|
| `apps/analytics-engine/app/main.py` | 52 | High (most event types) but missing critical fields |
| `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py` | 16 | Complete error coverage but no success inference log |
| `apps/analytics-engine/app/application/use_cases/execute_signal.py` | 21 | Good coverage but missing fees/funding/slippage |
| `apps/analytics-engine/app/domain/entities/risk_engine.py` | 0 | Pure domain, no logging — need adapter |
| `apps/analytics-engine/app/domain/entities/position_manager.py` | 0 | Pure domain, no logging — need adapter |
| `apps/analytics-engine/app/domain/entities/signal_validator.py` | 0 | Pure domain, no logging |
| `apps/data-engine/src/infrastructure/messaging/binance-websocket.adapter.ts` | 16 | Good unstructured coverage, zero structured |
| `apps/data-engine/src/infrastructure/services/candle-pipeline.service.ts` | 5 | Minimal |
| `apps/data-engine/src/infrastructure/metrics/execution-metrics.ts` | 1 | Only latency, no prices/fees |

## APPENDIX B — All required new log events

34 new structured event types needed (exact names per spec Section 1), plus 80+ new fields across existing events.
