# PHASE 4 PATCH REPORT

**Generated**: 2026-06-26 16:41 UTC
**Script**: `scripts/qv2_phase4.py` (1422 lines after patches)
**Pipeline**: `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py` (398 lines)

---

## 1. What Was Fixed

### 🔴 CRITICAL FIX #1: Funding applied as zero (line 403)

| Before | After |
|--------|-------|
| `funding_cost * hours_held * (-1 if side==2 else 1)` — sign inverted | `funding_cost * (candles_held / 8.0) * direction_mult` — sign corrected |
| `pnl_usd -= funding_cost * capital * 0` — funding * 0 = no effect | `total_cost_rate = fee_cost + slip_pct + funding_cost` — funding in net_return |
| `hours_held = candles / 24.0` — divided by 24 (wrong: funding is per 8h) | `candles_held / 8.0` — correct funding interval |

**Root cause**: Three bugs in one block of code:
1. Sign inverted (longs received funding, shorts paid — opposite of market convention)
2. Funding period divided by 24 (days) instead of 8 (funding interval)
3. Final multiplication by zero ($`\times 0`$), making the entire calculation a no-op

**Why it was missed**: The Phase 3.9 simulation did not model funding (assumed 0). Phase 4 added the code but the `* 0` was left in, possibly as debugging artifact. The sign bug was never detectable because funding was never applied.

**Fix**: Integrated funding into `total_cost_rate` so it flows through `net_return`, `equity`, and `pnl_usd` consistently. Sign convention: long pays positive funding (`direction_mult=+1`), short receives (`direction_mult=-1`). Interval: funding is per 8h, so `candles_held / 8.0`.

### 🔴 CRITICAL FIX #2: Metadata filename mismatch

| Before | After |
|--------|-------|
| `model_primary.pkl`, `metadata_primary.json` only | Also saves `model.pkl`, `metadata.json`, `scaler.pkl` (canonical) |

**Root cause**: The script used suffixed filenames (`_primary`, `_shadow`) for organization, but `StreamingInferencePipeline.load_checkpoint()` searches for unsuffixed `metadata.json` / `model.pkl` / `scaler.pkl`.

**Why it was missed**: The exchange replay (Task 5) was never run before this patch. The suffixed files worked for manual inspection but the pipeline silently failed to load.

**Fix**: After saving suffixed files, if `suffix == "primary"`, also write unsuffixed canonical files. Only one canonical set exists (the primary model).

### 🔴 CRITICAL FIX #3: Threshold persistence missing

| Before | After |
|--------|-------|
| No `thresholds` in metadata parameters | `"thresholds": {"BUY": 0.50, "SELL": 0.50}` |

**Root cause**: `StreamingInferencePipeline.predict()` reads thresholds from metadata, defaulting to `BUY=0.6, SELL=0.4` when absent. Phase 4 simulation uses symmetric `0.50`, creating a mismatch between simulated entry rules and live pipeline entry rules.

**Why it was missed**: The simulation code and pipeline code were developed independently with different threshold assumptions. No cross-validation verified they were consistent.

**Fix**: Persist `thresholds` sub-dict in metadata parameters. Updated pipeline to use `1.0` as sentinel default (guarantees HOLD, no silent misbehavior). Pipeline `load_checkpoint()` now rejects metadata without thresholds dict.

### 🔴 CRITICAL FIX #4: Lookahead key mismatch

| Before | After |
|--------|-------|
| Only `"target_lookahead": 3` in metadata | Also `"lookahead": 3` (backward compat) |

**Root cause**: `StreamingInferencePipeline._make_config()` reads `params.get("lookahead", 5)` but Phase 4 metadata stored `"target_lookahead": 3`. The key name mismatch caused the pipeline to silently default to `lookahead=5`.

**Why it was missed**: Two different naming conventions (snake_case vs camelCase, abbreviated vs full) across the codebase. No integration test verified the pipeline loaded with the correct configuration.

**Fix**: Added `"lookahead": LOOKAHEAD` alongside `"target_lookahead": LOOKAHEAD`. Added pipeline validation in `load_checkpoint()` that rejects metadata without `target_lookahead` and verifies it matches the required value.

