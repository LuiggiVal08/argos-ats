# LIVE vs RESEARCH PROBABILITY DISTRIBUTION AUDIT

**Audit ID**: LIVE_vs_RESEARCH_v1
**Date**: 2026-06-27
**Mode**: LIVE_SIMULATION
**Symbol**: BTC/USDT
**Model**: qv2_target_spec_v1_reduced_33_primary (LogisticRegression, C=10.0)
**Feature set**: reduced_33 (30 features)
**Scaler**: RobustScaler
**Lookahead**: 3
**Thresholds**: BUY=0.50, SELL=0.50

---

## A1 — Historical Probability Distribution (Phase 3.9)

Source: `reports/archive/qv2/qv2_phase39_output/predictions.csv`
Total inferences: **43,824** (walk-forward validation, 10 folds, BTC/USDT 1h, 2020-01 → 2026-06)

### P(SELL) — Research

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.238051 |
| Std       | 0.182224 |
| Median    | 0.205900 |
| P5        | 0.001700 |
| P25       | 0.106400 |
| P50       | 0.205900 |
| P75       | 0.329000 |
| P95       | 0.595800 |
| P99       | 0.841254 |
| Min       | 0.000000 |
| Max       | 0.999900 |

### P(HOLD) — Research

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.540992 |
| Std       | 0.161984 |
| Median    | 0.553450 |
| P5        | 0.236000 |
| P25       | 0.479200 |
| P50       | 0.553450 |
| P75       | 0.614500 |
| P95       | 0.822785 |
| P99       | 0.997000 |
| Min       | 0.000100 |
| Max       | 1.000000 |

### P(BUY) — Research

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.220957 |
| Std       | 0.194280 |
| Median    | 0.168800 |
| P5        | 0.008400 |
| P25       | 0.083700 |
| P50       | 0.168800 |
| P75       | 0.299500 |
| P95       | 0.630485 |
| P99       | 0.916754 |
| Min       | 0.000000 |
| Max       | 0.999900 |

---

## A2 — Live Probability Distribution

Source: `logs/inference.log`
Total inferences: **12** (2026-06-27, hourly BTC/USDT)

### P(SELL) — Live

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.040447 |
| Std       | 0.009937 |
| Median    | 0.037210 |
| P5        | 0.029210 |
| P25       | 0.036316 |
| P50       | 0.037210 |
| P75       | 0.043771 |
| P95       | 0.055989 |
| P99       | 0.063704 |
| Min       | 0.027590 |
| Max       | 0.065633 |

### P(HOLD) — Live

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.933274 |
| Std       | 0.013464 |
| Median    | 0.936379 |
| P5        | 0.915587 |
| P25       | 0.923898 |
| P50       | 0.936379 |
| P75       | 0.939092 |
| P95       | 0.951181 |
| P99       | 0.955226 |
| Min       | 0.906306 |
| Max       | 0.956237 |

### P(BUY) — Live

| Statistic | Value    |
| --------- | -------- |
| Mean      | 0.026279 |
| Std       | 0.004984 |
| Median    | 0.026427 |
| P5        | 0.018013 |
| P25       | 0.025294 |
| P50       | 0.026427 |
| P75       | 0.028684 |
| P95       | 0.032381 |
| P99       | 0.034035 |
| Min       | 0.016174 |
| Max       | 0.034448 |

---

## A3 — Distribution Comparison Metrics

### KL Divergence KL(P_research || Q_live)

| Class   | KL     | Interpretation                               |
| ------- | ------ | -------------------------------------------- |
| P(SELL) | 21.835 | Extremely high — near-disjoint distributions |
| P(HOLD) | 23.398 | Extremely high — near-disjoint distributions |
| P(BUY)  | 21.641 | Extremely high — near-disjoint distributions |

### Jensen-Shannon Divergence

| Class   | JS Div | JS Distance (sqrt) |
| ------- | ------ | ------------------ |
| P(SELL) | 0.579  | 0.761              |
| P(HOLD) | 0.666  | 0.816              |
| P(BUY)  | 0.565  | 0.751              |

### Population Stability Index (PSI)

| Class   | PSI    | Threshold    | Verdict          |
| ------- | ------ | ------------ | ---------------- |
| P(SELL) | 15.805 | >0.2 = drift | **SEVERE DRIFT** |
| P(HOLD) | 20.384 | >0.2 = drift | **SEVERE DRIFT** |
| P(BUY)  | 14.349 | >0.2 = drift | **SEVERE DRIFT** |

