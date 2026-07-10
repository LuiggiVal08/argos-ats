# ADR-007: Risk Engine Gating

**Status:** ACCEPTED (2026-06-26)

## Context

ARGOS ATS requires hard risk limits enforced at the infrastructure layer, not merely in application logic. The specification defines three non-negotiable constraints: maximum 1% loss per trade (ATR-based stop-loss), 5% daily drawdown circuit breaker, and maximum 3 order retries before market order liquidation. These limits must be unbypassable by application-level bugs.

## Options Considered

- **Soft gates** (logging warnings only) — rejected; violates spec requirements for hard enforcement.
- **Hard gates in application code only** — rejected; application bugs could bypass enforcement.
- **Composition-level enforcement with runtime contracts** — selected.
- **Hardware-level circuit breakers** — overkill for v1.

## Decision

`ExecuteMarketOrderUseCase` validates orders via contract schemas *before* exchange submission and validates fills *after* execution. The enforcement flow:

1. `validateOrder()` — validates order against risk schema before exchange submission
2. Exchange execution
3. `validateFill()` — validates fill against risk schema before storage

The circuit breaker is managed by `HealthMonitorUseCase`: on 10s broker cutoff, it triggers WS close and position liquidation.

## Consequences

**Positive:**
- Every order and fill is validated against contract schemas — no bypass possible
- Circuit breaker prevents runaway losses through automated position liquidation
- Clear separation between application logic and risk enforcement

**Negative:**
- Additional latency per order (~1-2ms for contract validation)
- Schema changes require coordinated updates across layers

## Alternatives Rejected

- **Soft gates:** violate spec requirements for hard loss limits.
- **Application-only gates:** bypassable if the application layer has a bug.

## References

- `spec.md` §5 (Historia 2, 3)
- `data-engine/execute-market-order.usecase.ts`
