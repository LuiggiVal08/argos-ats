# ADR-002: Target Specification v1

**Status:** ACCEPTED (2026-06-26)

## Context

The ML model requires a target variable that represents price direction. The target must satisfy three constraints: stationarity (non-stationary returns cause model degradation), threshold-interpretability (clear economic meaning), and leakage-freedom (no lookahead bias). The target definition directly shapes model behavior and trading outcomes.

## Options Considered

- **Raw returns** — rejected; non-stationary, contaminated by market beta, poor generalization across regimes.
- **ATR-normalized returns** — rejected; ATR measures OHLC volatility, not return volatility — a domain mismatch.
- **Volatility-adjusted sigma-units** — selected as the primary target.
- **Triple Barrier** — deferred to v2; better aligned with SL/TP mechanics but adds complexity.

## Decision

Use volatility-adjusted forward return measured in sigma-units:

```
y_i = r_i / (sigma_past * sqrt(h))
```

where `r_i = close[i+h]/close[i] - 1`, `sigma_past = std(returns over w=60 periods)`, and `h = 3`. The target is classified into three buckets:

| Class | Threshold | Expected Frequency |
|-------|-----------|--------------------|
| SELL | y_i < -0.5σ | 25–38% |
| HOLD | \|y_i\| ≤ 0.5σ | 25–38% |
| BUY | y_i > 0.5σ | 25–38% |

The 0.5σ threshold corresponds to approximately 0.4% at average volatility.

## Consequences

**Positive:**
- Stationary by construction — no drift across regimes
- Interpretable — threshold has clear economic meaning
- Symmetric class distribution enables balanced training

**Negative:**
- Assumes normal distribution of returns; fat tails are a known violation
- Does not model SL/TP dynamics directly
- Threshold choice (0.5σ) is heuristic and may require tuning

## Alternatives Rejected

- **Triple Barrier:** deferred to v2; conceptually superior for SL/TP alignment but adds labeling complexity.
- **Meta-Labeling:** requires two stacked models, increasing overfitting risk.
- **Regression:** MSE loss is not aligned with discrete trading decisions.
- **Binary classification:** discards directional information on the short side.

## References

- `TARGET_SPEC.md` §3–4
- `archive/qv2/qv2_baseline_output/label_report.json`
