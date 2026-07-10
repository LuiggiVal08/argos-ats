# ADR-004: Feature Selection — Reduced 33

**Status:** ACCEPTED (2026-06-26)

## Context

Feature engineering produced 53 features (20 base + 15 MTF 4h + 15 MTF 1d + 3 funding). Correlation analysis revealed that 56.6% (30 of 53) features are redundant at |r| > 0.95. Redundant features increase overfitting risk and inference latency without contributing additional signal. Feature reduction must preserve predictive performance while improving generalization and efficiency.

## Options Considered

- **Full 53 features** — baseline MCC 0.2957; highest theoretical ceiling but elevated overfitting risk.
- **Reduced 33** — 30 features; MCC 0.2894 (97.9% of baseline preserved).
- **Minimal 20** — 20 features; MCC 0.2812 (95.1% of baseline preserved).

## Decision

Use the **reduced_33** feature set (30 features). Twenty-three redundant features organized in 5 correlation clusters were removed:

| Cluster | Features | Count Before | Count After |
|---------|----------|:-----------:|:----------:|
| OHLCV + EMA + BB (all TFs) | 22 | 4 |
| MACD + signal | 2 | 1 |
| ATR + ATR_4h | 2 | 1 |
| MACD_4h + signal | 2 | 1 |
| MACD_1d + signal | 2 | 1 |

This preserves 97.9% of MCC while reducing the feature count by 38%.

## Consequences

**Positive:**
- Faster training — 2.9s vs 15.6s per cross-validation fold
- Reduced overfitting risk due to lower dimensionality
- Simpler model maintenance and faster inference in production

**Negative:**
- Theoretical performance ceiling is 2.1% lower than the full feature set
- Some feature interactions present in the discarded redundant features may be lost

## Alternatives Rejected

- **Full 53 (baseline):** elevated overfitting risk without proportional performance gain.
- **Minimal 20:** 5% MCC loss is too high relative to the reduction benefit.
- **L1-based selection:** not yet tested; may yield a better trade-off in future iterations.

## References

- `archive/qv2/qv2_phase38_output/PHASE38_REPORT.md`
