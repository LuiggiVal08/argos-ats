# Redis Outage Runbook

## Detection

Look for `system-degraded` state logs, health endpoint showing `broker=false`, data-engine logs with `poll error: Connection is closed`, or ioredis emitting `Unhandled error event: Error: connect ETIMEDOUT` or `getaddrinfo ENOTFOUND broker`.

## Diagnosis

Run `docker compose ps` to verify the broker container state. Use `health_health_check` to get a full system snapshot. Test broker reachability with `redis_redis_get ping`. Inspect broker container logs via `docker compose logs broker --tail=50` for OOM, disk-full, or crash-loop errors.

## Mitigation

The ioredis client uses `enableOfflineQueue=false`, meaning publish calls fail fast — no message queueing occurs during an outage. When the DE `HealthMonitorUseCase` detects the broker is down, it starts a 10s cutoff timer and closes the WebSocket connection. When the broker recovers, `HealthMonitorUseCase` automatically flushes the buffer and reconnects the WebSocket. If auto-recovery does not occur within 30s, restart the broker with `docker compose restart broker`. If the broker fails to restart, investigate the container runtime (disk space, memory limits, or corrupted data).

## Verify Recovery

Run `health_health_check` and confirm `broker=true`. Verify the DE WebSocket reconnects (state shows `open`). Confirm the AE is consuming ticks (`total_ticks` growing). Check Redis stream activity: `redis_redis_xlen stream=ticks:btcusdt` should return a value > 0.