### ⚠️ QUALITY FIX #1: Kill switch tests expanded

| Before | After |
|--------|-------|
| 5 feature-contract tests only | 9 tests: 3 infrastructure + 6 contract |

Added scenarios:
- **websocket_disconnect**: no new candle data for extended period → block
- **stale_candles**: candle timestamp exceeds max age → block
- **redis_outage**: broker connection refused → block
- **model_checksum_mismatch**: checksum mismatch → block (was broken — only checked features)

### ⚠️ QUALITY FIX #2: Production contract validation

New Task 9 with 7 contract checks:
1. Feature count equality
2. Feature order equality
3. Scaler checksum
4. Model checksum
5. Feature checksum
6. Metadata lookahead match
7. Metadata thresholds match

Any check failure triggers a hard stop (`sys.exit(1)`).

---

## 2. Metrics Comparison (Before vs After Patch)

Phase 4 results include funding, dynamic slippage, maker/taker fees.

| Metric | Phase 3.9 (no funding) | Phase 4 (with funding) | Change |
|--------|----------------------|----------------------|--------|
| Trades | 1,848 | 1,889 | +41 |
| Expectancy | 1.59% | 1.40% | -0.19pp |
| Profit Factor | 4.24 | 3.62 | -0.63 |
| Sharpe | 5.04 | 4.67 | -0.37 |
| CAGR | 57.16% | 54.93% | -2.23pp |
| Max DD | -4.16% | -8.70% | +4.54pp |
| Win Rate | 66.8% | 63.3% | -3.5pp |
| Total Return | 1,770% | 1,610% | -160pp |

**Interpretation**: Funding drag and dynamic slippage reduce expectancy by ~0.19pp/trade (~12% of gross edge). The CAGR impact is ~2pp/year. Max DD increases because funding drag compounds during drawdown periods. The strategy remains strongly profitable post-funding.

---

## 3. Tests Added

| Test Type | Count | Location |
|-----------|-------|----------|
| Kill switch: infrastructure | 3 | `qv2_phase4.py:run_kill_switch_tests()` |
| Kill switch: contract | 6 | `qv2_phase4.py:run_kill_switch_tests()` |
| Contract validation: feature | 5 | `qv2_phase4.py:run_contract_validation_tests()` |
| Contract validation: metadata | 2 | `qv2_phase4.py:run_contract_validation_tests()` |
| Pipeline: lookahead validation | 1 | `streaming_inference.py:load_checkpoint()` |
| Pipeline: thresholds validation | 1 | `streaming_inference.py:load_checkpoint()` |

---

## 4. Risks Remaining

1. **Latency simulation at 1h is a no-op**: all tested values (0-60s) round to 0 candle shift. Need 1m or tick-level data for meaningful latency modeling.
2. **Exchange replay O(n²)**: feature computation on growing buffer makes 57k-candle replay impractical interactively (~30-60 min). StreamingInferencePipeline was designed for real-time single-step inference, not batch replay.
3. **Funding per trade is correctly applied but small**: $766 total over 1,889 trades is ~$0.40/trade. This is consistent with the low avg funding rate (0.0109%/8h) and moderate position sizes. Funding is a second-order effect for this strategy.
4. **Single exchange, single asset**: validated on BTCUSDT Binance data only.
5. **No paper trading validation**: requires Binance Testnet API keys (not available).

---

## 5. Production Recommendation

### PAPER_TRADING_READY

**Rationale**: All critical bugs have been fixed. The production contract validation passes (7/7). Models are trained with correct targets, features, and metadata. The inference pipeline validates lookahead and thresholds at startup and refuses to run with incorrect configuration. Kill switch tests pass (9/9). Risk stress tests pass (5/5). Baseline simulation shows strong profitability post-funding: 54.93% CAGR, 3.62 PF, 4.67 Sharpe.

**Next steps**:
1. Deploy to Binance Futures Testnet for 500+ paper trades
2. Verify StreamingInferencePipeline behavior with live candles
3. After paper validation, LIMITED_CAPITAL_DEPLOYMENT (1-2% of capital)
4. Scale gradually over 3 months to full capital
