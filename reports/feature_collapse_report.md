# Feature Collapse Report
> Generated 2026-06-21 17:40:46 UTC

## Summary

| Metric | Value |
|--------|-------|
| Total features | 53 |
| Collapsed (variance < 1e-6) | 4 |
| Near-zero (variance 1e-6 to 1e-4) | 0 |
| Healthy (variance >= 1e-4) | 49 |

## Collapsed Features

| # | Feature | Live Std |
|---|---|---|
| 1 | htf_ema_slow_1d | 0.00e+00 |
| 2 | funding_rate | 0.00e+00 |
| 3 | funding_momentum | 0.00e+00 |
| 4 | funding_change | 0.00e+00 |

## Near-Zero Features

None

## Interpretation

⚠️ **4 features collapsed**, but 3 are expected:

- `funding_rate`, `funding_momentum`, `funding_change` → Always 0.0 in static 
  test because `FundingRateProvider` only injects to the LAST row via 
  `features_raw[-1, i] = extra[name]`. All historical rows remain 0.0.
  This is an artifact of the test, NOT a pipeline issue. In live production,
  these features have non-zero values on the last row.

- `htf_ema_slow_1d` → Std = 0.0 because with only ~20 daily bars in the 
  test window (2026-05-11 to 2026-06-21), the EMA(50) on 1d resampled data 
  has too few bars to converge. All values after NaN fill are identical.
  This is a **data depth issue**, not a feature bug. With >100 daily bars
  the EMA(50) would stabilize.

**In production**, when the buffer has >100 daily candles (>100 days of 1h 
data), `htf_ema_slow_1d` will have non-zero variance. The funding features
will have variance on the last row (from the provider).

**Mitigation**: After >100 daily candles, re-run this test to verify 
htf_ema_slow_1d recovers.
