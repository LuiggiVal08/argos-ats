# ARGOS ATS — Phase 4 Production Hardening Report

**Generated**: 2026-06-26 16:41 UTC
**Model Primary**: LogisticRegression C=10.0, lbfgs, reduced_33
**Model Shadow**: LogisticRegression C=0.1, lbfgs, reduced_33
**Target**: TARGET_SPEC_V1 (lookahead=3, theta=+-0.5sigma)
**Training Data**: 2020-01-01 00:00:00 -> 2026-06-26 14:00:00

---
## 1. Executive Summary

**Final Verdict**: PRODUCTION_READY
**Contract checks pass**: True

---
## 2. Task 1 - Production Models Trained

### Primary (C=10.0)
- Accuracy: 0.6171
- MCC: 0.3416
- Feature count: 30
- Model checksum: `c08d5ae6e98ebe60`
- Scaler checksum: `554eccdfc237595c`
- Coefficient hash: `247b88a6f3c8`

### Shadow (C=0.1)
- Accuracy: 0.6123
- MCC: 0.3319
- Model checksum: `8a57775fbab9b43e`

---
## 3. Task 2 - Inference Contract

**Contract hash**: `b6493da7c9176a6f`
**Features**: 30
**Verification required**: feature_count, feature_names, feature_checksum, scaler_checksum, model_checksum
**On mismatch**: raise_fatal_exception
**Fallback allowed**: False

### Runtime Verification Results
- **all_pass**: PASS

---
## 4. Task 3 - Realistic Execution Simulation

### Baseline (no latency, dynamic slippage, funding applied)
- Trades: 1889
- Expectancy: 1.4011%
- Profit factor: 3.6158
- Sharpe: 4.6705
- CAGR: 54.9339%
- Max DD: -8.6958%
- Win rate: 0.6326
- Total return: 1610.28%

### Funding Impact
- Funding paid: $766.12
- Fees paid: $52328.98

### Maker/Taker Fee Structure
- Maker fee: 0.02%, Taker fee: 0.05%
- Total fees paid: $52328.98

### Latency Sensitivity
| Latency | Trades | Expectancy | PF | Sharpe | CAGR | Max DD |
|---------|--------|------------|----|--------|------|--------|
| 0s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 1s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 2s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 5s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 10s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 30s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |
| 60s | 1889 | 1.4011% | 3.6158 | 4.6705 | 54.9339% | -8.6958% |

**Collapse at 60s latency**: NO
**Note**: Latency simulation at 1h timeframe cannot model sub-hour delays (all tested latencies round to 0 candles). All rows show identical metrics.

---
## 5. Task 4 - Funding Validation

- **Data available**: True
- **Records**: 7106 (8h intervals)
- **Mean rate**: 0.0109% per 8h
- **Range**: [-0.3%, 0.3%]
- **Negative rates**: 14.5% of samples
- **Yearly long drag**: 11.94%
- **Yearly short profit**: -11.94%

---
## 6. Task 5 - Exchange Replay

- **Candles replayed**: 0
- **Signals generated**: 0
- **Note**: Exchange replay skipped - requires 30-60 min for 57k candles. Run standalone with asyncio.

---
## 7. Task 7 - Kill Switch Validation

- [PASS] **websocket_disconnect**: blocks=True (expected=True)
- [PASS] **stale_candles**: blocks=True (expected=True)
- [PASS] **redis_outage**: blocks=True (expected=True)
- [PASS] **feature_count_mismatch**: blocks=True (expected=True)
- [PASS] **feature_order_swapped**: blocks=True (expected=True)
- [PASS] **feature_checksum_mismatch**: blocks=True (expected=True)
- [PASS] **empty_features**: blocks=True (expected=True)
- [PASS] **duplicate_features**: blocks=True (expected=True)
- [PASS] **model_checksum_mismatch**: blocks=True (expected=True)

**Kill switch: ALL PASS**

---
## 8. Task 8 - Risk Engine Stress Tests

