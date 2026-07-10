# LIVE FEATURE DRIFT REPORT

**Audit ID**: LIVE_vs_RESEARCH_v1
**Date**: 2026-06-27
**Mode**: LIVE_SIMULATION
**Symbol**: BTC/USDT

---

## Scope

This report compares feature distributions between the research dataset (Phase 3.9, 2020-01 → 2026-06) and current live inference features. Due to read-only constraints during the audit, the comparison is performed on the 10 features logged in `logs/inference.log` (the top 10 features by absolute magnitude per inference, as selected by the inference logger).

A complete 30-feature drift analysis requires:
1. Extracting raw feature values from the research parquet files (`apps/analytics-engine/data/btc_usdt_1h.parquet`)
2. Computing all 30 `reduced_33` features via `TaDataPreprocessor.build_features()` on the full research dataset
3. Capturing all 30 live feature values (the inference logger only logs top 10 by absolute magnitude)

---

## Top-10 Features — Live Statistics

Data source: `logs/inference.log` (12 inferences, 2026-06-27)

| Feature | Mean | Std | Min | Max | Last |
|---------|------|------|------|------|------|
| htf_obv_1d | -10,574,437 | 971 | -10,576,214 | -10,572,707 | -10,572,707 |
| htf_obv_4h | -5,629,532 | 1,162 | -5,631,799 | -5,627,649 | -5,627,649 |
| obv | -4,643,883 | 671 | -4,645,001 | -4,642,523 | -4,642,523 |
| htf_volume_sma_1d | 1,507,905 | 49 | 1,507,816 | 1,507,995 | 1,507,991 |
| htf_volume_sma_4h | 322,747 | 16,495 | 295,780 | 354,488 | 295,780 |
| volume_sma | 62,778 | 16,821 | 35,693 | 91,544 | 35,693 |
| close | 60,450 | 162 | 60,237 | 60,611 | 60,840 |
| ema_fast | 60,243 | 155 | 59,936 | 60,416 | 60,501 |
| bb_middle | 60,003 | 117 | 59,877 | 60,232 | 60,233 |
| htf_macd_1d | -2,293 | 9 | -2,307 | -2,277 | -2,277 |

---

## Feature Stability Assessment

### Stable Features (low variance over 12 samples)

| Feature | CV (std/mean) | Assessment |
|---------|--------------|------------|
| htf_volume_sma_1d | 0.003% | Very stable — daily volume SMA changes slowly |
| close | 0.27% | Low variance — BTC in narrow range $60K-$60.6K |
| ema_fast | 0.26% | Tracks close |
| bb_middle | 0.20% | Slow-moving average |
| htf_macd_1d | -0.38% | Low relative variance |

### Volatile Features (high variance)

| Feature | CV (std/mean) | Assessment |
|---------|--------------|------------|
| volume_sma | 26.8% | High variance — intraday volume patterns change significantly |
| htf_volume_sma_4h | 5.1% | Moderate variance |
| htf_obv_4h | -0.02% | Low relative variance (absolute magnitude large) |

---

## Known Limitation: Top-10 Logging Bias

The inference logger (`infrastructure/logging/inference_logger.py`) only records the **top 10 features by absolute value** per inference. This introduces a systematic bias:

- Features with consistently large magnitudes (OBV, volume SMA, close, etc.) are always logged
- Features with small or variable magnitudes (rsi, macd_hist, adx, pct_change, zscore_close, etc.) are **systematically under-reported**

The following 20 features from `reduced_33` are NOT captured in the live logs:

```
volume, rsi, macd_hist, adx, pct_change,
htf_rsi_4h, htf_macd_hist_4h, htf_adx_4h, htf_pct_change_4h,
htf_rsi_1d, htf_macd_hist_1d, htf_adx_1d, htf_pct_change_1d,
htf_atr_1d, macd, atr, htf_macd_4h,
low, high, rolling_std_24, zscore_close, close_lag_3,
rolling_std_12, trend_regime, bb_lower, bb_upper, ema_medium
```

---

## Recommended Full Analysis

To complete a proper PSI/K-S drift analysis on all 30 features:

```python
# Step 1: Extract research features
import pandas as pd
research_df = pd.read_parquet("apps/analytics-engine/data/btc_usdt_1h.parquet")
# Apply TaDataPreprocessor to generate all 30 reduced_33 features

# Step 2: Extract live features
# Modify inference_logger to log ALL 30 feature values (not just top 10)
# OR run a separate script that loads production model + scaler and
# computes features from the live candle buffer

# Step 3: For each of 30 features, compute:
# - Research: mean, std, p5, p25, p50, p75, p95
# - Live: mean, std, p5, p25, p50, p75, p95  
# - PSI (10 bins)
# - KS statistic + p-value
# - Rank by PSI to identify top drift contributors
```

---

## Preliminary PSI Estimate (Available Features Only)

Since only 10 features are logged and the logging is biased by magnitude, a proper PSI cannot be computed without the full feature matrix. However, based on the probability distribution collapse observed in the main audit:

The **probability-level drift** (PSI 14-20 for P(SELL), P(HOLD), P(BUY)) is consistent with **feature-level drift** affecting the LogisticRegression sigmoid calibration. Specifically, if one or more key features (e.g., `close`, `volume_sma`, `atr`) fall outside the RobustScaler's training interquartile range, the scaled features will saturate the sigmoid.

The RobusScaler centers by median and scales by IQR. If current market conditions produce feature values beyond the IQR observed during training (2020-01 to 2026-06), the model's uncertainty calibration breaks down.

---

## Data Sources

| Data | Path |
|------|------|
| Live inference log | `logs/inference.log` |
| Retained features list | `apps/analytics-engine/data/retained_features.txt` |
| Model metadata (feature order) | `models/production/btc/metadata.json` |
| Research feature definitions | `apps/analytics-engine/archive/legacy_data_export/features/` |
| Research full feature data | `apps/analytics-engine/data/btc_usdt_1h.parquet` |
