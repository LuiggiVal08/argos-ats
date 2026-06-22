# Phase 9 — Paper Trading Framework

## Goal
Build a reusable paper trading simulation framework and execute a 1-year historical
simulation to validate the alpha under realistic execution conditions with risk management.

## Components
- `signal_engine` — loads predictions, applies thresholds, generates signals
- `paper_broker` — simulates market order fills with configurable costs/delay
- `portfolio_state` — tracks positions, balance, P&L, margin usage
- `metrics_tracker` — records trades, computes performance
- `simulation` — orchestrates over last year, applies invariants

## Protocol
- Capital: $100,000
- Risk: 1% per trade, SL = 2× ATR
- Drawdown CB: 5% daily → halt
- Thresholds: BUY > 0.60, SELL < 0.40
- Costs: 0.31% round-trip

## Output
- `paper_trading.json` — simulation results
- `trade_log.csv` — all executed trades
- `equity_curve.csv` — daily equity

## Verdicts
- PAPER_ALPHA_CONFIRMED: Sharpe > 1.0
- PAPER_ALPHA_NEUTRAL: 0 < Sharpe < 1.0
- PAPER_ALPHA_REJECTED: Sharpe < 0
