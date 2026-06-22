# Phase 5 — Portfolio Validation

## Goal
Determine whether the signal found in BTC generalizes to ETH and SOL, and whether a multi-asset portfolio produces coherent risk-adjusted returns.

## Protocol
- Phase 3.5 frozen: lookahead=5, stride=5, embargo=1
- 53 features, no tuning
- LR as champion classifier

## Outputs
- Per-symbol: `{symbol}_predictions.parquet` (y_true, y_pred, y_proba, forward_return, timestamps)
- Per-symbol: `{symbol}.json` (aggregated metrics)
- Portfolio: `summary.json` (equal-weight + correlations + diversification)

## Verdicts
- DIVERSIFICATION BENEFIT: portfolio Sharpe > mean(individual)
- CONCENTRATION RISK: portfolio Sharpe < min(individual)
