# Feature Redesign Study
> Generated 2026-06-21
> 
> Purpose: Propose robust feature replacements for the next model iteration,
> based on evidence from the Data Pipeline Audit and Drift Report.

---

## Problem Diagnosis

From the KILL verdict and audit:

| Feature | Training Median | Live Mean | z-score | Root Cause |
|---------|---------------|-----------|---------|------------|
| `volume` | 1,205 | 60,588 | 25.5σ | Pipeline volume inflation + real regime shift |
| `volume_sma` | 1,205 | 60,588 | 35.7σ | Inherits volume unit mismatch |
| `htf_volume_sma_4h` | ~1,205 | ~60,588 | 43.0σ | Inherits volume unit mismatch |
| `htf_volume_sma_1d` | ~1,205 | ~60,588 | 50.6σ | Inherits volume unit mismatch |

**Root cause**: Absolute volume is non-stationary. Training (2022–2026 H1) 
captures a different volume regime than mid-2026, and the pipeline multiplies 
the error by accumulating without deduplication. Any model that relies on 
absolute volume will fail when volume units or regime shifts.

---

## Proposed Replacements

### 1. Volume → Relative Volume Metrics

| Proposed Feature | Formula | Why Robust |
|-----------------|---------|------------|
| `log_volume` | `log(volume + 1)` | Compresses dynamic range |
| `relative_volume` | `volume / volume_sma(20)` | Unit-independent ratio |
| `volume_zscore_50` | `(volume - mean_vol_50) / std_vol_50` | Normalized, drift-resistant |
| `volume_percentile_100` | `rank(volume, 100) / 100` | Bounded [0,1], stationary |
| `volume_ratio_20` | `volume / volume_ema(20)` | Trend-relative volume |

**Recommendation**: Replace `volume` + `volume_sma` with `relative_volume` + 
`volume_zscore_50`. These are unit-agnostic and capture volume regime changes
rather than absolute levels.

### 2. ATR → Normalized ATR

| Proposed Feature | Formula | Why Robust |
|-----------------|---------|------------|
| `atr_normalized` | `atr / close` | Unitless percentage |
| `atr_percentile` | `rank(atr, 100) / 100` | Bounded, stationary |

**Recommendation**: Add `atr_normalized` alongside `atr`. Keep `atr` for 
models that can learn to scale internally (tree-based models).

### 3. New Features

| Feature | Formula | Signal |
|---------|---------|--------|
| `distance_to_ma25` | `(close - sma_25) / sma_25` | Mean reversion |
| `distance_to_ma99` | `(close - sma_99) / sma_99` | Long-term deviation |
| `volatility_regime` | `atr_normalized > p75_atr → 1 else 0` | Regime gate |
| `trend_strength` | `abs(close - sma_50) / atr` | Trend vs noise |
| `funding_extremeness` | `abs(funding_rate) > p95_funding → 1` | Extreme funding signal |
| `funding_percentile` | `rank(funding_rate, 100) / 100` | Funding regime |
| `funding_zscore` | `(funding_rate - mean_funding) / std_funding` | Normalized funding |

### 4. Features to Remove

| Feature | Reason |
|---------|--------|
| `volume` | Non-stationary, unit-dependent |
| `volume_sma` | Non-stationary |
| `htf_volume_sma_4h` | Non-stationary |
| `htf_volume_sma_1d` | Non-stationary |
| `obv` | Directly depends on volume magnitude |
| `htf_obv_4h` | Directly depends on volume magnitude |
| `htf_obv_1d` | Directly depends on volume magnitude |

---

## Projected Feature Set (V2)

**Base (15 → 17)**:
```
open, high, low, close, log_volume,              ← volume → log_volume
rsi, ema_fast, ema_medium, ema_slow,
macd, macd_signal, macd_hist,
bb_upper, bb_middle, bb_lower,
atr, adx, atr_normalized,                        ← added atr_normalized
pct_change,
relative_volume, volume_zscore_50,               ← replaced volume_sma
```

**MTF 4h + 1d (30 → ~22)**: Remove volume-dependent indicators:
- Keep: htf_rsi, htf_ema_*, htf_macd*, htf_bb_*, htf_atr, htf_adx, htf_pct_change
- Remove: htf_volume_sma, htf_obv

**Funding (3)**:
- Keep all three (they are already relative/first-difference based)

**Extra (7 new)**:
- distance_to_ma25, distance_to_ma99, volatility_regime, trend_strength,
  funding_percentile, funding_zscore, funding_extremeness

**Total**: ~49 features (down from 53, but more robust)

---

## Risk

1. Relative volume loses short-term volume spike information
2. ATR normalization may remove useful scaling info for position sizing
3. Feature count reduction may reduce model capacity

**Mitigation**: Test V2 feature set in backtest before deploying. Use 
feature importance to verify new features add signal.