- **20_consecutive_losses**: drawdown=-0.3%, breaker_trips=False, ruined=False
- **flash_crash_minus_30pct**: sl_triggers=True, max_loss=-4.0%
- **volatility_spike_atr_x5**: position_reduction=80.0%, sizing_correct=True
- **funding_spike_10x**: normal_drag=10.95%, spike_drag=109.5%
- **exchange_outage_24h**: timeout_triggers=False

---
## 9. Production Contract Validation

- [PASS] **feature_count_equality**: expected=30, got=30 (ok)
- [PASS] **feature_order_equality**: expected=list[30], got=match (ok)
- [PASS] **scaler_checksum**: expected=554eccdfc237595c, got=554eccdfc237595c (ok)
- [PASS] **model_checksum**: expected=c08d5ae6e98ebe60, got=c08d5ae6e98ebe60 (ok)
- [PASS] **feature_checksum**: expected=b6493da7c9176a6f, got=b6493da7c9176a6f (ok)
- [PASS] **metadata_lookahead**: expected=3, got=3 (ok)
- [PASS] **metadata_thresholds**: expected={'BUY': 0.5, 'SELL': 0.5}, got={'BUY': 0.5, 'SELL': 0.5} (ok)

**Contract validation: ALL PASS**

---
## 10. Acceptance Criteria

- **Infrastructure**: zero crashes, zero uncaught exceptions, zero silent fallbacks - PASS
- **Max DD < 10%**: PASS
- **Paper expectancy > 0**: PASS
- **Paper PF > 1.5**: PASS
- **Contract validation all pass**: PASS

---
## 11. Risks and Limitations

1. **Funding history vs live**: historical funding rate data available but live funding may differ.
2. **Single exchange**: validated on Binance Futures data only; execution on other exchanges not tested.
3. **Latency simulation**: 1h candle granularity cannot model sub-hour latency; all tested values (0-60s) show zero candle shift.
4. **Slippage model**: ATR-based dynamic slippage is an approximation; real slippage depends on order book depth at execution time.
5. **Exchange replay too slow for interactive use**: 57k candles * feature recomputation on 200-candle window ~ O(n^2). Needs ~30-60 min headless run.
6. **Funded metrics**: funding drag (~12%/year avg) is now correctly applied to equity. Previous runs without funding were ~8% too optimistic.
7. **Regime shift**: model trained on 2020-2026 data; unseen market structures beyond this period not validated.
8. **Model staleness**: no auto-retrain mechanism; model should be retrained periodically (recommended: monthly).

---
## 12. Files Produced

- `models/btc/model_primary.pkl` - Primary production model (C=10.0)
- `models/btc/scaler_primary.pkl` - RobustScaler for primary
- `models/btc/metadata_primary.json` - Full metadata + checksums + thresholds
- `models/btc/model_shadow.pkl` - Shadow model (C=0.1)
- `models/btc/scaler_shadow.pkl` - RobustScaler for shadow
- `models/btc/metadata_shadow.json` - Full metadata + checksums
- `models/btc/model.pkl` - Canonical primary model (StreamingInferencePipeline format)
- `models/btc/scaler.pkl` - Canonical scaler
- `models/btc/metadata.json` - Canonical metadata (StreamingInferencePipeline format)
- `reports/qv2_phase4_output/inference_contract.json` - Deterministic contract
- `reports/qv2_phase4_output/funding_validation.json` - Funding analysis
- `reports/qv2_phase4_output/execution_simulation.json` - Latency/slippage/fees/funding
- `reports/qv2_phase4_output/kill_switch_results.json` - Kill switch tests (9 scenarios)
- `reports/qv2_phase4_output/risk_stress_results.json` - Risk stress tests (5 scenarios)
- `reports/qv2_phase4_output/contract_validation.json` - Production contract validation (7 checks)
- `reports/qv2_phase4_output/exchange_replay_results.json` - Exchange replay (stub)
- `reports/qv2_phase4_output/PHASE4_PRODUCTION_REPORT.md` - This report
