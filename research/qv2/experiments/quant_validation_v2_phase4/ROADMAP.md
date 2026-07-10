# QV2 Phase 4 — Cross-Regime Validation

## Goal

Determine whether the structural alpha discovered in QV2 depends on a specific market regime or survives regime changes.

## Definitions

- **Trend regimes**: Bull (rolling 50-bar return > p60), Bear (< p40), Sideways (p40–p60)
- **Volatility regimes**: High (rolling 20-bar std > p75), Medium (p25–p75), Low (< p25)

## Protocol

- Phase 3.5 frozen: lookahead=5, stride=5, embargo=1
- 53 features, no tuning, no feature engineering
- BTC Binance only (longest history, most regime samples)
- Regimes computed on full OHLCV before subsampling
- Filter applied *after* stride=5 subsampling

## Experiments

1. `full` — no regime filter (control, must match Phase 3.5)
2. `bull` — only bull bars
3. `bear` — only bear bars
4. `sideways` — only sideways bars
5. `high_vol` — only high volatility bars
6. `medium_vol` — only medium volatility bars
7. `low_vol` — only low volatility bars

## Verdict tree

1. ≤2 regimes pass gates → REGIME ARTIFACT
2. Only bull passes, bear fails → BULL-MARKET ALPHA
3. Only high_vol passes → VOLATILITY-PREMIUM ALPHA
4. All pass + stability > 0.75 → REGIME-INVARIANT
5. All pass + stability ≤ 0.75 → REGIME-SENSITIVE
6. Mixed → REGIME-SENSITIVE

## Out of scope

- Cross-asset regime validation (defer to Phase 4.5 if warranted)
- Feature engineering, hyperparameter tuning, new models
- GRU/LSTM/Transformer (deferred to Phase 7)
