# Emergency Shutdown Runbook

## Detection

The drawdown circuit breaker trips (daily P&L exceeds 5%), funding costs exceed the edge, the exchange reports operational issues, or suspicious account activity is detected.

## Diagnosis

Run `risk_drawdown_check` with the opening and current balance to confirm threshold breach. Check open positions and current system mode. Review exchange status and recent order activity.

## Mitigation

1. Set system to PASIVO mode immediately via `config_toggle_mode` or by setting `ENVIRONMENT_MODE=PASIVO`. 2. Cancel all open orders at the exchange. 3. Close all open positions at market price. 4. Verify system is in PASIVO mode — no new trades and no inference execution. 5. Log the full incident with a complete state snapshot (positions, balance, orders, mode). 6. If an exchange issue is suspected, wait for the exchange all-clear before any resume action. 7. Investigate root cause — do NOT resume trading the same day. Wait for the next UTC day boundary.

## Verify Recovery

System is confirmed in PASIVO mode. Zero open positions. Zero open orders. Balance is preserved and accounted for. Incident report is logged with full state snapshot.
