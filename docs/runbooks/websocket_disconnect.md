# WebSocket Disconnect Runbook

## Detection

The `health/exchange` endpoint shows `state=closed` or a stale timestamp. Data-engine logs contain `connection closed` or `intentional close`. Analytics-engine emits `stream_anomaly_detected` (idle) when `lastTickAgeMs > 10000`.

## Diagnosis

Check the Exchange health endpoint: `curl -s http://localhost:3000/health/exchange`. Inspect DE logs for the close reason frame. Verify Binance Testnet status through their status page. Cross-reference broker health: `curl -s http://localhost:3000/health` — if `broker=false`, the disconnect is secondary to a broker outage.

## Mitigation

If the broker is down, follow the [redis_outage.md](redis_outage.md) runbook first. If the broker is healthy but the WS is closed, `HealthMonitorUseCase` handles auto-reconnection on the next healthy tick cycle — wait up to 30s. If auto-reconnect fails, restart the data-engine: `docker compose restart data-engine`. If the `intentionalClose` flag was set incorrectly by a previous error, the `reconnect()` method is now invoked automatically by `HealthMonitorUseCase`, so no manual intervention is needed.

## Verify Recovery

Check `health/exchange` shows `state=open` and `connected=true`. Confirm `lastTickAgeMs < 5000`. Verify AE tick count is growing and no new `stream_anomaly_detected` events are firing.
