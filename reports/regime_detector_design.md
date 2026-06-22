# Regime Detector Design Study
> Generated 2026-06-21
>
> Purpose: Design a regime detection layer that gates model predictions
> based on current market conditions, improving robustness to distribution shift.

---

## Motivation

The KILL verdict showed the model collapses when volume regime shifts.
A regime detector would:
1. Detect when the model is operating out-of-distribution
2. Gate predictions (fall back to HOLD/null model)
3. Allow specialized sub-models per regime

---

## Proposed Regimes

| Regime | Definition | Features | Typical Behavior |
|--------|-----------|----------|-----------------|
| `TRENDING` | ADX ≥ 25, trend_strength > 1.5 | Directional followers work best | Model confidence high |
| `RANGING` | ADX < 25, volatility < p50 | Mean reversion, avoid trend followers | Model may degrade |
| `HIGH_VOL` | atr_normalized > p75(ATR) | Wider stops, smaller positions | Model may be noisy |
| `LOW_VOL` | atr_normalized < p25(ATR) | Tighter stops | Model may be less useful |
| `EXTREME_FUNDING` | abs(funding_rate) > 0.0005 | Contrarian signals | Model may be overconfident |
| `SHORT_SQUEEZE` | funding_rate < -0.001, price > sma_25 | Strong upward pressure | Model may miss |
| `LONG_SQUEEZE` | funding_rate > 0.001, price < sma_25 | Strong downward pressure | Model may miss |

---

## Detection Method

### Approach A: Rule-Based Classification

Simple threshold gates:

```python
def detect_regime(features: dict) -> str:
    adx = features.get("adx", 0)
    atr_norm = features.get("atr_normalized", 0)
    funding = features.get("funding_rate", 0)
    vol_ratio = features.get("relative_volume", 1)
    trend = features.get("trend_strength", 0)
    ma_dist = features.get("distance_to_ma25", 0)

    # Squeeze detection (rare but important)
    if funding < -0.001 and ma_dist > 0.03:
        return "SHORT_SQUEEZE"
    if funding > 0.001 and ma_dist < -0.03:
        return "LONG_SQUEEZE"

    # Funding extremes
    if abs(funding) > 0.0005:
        return "EXTREME_FUNDING"

    # Volatility
    atr_pct = percentile(atr_norm, lookback=100)
    if atr_pct > 0.75:
        if adx >= 25:
            return "TRENDING"  # High vol trending
        return "HIGH_VOL"
    if atr_pct < 0.25:
        if adx < 25:
            return "LOW_VOL"
        return "TRENDING"  # Low vol trending

    # Trend vs range
    if adx >= 25:
        return "TRENDING"
    return "RANGING"
```

### Approach B: Unsupervised Clustering (HDBSCAN)

- Train on live feature distribution (no labels needed)
- Cluster regimes based on market context features
- Assign each candle to a regime cluster
- Pro: adaptive, finds unexpected regimes
- Con: regime labels change over time, non-deterministic

### Approach C: Supervised Regime Classifier

- Label historical regimes manually or with heuristics
- Train a small model (XGBoost, 4-6 features)
- Pro: deterministic, interpretable
- Con: requires labeled data, regime definitions may not generalize

---

## Integration with Model

```
live_features
    ↓
RegimeDetector.predict(features) → regime
    ↓
if regime in CONFIDENT_REGIMES:
    model.predict(features) → signal
else:
    return HOLD  (fallback)
```

Recommended: start with Approach A (rule-based), iterate to Approach C.

---

## Suggested CONFIDENT_REGIMES (initial)

Models tend to work in:
- `TRENDING` (directional models)
- `EXTREME_FUNDING` (contrarian opportunities)

Models tend to fail in:
- `HIGH_VOL` (noise dominates, unless calibrated)
- `RANGING` (signals cancel out)

---

## Risk

1. Regime filtering reduces trade frequency (potentially 30-50% fewer signals)
2. Rule-based thresholds may need periodic recalibration
3. Regime misclassification (especially squeezes) can cause large losses

**Recommendation**: Implement rule-based detection first, measure signal 
quality per regime, then iterate.
