# Retraining Strategy
> Generated 2026-06-21
>
> Purpose: Evaluate retraining approaches for the next model iteration,
> given the KILL verdict and evidence of distribution shift.

---

## Current State

| Metric | Value |
|--------|-------|
| Current model | LogisticRegression, 53 features, binary |
| Training data | 2022-01-01 to 2026-06-16 (4.5 years, 4044 samples) |
| Training accuracy | 76.98% |
| Test performance (lock test) | Directional accuracy 75.3% (2025-06 to 2026-06) |
| Live performance | **KILL** — skill=0.0, log_loss > baseline |

---

## Options

### Option A: Full retrain (2022–2026)

**What**: Same data range, same architecture, same features (with proposed
improvements from feature_redesign_proposal.md).

**Pros**:
- Maximum data volume (4044+ samples after refill)
- Captures full market cycle (bull, bear, sideways)
- Proven baseline (76.98% accuracy in backtest)

**Cons**:
- Distribution shift NOT addressed — volume features still dominate
- Old regime data (2022-2024) may not generalize to 2026
- Without feature redesign, will produce same degenerate output

**Risks**: HIGH — same pitfalls as current model.

---

### Option B: Temporal weighting

**What**: Same data range, but weigh recent samples more heavily.
Exponential decay: `weight = alpha^(n)`, `alpha = 0.998`.

**Pros**:
- Smooth transition between regimes
- Uses all available data
- Simple to implement (sample_weight in LogisticRegression)

**Cons**:
- Does not fix absolute volume dependency
- Still dominated by 2022-2024 data (more samples)
- Hyperparameter alpha needs tuning

**Best for**: Interim fix before full redesign.

---

### Option C: Rolling window (last 12 months)

**What**: Train only on the most recent 12 months (~8760 1h samples).

**Pros**:
- Model matches current regime
- Volume distribution more similar to live data
- Faster training, less compute
- Easy to retrain monthly

**Cons**:
- Loses historical regime diversity (2022 crash, 2023 bull, etc.)
- May overfit to recent patterns
- Need monthly retraining cycle
- Fewer training samples (~1/4 of full set)

**Risk**: MEDIUM — may miss rare events.

---

### Option D: Specialized models per regime

**What**: Train separate models for each market regime, use RegimeDetector
to select the active model.

**Pros**:
- Each model optimized for its regime
- No distribution shift within regime
- Natural with RegimeDetector from Phase 6

**Cons**:
- Complex infrastructure (M models to train, deploy, monitor)
- Need regime-labeled training data
- Regime boundary transitions may cause thrashing
- N-way more maintenance

**Risk**: HIGH — complexity, but highest potential reward.

---

## Recommendation

### Immediate (Phase 1)

1. **Fix the volume pipeline bug** — identify why CandleBuilder inflates
   volume vs CCXT OHLCV. This is the highest priority fix before ANY retraining.
2. **Implement volume-independent features** — relative_volume, volume_zscore.

### Short-term (Phase 2)

3. **Option A + Feature Redesign**: Full retrain with the V2 feature set
   (replace volume with relative volume). This gives a clean baseline.
4. **Validate** with the KILL/KEEP test framework.

### Medium-term (Phase 3)

5. **Option C**: Switch to rolling window once the feature set is validated.
6. **Option D**: Regime-specialized models after gathering regime-labeled data.

---

## Timeline Estimate

| Phase | Action | Effort | Timeline |
|-------|--------|--------|----------|
| 1 | Fix volume pipeline | 1-2 days | Week 1 |
| 1 | Feature redesign implementation | 2-3 days | Week 1-2 |
| 2 | Retrain (Option A + V2 features) | 1 day | Week 2 |
| 2 | Validate with KILL/KEEP test | 0.5 day | Week 2 |
| 3 | Rolling window pipeline | 2-3 days | Week 3 |
| 3 | Regime detector | 3-5 days | Week 3-4 |

---

## Critical Dependencies

1. **Volume pipeline bug fix** — without this, any retraining will reproduce
   the same KILL result
2. **Feature redesign** — absolute volume must be replaced before retraining
3. **Fresh CCXT data fetch** — training data should use the same source as 
   live pipeline (both spot or both futures), with verified volume units
