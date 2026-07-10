# H12 — Live Prediction Distribution Report

> Based on 12 live inferences (2026-06-27 03:00–16:00 UTC) + research baseline (43,824–56,647 candles)

---

## TL;DR

**Severe distribution shift confirmed across all three probability channels.** Live P(HOLD) mean is 0.933 vs research 0.541 — a ~72% absolute increase. PSI values of 14–20 (threshold for "severe" is 0.2) indicate the live probability distribution has almost no overlap with the research distribution. The model is collapsing to near-certain HOLD on every inference.

---

## 1. Class distribution

### 1.1 Training target (ground truth labels)

Source: `research/qv2/feature_selection/target_analysis_output/tables/summary.csv`

| Class | % of training samples |
|-------|----------------------|
| SELL  | 23.5% |
| HOLD  | 51.4% |
| BUY   | 25.1% |

Target: TARGET_SPEC_V1, ternary, lookahead=3, threshold=0.5σ, vol-adjusted.

### 1.2 Research inference distribution (model predictions)

Source: `reports/archive/qv2/qv2_phase4_output/REPLAY_CONSISTENCY_REPORT.md` — 56,647 candles

| Signal | Count | % |
|--------|-------|---|
| HOLD   | 45,170 | 79.74% |
| BUY    | 6,239  | 11.01% |
| SELL   | 5,238  | 9.25% |

Source: `reports/archive/qv2/qv2_phase39_output/PHASE39_REPORT.md` — 43,824 walk-forward predictions (>0.50 threshold)

| Signal | Count | % |
|--------|-------|---|
| HOLD   | 36,000 | 82.14% |
| BUY    | 3,986  | 9.10% |
| SELL   | 3,838  | 8.76% |

### 1.3 Live inference distribution

Source: `reports/active/paper_trading/inference_timeline.csv` — 12 inferences

| Decision | Count | % |
|----------|-------|---|
| HOLD     | 12    | 100% |
| BUY      | 0     | 0% |
| SELL     | 0     | 0% |

### 1.4 Comparison matrix

| Source | n | SELL | HOLD | BUY | %TRADE |
|--------|---|------|------|-----|--------|
| Training target | 56,647 | 23.5% | 51.4% | 25.1% | 48.6% |
| Research (phase4) | 56,647 | 9.25% | 79.74% | 11.01% | 20.26% |
| Research (phase39) | 43,824 | 8.76% | 82.14% | 9.10% | 17.86% |
| **Live** | **12** | **0%** | **100%** | **0%** | **0%** |

---

## 2. Probability distribution

### 2.1 Research (n=43,824)

Source: `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A1

| Estadístico | P(SELL) | P(HOLD) | P(BUY) |
|-------------|---------|---------|--------|
| Mean | 0.238 | 0.541 | 0.221 |
| Std | 0.182 | 0.162 | 0.194 |
| Median | 0.206 | 0.553 | 0.169 |
| P95 | 0.596 | 0.823 | 0.630 |
| P99 | 0.841 | 0.997 | 0.917 |

### 2.2 Live (n=12)

Source: `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A2 (same 12 inferences as timeline CSV)

| Estadístico | P(SELL) | P(HOLD) | P(BUY) |
|-------------|---------|---------|--------|
| Mean | 0.040 | 0.933 | 0.026 |
| Std | 0.010 | 0.013 | 0.005 |
| Min | 0.028 | 0.906 | 0.016 |
| Max | 0.066 | 0.956 | 0.034 |

### 2.3 Live-only detail (each inference)

| INF | P(SELL) | P(HOLD) | P(BUY) | Decision |
|-----|---------|---------|--------|----------|
| 001 | 0.0276 | 0.9562 | 0.0162 | HOLD |
| 002 | 0.0305 | 0.9470 | 0.0224 | HOLD |
| 003 | 0.0337 | 0.9468 | 0.0195 | HOLD |
| 004 | 0.0372 | 0.9364 | 0.0264 | HOLD |
| 005 | 0.0372 | 0.9364 | 0.0264 | HOLD |
| 006 | 0.0372 | 0.9364 | 0.0264 | HOLD |
| 007 | 0.0372 | 0.9365 | 0.0263 | HOLD |
| 008 | 0.0418 | 0.9237 | 0.0344 | HOLD |
| 009 | 0.0463 | 0.9232 | 0.0306 | HOLD |
| 010 | 0.0481 | 0.9239 | 0.0280 | HOLD |
| 011 | 0.0429 | 0.9264 | 0.0307 | HOLD |
| 012 | 0.0656 | 0.9063 | 0.0281 | HOLD |

