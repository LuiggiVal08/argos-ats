# ADR-006: Replay Adapter

**Status:** ACCEPTED (2026-06-26)

## Context

Before deploying to live trading, the production inference pipeline must produce signals identical to backtest results. This requires replaying historical data through the full LIVE pipeline — same code path, same candle builder, same feature calculator, same inference engine. The replay must be faithful enough to catch integration bugs that unit tests miss.

## Options Considered

- **Unit test comparison** — rejected; tests individual components but does not validate end-to-end pipeline integration.
- **Synthetic data replay** — rejected; synthetic data does not exhibit the edge cases and market microstructure of real data.
- **Historical tick replay through the live pipeline** — selected.

## Decision

Implement `ReplayAdapter` that reads events from EventStore (SQLite) within a date range and replays them to the broker at configurable speed. Events are sorted by timestamp and emitted in wall-clock order. The adapter reuses the exact same pipeline components as LIVE mode — ensuring full parity.

Consistency validation is performed end-to-end: replay output is compared against backtest output for identical date ranges.

## Consequences

**Positive:**
- Validates pipeline parity — the same code path executes in replay and LIVE modes
- Catches integration bugs before capital deployment
- Configurable replay speed allows faster-than-real-time validation

**Negative:**
- Requires EventStore to be populated with historical data
- Adds approximately 10 seconds to the deployment validation process

## Alternatives Rejected

- **Unit test parity check:** misses integration bugs at component boundaries.
- **Synthetic data:** fails to expose real-market edge cases (gaps, illiquid periods, exchange errors).

## References

- `reports/active/operational/PHASE4_PRODUCTION_REPORT.md`
- `apps/analytics-engine/app/infrastructure/trading/candle_builder.py`
