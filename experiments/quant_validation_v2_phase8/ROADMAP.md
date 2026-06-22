# Phase 8 — Capacity & Friction Stress

## Goal
Subject the alpha to adverse market conditions (slippage, delay, cost spikes) to estimate
capacity (how much capital can the signal absorb) and execution resilience.

## Stress Tests
1. **Slippage stress**: 2x slippage → 0.41% round-trip
2. **Delay stress**: 1-bar execution lag (forward_return shifted by 1)
3. **Cost stress**: 3x costs → 0.93% round-trip
4. **Combined**: delay + 3x cost
5. **Adverse selection**: always trade against model (inverted positions)
6. **ExecutionResilienceIndex**: median + mean Sharpe across stresses

## Input
- Phase 5 predictions parquet

## Output
- `stress.json`
- `resilience_index.json`

## Verdicts
- RESILIENT: mean(Sharpe across stresses) > 1.0
- VULNERABLE: 0 < mean(Sharpe) < 1.0
- BRITTLE: mean(Sharpe) < 0