### Wasserstein-1 Distance

| Class   | Distance |
| ------- | -------- |
| P(SELL) | 0.202    |
| P(HOLD) | 0.394    |
| P(BUY)  | 0.196    |

### Kolmogorov-Smirnov Test

| Class   | D-statistic | p-value  | Verdict              |
| ------- | ----------- | -------- | -------------------- |
| P(SELL) | 0.835       | 8.45e-10 | **DRIFT** (p < 0.05) |
| P(HOLD) | 0.961       | 2.18e-17 | **DRIFT** (p < 0.05) |
| P(BUY)  | 0.887       | 8.50e-12 | **DRIFT** (p < 0.05) |

### Z-test for Means (Live vs Research)

| Class   | Research μ | Live μ | Z-score | Verdict |
| ------- | ---------- | ------ | ------- | ------- | --- | ------ |
| P(SELL) | 0.238      | 0.040  | -3.76   | DRIFT ( | z   | >1.96) |
| P(HOLD) | 0.541      | 0.933  | +8.39   | DRIFT ( | z   | >1.96) |
| P(BUY)  | 0.221      | 0.026  | -3.47   | DRIFT ( | z   | >1.96) |

---

## A4 — Histogram Comparison

### P(SELL) Histogram (10 bins, % of total)

| Bin     | Research (%) | Live (%) | Delta  |
| ------- | ------------ | -------- | ------ |
| 0.0-0.1 | 15.68        | 0.00     | -15.68 |
| 0.1-0.2 | 29.19        | 0.00     | -29.19 |
| 0.2-0.3 | 24.42        | 0.00     | -24.42 |
| 0.3-0.4 | 14.60        | 0.00     | -14.60 |
| 0.4-0.5 | 7.39         | 0.00     | -7.39  |
| 0.5-0.6 | 4.92         | 0.00     | -4.92  |
| 0.6-0.7 | 2.20         | 0.00     | -2.20  |
| 0.7-0.8 | 1.02         | 0.00     | -1.02  |
| 0.8-0.9 | 0.37         | 0.00     | -0.37  |
| 0.9-1.0 | 0.20         | 100.00   | +99.80 |

### P(HOLD) Histogram (10 bins, % of total)

| Bin     | Research (%) | Live (%) | Delta  |
| ------- | ------------ | -------- | ------ |
| 0.0-0.1 | 0.00         | 0.00     | 0.00   |
| 0.1-0.2 | 0.06         | 0.00     | -0.06  |
| 0.2-0.3 | 0.92         | 0.00     | -0.92  |
| 0.3-0.4 | 6.27         | 0.00     | -6.27  |
| 0.4-0.5 | 19.88        | 0.00     | -19.88 |
| 0.5-0.6 | 36.00        | 0.00     | -36.00 |
| 0.6-0.7 | 20.92        | 0.00     | -20.92 |
| 0.7-0.8 | 8.56         | 0.00     | -8.56  |
| 0.8-0.9 | 5.03         | 0.00     | -5.03  |
| 0.9-1.0 | 2.36         | 100.00   | +97.64 |

### P(BUY) Histogram (10 bins, % of total)

| Bin     | Research (%) | Live (%) | Delta  |
| ------- | ------------ | -------- | ------ |
| 0.0-0.1 | 18.71        | 100.00   | +81.29 |
| 0.1-0.2 | 28.75        | 0.00     | -28.75 |
| 0.2-0.3 | 21.13        | 0.00     | -21.13 |
| 0.3-0.4 | 12.67        | 0.00     | -12.67 |
| 0.4-0.5 | 7.18         | 0.00     | -7.18  |
| 0.5-0.6 | 4.96         | 0.00     | -4.96  |
| 0.6-0.7 | 3.14         | 0.00     | -3.14  |
| 0.7-0.8 | 1.81         | 0.00     | -1.81  |
| 0.8-0.9 | 0.88         | 0.00     | -0.88  |
| 0.9-1.0 | 0.77         | 0.00     | -0.77  |

---

## A5 — Threshold Crossing Analysis

### Research (Phase 3.9, n=43,824)

| Threshold              | Crossings | Rate   |
| ---------------------- | --------- | ------ |
| P(BUY) > 0.50          | 3,986     | 9.10%  |
| P(SELL) > 0.50         | 3,838     | 8.76%  |
| Any > 0.50             | 7,824     | 17.85% |
| Actual trades executed | 1,848     | 4.22%  |

