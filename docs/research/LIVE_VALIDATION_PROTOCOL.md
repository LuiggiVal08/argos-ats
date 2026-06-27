# LIVE VALIDATION PROTOCOL — Distribution Shift Investigation

**Status**: ACTIVE
**Start date**: 2026-06-27
**Minimum duration**: 100 inferences or 14 days continuous operation
**Model freeze**: ✅ Enforced — no modifications permitted during Phases 1-5

---

## Phase 1 — Collect minimum 100 inferences

**Goal**: Gather sufficient sample for statistical analysis.
**Duration**: ~4-5 days (hourly inference on BTC/USDT)

### Requirements
- [ ] Continue normal LIVE_SIMULATION operation
- [ ] Ensure `inference_timeline.csv` is appended on every inference
- [ ] Verify `inference_sequence_id` appears in `inference.log`
- [ ] No changes to model, thresholds, scaler, or features

### Entry criteria
- Pipeline running in LIVE_SIMULATION
- Contract verification passed

### Exit criteria
- At least 100 inferences recorded in timeline
- All rows in `inference_timeline.csv` have non-zero `candle_open` and `candle_close`

---

## Phase 2 — Store all 30 raw features

**Goal**: Capture the complete feature vector for offline analysis.

### Changes required (after Phase 1 completes)
- [ ] Modify `inference_logger.py` to log ALL 30 feature values (not just top 10)
- [ ] Add raw feature vector to a separate parquet/CSV file: `state/raw_features.csv`
- [ ] Columns: `sequence_id, timestamp, feature_01, ..., feature_30`

### Why
Current top-10 logging systematically omits low-magnitude features (rsi, adx, macd_hist, etc.) which are critical for drift detection.

### Exit criteria
- Raw features for Phase 1 inferences are backfilled
- All subsequent inferences log complete 30-feature vectors

---

## Phase 3 — Store all 30 scaled features

**Goal**: Capture the scaled feature vector (after RobustScaler) to detect saturation.

### Changes required (after Phase 2 completes)
- [ ] After `self._scaler.transform(features_raw)`, capture `features_norm[-1]` for logging
- [ ] Add scaled feature vector to file: `state/scaled_features.csv`
- [ ] Columns: `sequence_id, timestamp, scaled_01, ..., scaled_30`

### Why
RobustScaler saturation is the leading hypothesis. If scaled features fall outside the [-3, +3] range seen in training, the sigmoid will saturate. This is the definitive test.

### Exit criteria
- Scaled features for Phase 1-2 inferences are captured
- All subsequent inferences log complete scaled vectors

---

## Phase 4 — Compute per-feature drift metrics

**Goal**: Identify which specific features are drifting.

### For each of the 30 features, compute:

| Metric | Threshold | Interpretation |
|--------|-----------|----------------|
| PSI | > 0.2 | General distribution shift |
| KS statistic | p < 0.05 | Distribution shape change |
| Wasserstein distance | — | Magnitude of shift |
| Z-score of live mean vs research mean | \|z\| > 1.96 | Mean shift |

### Implementation
```python
for feature in reduced_33_features:
    research_values = extract_from_research_parquet(feature)
    live_values = load_live_features(feature)
    
    psi = compute_psi(research_values, live_values)
    ks_stat, ks_p = ks_2samp(research_values, live_values)
    wasser = wasserstein_distance(research_values, live_values)
    z = (mean_live - mean_research) / (std_research / sqrt(n_live))
    
    rank_by_psi.append((feature, psi))
```

### Exit criteria
- All 30 features ranked by PSI
- Top 5 drift contributors identified
- Report generated: `reports/active/paper_training/FEATURE_DRIFT_RANKING.md`

---

## Phase 5 — Compare live vs research

**Goal**: Determine whether the live distribution is compatible with research.

### Analysis

1. **Probability distribution comparison** (repeat of Phase 3.9 audit)
   - Compare live P(SELL), P(HOLD), P(BUY) distributions vs 43,824 research samples
   - Update KL, JS, PSI, Wasserstein metrics
   - Update KS test

2. **Threshold crossing analysis**
   - Compare actual crossing rate vs expected (17.85% P > 0.50)
   - Binomial test with updated sample size

3. **Expected trades projection**
   ```
   P(≥1 trade) = 1 − (1 − 0.0422)^n
   At n=100: P(≥1 trade) = 98.6%
   At n=50:  P(≥1 trade) = 87.9%
   ```

4. **Feature-level diagnosis**
   - If PSI > 0.2 on ≥ 3 features → feature drift confirmed
   - If scaled features outside [-5, +5] range → RobustScaler saturation
   - If no individual feature PSI > 0.1 → pipeline mismatch suspected

### Decision matrix

| Scenario | Verdict | Action |
|----------|---------|--------|
| Trade rate matches research | EXPECTED_BEHAVIOR | Continue monitoring |
| Feature drift < 3 features, probs shifted | MILD_DISTRIBUTION_SHIFT | Monitor, no action |
| Feature drift ≥ 3 features, probs shifted | MODERATE_DISTRIBUTION_SHIFT | Investigate feature pipeline |
| Scaled features outside [-5, +5] | SEVERE_DISTRIBUTION_SHIFT | Consider re-scaling or model retraining |
| Checksums mismatch | CONTRACT_MISMATCH | Emergency redeployment |

---

## Operational constraints

### Prohibited during Phases 1-5

| Action | Reason |
|--------|--------|
| Change BUY/SELL thresholds | Would contaminate the experiment |
| Recalibrate probabilities | Would mask the distribution shift |
| Modify scaler | Would prevent RobustScaler saturation diagnosis |
| Change feature definitions | Would break contract with research |
| Add calibrators | Would alter the observable distribution |
| Modify model architecture | Invalidates the entire experiment |

### Permitted

| Action | Reason |
|--------|--------|
| Read inference logs | Passive observation |
| Generate reports | Documentation |
| Add logging/tracking | Measurement instrumentation |
| Run backtests on historical data | Research, no production impact |
| Monitor broker streams | Observability |

---

## Timeline estimate

| Phase | Duration | Cumulative |
|-------|----------|------------|
| Phase 1 | 4-5 days | 4-5 days |
| Phase 2 | 1 day | 5-6 days |
| Phase 3 | 1 day | 6-7 days |
| Phase 4 | 1 day | 7-8 days |
| Phase 5 | 1 day | 8-9 days |

**Total**: ~8-9 days minimum
**Model freeze**: 14 days maximum (enforced by TASK 7)
