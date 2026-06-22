# Quant Validation v1 — ¿Existe señal predictiva en BTC/USDT 1h?

> Ground truth: S01 (data pipeline) + Q02 (baseline modeling) committed.
> No experiments from previous conceptual phases exist in this repo.
> This is a **fresh hypothesis test**, not a continuation.

## Principle

We do NOT assume alpha exists. We are in **hypothesis generation** stage.

| Hypothesis | Statement |
|---|---|
| **H0** | No predictive signal exists in OHLCV + TA features on BTC/USDT 1h |
| **H1** | A weak but statistically significant signal exists |

## Pipeline (ordered, no skipping)

### Phase 1: Feature Sanity
- [ ] Load BTC/USDT 1h OHLCV (~39k bars, 2022-01 to 2026-06)
- [ ] Compute basic TA features (RSI, MACD, BB, EMA, ATR, volume metrics)
- [ ] Check: stationarity, missing values, multicollinearity (VIF)
- [ ] Check: label distribution, class balance over time
- [ ] **GATE 1**: Data passes basic quality checks

### Phase 2: Simple Baselines
- [ ] Always-HOLD (predict majority class always)
- [ ] Always-BUY / Always-SELL
- [ ] Naive momentum (predict same as previous direction)
- [ ] **GATE 2**: Actual model must beat ALL simple baselines with p < 0.05

### Phase 3: Walk-Forward Validation
- [ ] 4-fold chronological walk-forward (train→test, no leakage)
- [ ] Models: Logistic Regression (linear baseline), Random Forest (nonlinear)
- [ ] Metrics: Accuracy, Kappa, MCC, F1 per class, confusion matrix
- [ ] Compare: model vs null (shuffled labels, 10 seeds)
- [ ] **GATE 3**: At least one model beats shuffled null with p < 0.05

### Phase 4: Economic Proxy
- [ ] Simulate simple trading rule: model signal → position
- [ ] Apply: 0.1% slippage + 0.1% fee per round trip
- [ ] Metrics: Sharpe ratio, max drawdown, total return
- [ ] Compare: buy-and-hold, always-flat
- [ ] **GATE 4**: Strategy Sharpe > 0.5 after costs AND beats B&H

### Phase 5: Advanced Statistical Tests (if GATES 1-4 pass)
- [ ] Purged K-Fold CV
- [ ] Combinatorial Purged CV (CPCV)
- [ ] Deflated Sharpe Ratio (DSR)
- [ ] Stepwise Model Selection (SMS / PBO)
- [ ] Superior Predictive Ability (SPA / White RC)
- [ ] **GATE 5**: Statistical significance confirmed

## Success Criteria

| Gate | Condition | Action if fail |
|---|---|---|
| GATE 1 | Data is clean | Fix data pipeline |
| GATE 2 | Beats naive baselines | Stop: no signal > random |
| GATE 3 | Beats shuffled null | Stop: no information in features |
| GATE 4 | Sharpe > 0.5 net costs | Stop: not economically viable |
| GATE 5 | Statistical significance | Accept H1 or stop |

## Current Status

- Phase 1: ✅ completed
- Phase 2: ✅ completed
- Phase 3A: ✅ completed — multiclass — FAIL
- Phase 3B: ✅ completed — binary — FAIL
- Phase 3C: ✅ completed — regression — FAIL
- Phase 4: 🚫 not started (blocked by GATE)
- Phase 5: 🚫 not started (blocked by GATE)

## Final Verdict (2026-06-16)

| Phase | Best Model | Best Null | Beats Null? | Shuffle Delta | Gate |
|-------|-----------|-----------|-------------|--------------|------|
| 3A multiclass | RF f1=0.311 | persist_last_label f1=0.685 | ❌ | +0.012 | FAIL |
| 3B binary | HistGB f1=0.467 | persist_last_label f1=0.811 | ❌ | +0.013 | FAIL |
| 3C regression | Ridge R²=-0.014 | persist_last_value R²=0.571 | ❌ | -0.011 | FAIL |

**Global: ALL GATES FAIL → H0 NO SE RECHAZA**

OHLCV + TA (20 features) on BTC/USDT 1h does NOT contain exploitable predictive signal sufficient to justify continued ML research on this feature space.

## Files

| File | Purpose |
|---|---|
| `baseline.py` | Data loading, feature engineering, walk-forward, null comparison |
| `gates.py` | Gate evaluation logic |
| `ROADMAP.md` | This file |
