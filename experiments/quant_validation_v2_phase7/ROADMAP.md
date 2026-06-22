# Phase 7 — Economic Alpha Backtest

## Goal
Backtest the signal with realistic costs and threshold-based position sizing to determine whether the alpha survives frictions.

## Protocol
- Thresholds: BUY > 0.60, SELL < 0.40, HOLD otherwise
- Trade cost: 0.31% round-trip (fee=0.10% + slippage=0.05% + spread/2=0.005% per side × 2)
- Equal-weight portfolio of BTC+ETH+SOL

## Input
- Phase 5 predictions parquet

## Output
- `economic_alpha.json` — per-symbol + portfolio metrics with costs

## Verdicts
- ALPHA SURVIVES: portfolio Sharpe > 1.0 after costs
- ALPHA ERODED: 0 < Sharpe < 1.0
- ALPHA DESTROYED: Sharpe < 0
