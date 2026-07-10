# ADR-001: Timeframe Selection

**Status:** ACCEPTED (2026-06-26)

## Context

ARGOS ATS needs a canonical base timeframe for BTC/USDT perpetual futures prediction. Three candidates were evaluated: 15m, 1h, 4h. The analysis tested 18 combinations (3 bases × 6 horizons) across 5 weighted criteria: expectancy (30%), SNR (25%), sample density (20%), operational cost (15%), temporal independence (10%).

## Options Considered

- **15m base** — rejected; spread consumes >30% of expected movement, micro-structure noise dominates at this resolution.
- **4h base** — rejected; yields ~2190 samples/year, which is insufficient for ML training and creates extreme overfitting risk.
- **1h base with h=3** — selected as optimal.

## Decision

Use 1h base candles with a 3-period forward horizon (h=3). This configuration provides 8 decisions per day (~2000/year), SNR in the 0.15–0.30 range, and cost per trade of approximately 0.15%.

## Consequences

**Positive:**
- Sufficient sample density for ML training and EDL Bayesian inference
- Manageable funding costs (~0.004% per 3h hold period)
- Balanced trade frequency for operational feasibility

**Negative:**
- Higher noise than longer horizons
- Requires robust feature engineering to extract signal from the noise

## Alternatives Rejected

- **15m base:** spread too high relative to expected move
- **4h base:** insufficient samples for statistical significance
- **h=5 horizon:** better SNR but produces fewer decisions per day, delaying EDL convergence

## References

- `TARGET_SPEC.md` §2
- `TARGET_ANALYSIS_REPORT.md`