Observations:
- P(HOLD) ranges from 0.906 to 0.956 — **extremely concentrated**
- P(BUY) never exceeds 0.0344
- P(SELL) never exceeds 0.0656
- No threshold crossings (BUY threshold = 0.5, SELL threshold = 0.5)

---

## 3. PSI (Population Stability Index)

Source: `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A3

| Metric | P(SELL) | P(HOLD) | P(BUY) | Interpretation |
|--------|---------|---------|--------|----------------|
| **PSI** | **15.805** | **20.384** | **14.349** | Severe (>0.2 = flag, >0.5 = severe) |
| KL Divergence | 21.835 | 23.398 | 21.641 | Very high |
| JS Distance | 0.761 | 0.816 | 0.751 | Near-maximum divergence (max = 1.0) |
| KS D-stat | 0.835 | 0.961 | 0.887 | Extreme distribution separation |
| Z-score (means) | -3.76 | +8.39 | -3.47 | P(HOLD) shifted +8.4σ **up** |

**Verdict**: PSI >> 0.2 for all three classes. The live probability distribution is statistically unrelated to the research distribution.

---

## 4. Feature drift

Source: `reports/drift_report.json`, `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A6

### 4.1 Most drifted features

| Feature | Z-score | Severity |
|---------|---------|----------|
| htf_volume_sma_1d | 50.44 | EXTREME |
| htf_volume_sma_4h | 42.81 | EXTREME |
| volume_sma | 35.53 | EXTREME |
| volume | 25.45 | EXTREME |
| htf_obv_4h | 5.01 | HIGH |
| htf_obv_1d | 4.87 | MODERATE |
| obv | 4.24 | MODERATE |

### 4.2 Suspected root cause

All volume-based features show EXTREME drift (z-score 25–50). This matches the expected signature of:
- **Exchange migration**: research was trained on Binance spot data; live pipeline uses Binance USDⓈ-M futures
- Volume is structurally different between spot and futures (different liquidity pools)
- OBV inherits volume drift → cascading effect

---

## 5. Conclusions

### 5.1 The HOLD collapse is real

The model assigns P(HOLD) > 0.90 on every single live inference. This is not a threshold or filtering issue — it is a **probability calibration collapse**.

### 5.2 Likely causes (ordered by probability)

1. **Exchange data mismatch (spot → futures)**: volume-based features are the most drifted (z=25–50). Spot and futures have fundamentally different volumes. This cascades into OBV, volume_sma, and all HTF volume-based features.

2. **Model overconfidence on HOLD**: LogisticRegression with C=10.0 may produce extreme probabilities when feature values fall outside training range. Volume features at z=25–50 are completely unseen.

3. **No probability calibration (Platt/isotonic)**: The pipeline applies no calibration post-model. Raw LR probabilities are known to be poorly calibrated when the feature distribution shifts.

### 5.3 What PSI tells us

PSI(P(HOLD)) = 20.384 is catastrophic. In industry practice:
- PSI < 0.1: no change
- PSI 0.1–0.2: moderate
- PSI > 0.2: severe — investigate immediately

We are **100x** over the severe threshold.

### 5.4 What does NOT explain it

- **Regime UNKNOWN**: was a secondary issue, not causal (regime is metadata, not an input to the model)
- **Thresholds**: model never crosses 0.5 for BUY/SELL — but the probabilities themselves are wrong
- **Class imbalance**: training had 51.4% HOLD, live has 100% HOLD — the 48.6pp gap is too large for imbalance alone

---

## 6. Recommended next steps (in priority order)

1. **Collect 100+ live inferences** to confirm the distribution is stable (non-random)
2. **Compare spot vs futures feature distributions** — build a side-by-side of BTC/USDT (spot) vs BTC/USDT (perpetual futures) for the volume-based features
3. **Validate feature pipeline equivalence** — ensure the live pipeline produces the exact same features as research for the same candle
4. **Calibrate probabilities** — apply Platt scaling or isotonic regression on the research holdout set
5. **Only then consider** threshold changes or retraining

---

## 7. Data sources

| Source | Path |
|--------|------|
| Training metadata | `models/production/btc/metadata.json` |
| Training target distribution | `research/qv2/feature_selection/target_analysis_output/tables/summary.csv` |
| Research inference (phase4) | `reports/archive/qv2/qv2_phase4_output/REPLAY_CONSISTENCY_REPORT.md` |
| Research inference (phase39) | `reports/archive/qv2/qv2_phase39_output/PHASE39_REPORT.md` |
| Research prob distribution | `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A1 |
| Live prob distribution | `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A2 |
| PSI analysis | `reports/LIVE_RESEARCH_PROBABILITY_AUDIT.md` §A3 |
| Feature drift | `reports/drift_report.json`, `reports/drift_report.md` |
| Live timeline | `reports/active/paper_trading/inference_timeline.csv` |
