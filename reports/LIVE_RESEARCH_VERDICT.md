# LIVE vs RESEARCH — FINAL VERDICT

**Audit ID**: LIVE_vs_RESEARCH_v1
**Date**: 2026-06-27
**Mode**: LIVE_SIMULATION
**Symbol**: BTC/USDT
**Model**: qv2_target_spec_v1_reduced_33_primary

---

## Verdict

> ## MODERATE_DISTRIBUTION_SHIFT

---

## Decision Matrix

| Criterion                    | Evidence                             | Score               |
| ---------------------------- | ------------------------------------ | ------------------- | ------------------ | ------------ |
| Contract integrity           | 10/10 checks pass                    | ✅ **Intact**       |
| Binomial test (0 trades)     | p=0.596 — not significant (n=12)     | ✅ **Plausible**    |
| KS test (all 3 classes)      | p<1e-9 — distributions are different | ❌ **Drift**        |
| PSI (all 3 classes)          | 14-20 >> 0.2 threshold               | ❌ **Severe drift** |
| Mean shift (Z-test)          |                                      | Z                   | =3.5-8.4 — extreme | ❌ **Drift** |
| Live P(HOLD) in research     | 96.7th percentile                    | ❌ **Extreme tail** |
| Live P(SELL/BUY) in research | 9-12th percentile                    | ❌ **Extreme tail** |
| Sample size                  | n=12 — underpowered                  | ⚠️ **Limited**      |

**Weighted decision**: The distribution is clearly shifted (all divergence metrics confirm), but the contract is intact and 0 trades out of 12 is statistically plausible. The shift is moderate — not severe enough to conclude the alpha has disappeared, but too large to ignore.

---

## Key Evidence

### 1. Contract Intact

```
research_model_checksum == production_model_checksum ✅
research_scaler_checksum == production_scaler_checksum ✅
```

### 2. Live P(HOLD) is Abnormally High

| Metric         | Research | Live      |
| -------------- | -------- | --------- |
| Mean P(HOLD)   | 0.541    | **0.933** |
| Std P(HOLD)    | 0.162    | **0.013** |
| Min P(HOLD)    | 0.000    | **0.906** |
| P(HOLD) > 0.90 | 2.36%    | **100%**  |

Live P(HOLD) mean sits at the **96.7th percentile** of the research distribution.

### 3. Live P(BUY) and P(SELL) are Extremely Low

| Metric              | P(SELL) Live | P(BUY) Live |
| ------------------- | ------------ | ----------- |
| Mean                | 0.040        | 0.026       |
| Max                 | 0.066        | 0.034       |
| Research percentile | 12th         | 9th         |

### 4. No Thresholds Crossed

In research, threshold crossings (P > 0.50) occur in **17.85%** of hourly decisions. In 12 live inferences, there were **zero** crossings.

### 5. 0 Trades is Plausible with n=12

P(0 trades | n=12, p_trade=0.0422) = **59.6%** — expected trades = 0.51.

---

## Phase 3.9 Expected Trade Frequency

From Phase 3.9 economic validation:

| Metric                | Value |
| --------------------- | ----- |
| Trades over 6.5 years | 1,848 |
| Trades/year           | 285.3 |
| Trades/month          | 23.8  |
| Trades/day            | 0.78  |
| % of hourly decisions | 3.20% |

Note: The trade rate of 3.20% (1,848/≈57,600 hourly candles) differs slightly from the threshold crossing rate of 17.85% because Phase 3.9 uses additional exit rules (stop loss, opposite signal exit, timeout) that filter out many threshold-crossing signals. The entry rate at threshold 0.50 is ~17.85% of hourly decisions, but only ~3.20% result in executed trades — meaning approximately 5 of every 6 threshold crossings do not result in a trade due to existing position constraints and exit rules.

The accuracy figure (0.6171) and mean max probability (0.5921) from the training metrics further confirm that the research model was **not** highly confident — on average it assigned only ~59% probability to its top class. Live predictions assign **>90%** to HOLD every time, which is inconsistent with the research model's calibration.

---

## Root Cause Analysis (Read-Only Assessment)

### Most Likely: Feature Scale Drift

LogisticRegression with RobustScaler is sensitive to feature values outside the training IQR:

1. `RobustScaler` computes: `X_scaled = (X - median) / IQR`
2. If live feature values (especially `close`, `volume_sma`, `atr`, `obv`) are at extreme percentiles of the training distribution, scaled values explode
3. LogisticRegression sigmoid saturates: `σ(w·x) → 0` or `σ(w·x) → 1`
4. Since most weight coefficients point toward HOLD (the majority class in training), P(HOLD) → 1.0

The training range was 2020-01 to 2026-06. If BTC at $60K (June 2026) represents an extreme value relative to that 6.5-year range, this would explain the behavior.

### Second Likely: Feature Pipeline Asymmetry

The research pipeline computes features via `TaDataPreprocessor.build_features()` with a specific lookback window and NaN handling. If the live pipeline diverges in:

- Candle buffer length or alignment
- Column naming conventions
- NaN replacement strategy
- Lookback window parameter

...the generated feature vectors would differ, potentially pushing the model into unseen regions of feature space.

### Less Likely: Market Regime Change

The current BTC market may genuinely have no statistical edge for the model. This is testable by:

- Running the model on held-out 2026 data from the research pipeline
- Comparing feature distributions between 2020-2025 (training) and 2026 (live)

### Unlikely: Model Corruption

Model checksum matches exactly. The binary on disk is the same as the one used in research.

---

## Recommended Next Steps

| Priority | Action                                                      | Rationale                                             |
| -------- | ----------------------------------------------------------- | ----------------------------------------------------- |
| **P1**   | Collect 100+ more live inferences                           | Current n=12 is underpowered for definitive diagnosis |
| **P2**   | Capture all 30 feature values in live logs                  | Current top-10 logging misses 2/3 of features         |
| **P3**   | Compare scaled feature values against training distribution | Confirm/rule out RobustScaler saturation hypothesis   |
| **P4**   | Run research pipeline on 2026 data only                     | Isolate market-regime effect from pipeline-effect     |
| **P5**   | Add feature-level PSI monitoring to observability           | Early detection of future drift                       |

---

## References

| Document                        | Path                                                                      |
| ------------------------------- | ------------------------------------------------------------------------- |
| Phase 3.9 Report                | `reports/archive/qv2/qv2_phase39_output/PHASE39_REPORT.md`                |
| Phase 3.9 Predictions           | `reports/archive/qv2/qv2_phase39_output/predictions.csv`                  |
| Phase 3.9 Metrics               | `reports/archive/qv2/qv2_phase39_output/metrics.json`                     |
| Phase 3.9 Threshold Sensitivity | `reports/archive/qv2/qv2_phase39_output/threshold_sensitivity.json`       |
| Live Inference Log              | `logs/inference.log`                                                      |
| Production Metadata             | `models/production/btc/metadata.json`                                     |
| Model Registry                  | `registry/models.yaml`                                                    |
| Streaming Inference Code        | `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py` |
| Inference Logger                | `apps/analytics-engine/app/infrastructure/logging/inference_logger.py`    |