Note: The gap between threshold crossings (17.85%) and actual trades (4.22%) is due to:

- Additional exit rules (ATR-based stop loss, opposite signal exit, 7-day timeout)
- Risk checks (drawdown circuit breaker, risk per trade limits)
- Trade density from opposing signals in adjacent periods

### Live (n=12)

| Threshold              | Crossings | Rate  |
| ---------------------- | --------- | ----- |
| P(BUY) > 0.50          | 0         | 0.00% |
| P(SELL) > 0.50         | 0         | 0.00% |
| Any > 0.50             | 0         | 0.00% |
| Actual trades executed | 0         | 0.00% |

### Statistical Significance

**Binomial test**: P(0 trades | n=12, p_trade=0.0422) = 0.596 (59.6%)

The probability of observing 0 trades after 12 inferences, assuming the historical trade rate applies, is **59.6%**. This is NOT statistically significant at conventional thresholds (α=0.05). With n=12, the expected number of trades is only **0.51**.

| N inferences | Expected trades | P(0 trades) |
| ------------ | --------------- | ----------- |
| 12           | 0.51            | 59.6%       |
| 24           | 1.01            | 35.5%       |
| 50           | 2.11            | 12.1%       |
| 100          | 4.22            | 1.4%        |
| 200          | 8.44            | 0.02%       |

**Conclusion**: With n=12, observing 0 trades is not in itself evidence of drift. The distribution SHAPE (not just the trade count) is what indicates a problem.

---

## A6 — Feature Distribution Drift

### Observed Live Feature Values (top 10, n=12)

| Feature           | Mean        | Std    | Last Value  |
| ----------------- | ----------- | ------ | ----------- |
| htf_obv_1d        | -10,574,437 | 971    | -10,572,707 |
| htf_obv_4h        | -5,629,532  | 1,162  | -5,627,649  |
| obv               | -4,643,883  | 671    | -4,642,523  |
| htf_volume_sma_1d | 1,507,905   | 49     | 1,507,991   |
| htf_volume_sma_4h | 322,747     | 16,495 | 295,780     |
| volume_sma        | 62,778      | 16,821 | 35,693      |
| close             | 60,450      | 162    | 60,840      |
| ema_fast          | 60,243      | 155    | 60,501      |
| bb_middle         | 60,003      | 117    | 60,233      |
| htf_macd_1d       | -2,293      | 9      | -2,277      |

Note: Full 30-feature distribution comparison requires raw feature access from both research and live pipelines. This audit extracted the 10 features logged in live inference logs. A full pipeline-parity comparison (A6 complete) requires extracting raw features from the research parquet files and live feature computation via `TaDataPreprocessor`.

---

## Key Findings Summary

1. **Contract is intact**: Model, scaler, thresholds, lookahead, feature set all match between deployed metadata and live inference logs.

2. **Distribution is qualitatively different**: Live P(HOLD) mean (0.933) is at the **96.7th percentile** of the research P(HOLD) distribution. Only **3.9%** of research samples have P(HOLD) ≥ live's minimum P(HOLD) of 0.906.

3. **All divergence metrics flag severe drift**: PSI values of 14-20 (threshold: >0.2), KS p-values < 1e-9, Z-scores of -3.76 to +8.39.

4. **0 trades is statistically plausible with n=12** but the probability distribution collapse is evident even at this small sample size.

5. **Most likely cause**: LogisticRegression probability saturation due to feature values at extreme percentiles of the RobustScaler training distribution. When scaled features fall outside the range seen during training, sigmoid outputs saturate toward 0 or 1, and since most weight coefficients point toward HOLD (the majority class), P(HOLD) → 0.9-1.0.

---

## Data Sources

| Data                 | Path                                                       | Records   |
| -------------------- | ---------------------------------------------------------- | --------- |
| Research predictions | `reports/archive/qv2/qv2_phase39_output/predictions.csv`   | 43,824    |
| Research trade log   | `reports/archive/qv2/qv2_phase39_output/trade_log.csv`     | 1,848     |
| Live inference log   | `logs/inference.log`                                       | 12        |
| Model metadata       | `models/production/btc/metadata.json`                      | 1         |
| Model registry       | `registry/models.yaml`                                     | 5 models  |
| Phase 3.9 report     | `reports/archive/qv2/qv2_phase39_output/PHASE39_REPORT.md` | 243 lines |
