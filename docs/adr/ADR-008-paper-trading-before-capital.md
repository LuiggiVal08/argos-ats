# ADR-008: Paper Trading Before Capital Deployment

**Status:** ACCEPTED (2026-06-26)

## Context

Statistical alpha has been validated through backtesting (Phase 3.9), but market microstructure, slippage, and operational failures can only be observed with real exchange interaction. The core question is not "does the model predict?" but "does the system make money?" — and that question requires operational validation.

## Options Considered

- **Deploy directly to LIVE with small capital** — rejected; unacceptable risk of capital loss from operational bugs.
- **Simulated paper only** — selected as intermediate environment.
- **Deploy to paper with shadow mode for N trades before LIVE** — selected; 100-trade freeze period.

## Decision

Run paper trading on Binance Testnet with `LIVE_SIMULATION` mode. The deployment follows a freeze protocol:

1. Execute 100 trades on paper
2. Evaluate performance against backtest expectations
3. Only after 100-trade freeze: consider parameter changes or transition to LIVE

Shadow mode records all decisions alongside the primary system for offline comparison.

## Consequences

**Positive:**
- Capital-safe operational validation
- Real market microstructure exposure (order book dynamics, slippage patterns)
- Debugging without financial risk during the freeze period

**Negative:**
- Testnet liquidity differs from production — fill rates may not transfer
- No real funding costs — funding rate impact is estimated, not observed
- 100-trade freeze delays capital deployment by approximately 12 days

## Alternatives Rejected

- **Direct LIVE deployment:** too risky for a system without operational validation.
- **Simulation-only:** does not exercise real exchange APIs or market microstructure.

## References

- `spec.md` §5 (Historia 4)
- `TASKS.md` Phase 5
