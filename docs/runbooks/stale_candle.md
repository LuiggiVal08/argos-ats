# Stale Candle Runbook

## Detection

Analytics-engine logs contain `decision_skipped_stale_candle` with `candle_age_ms > MAX_CANDLE_AGE_MS` (5,400,000ms = 90 minutes). The AE also logs `stream_anomaly_detected` (idle), and the total tick count stagnates.

## Diagnosis

Review AE decision loop logs for the `decision_skipped_stale_candle` event. Check candle builder health metrics. Verify WS connection state (see [websocket_disconnect.md](websocket_disconnect.md)). Check broker connectivity (see [redis_outage.md](redis_outage.md)). Examine Redis stream depth with `redis_redis_xlen`. Determine whether the tick→candle loop is alive or dead by checking for `task_dead` markers in AE logs.

## Mitigation

If the tick→candle loop is dead (`task_dead`), restart the analytics-engine: `docker compose restart analytics-engine`. If the WS disconnected, follow the websocket_disconnect.md runbook. If the broker disconnected, follow the redis_outage.md runbook. If all components are healthy but ticks arrive too slowly (common during testnet idle hours), wait for the next 1h candle to close naturally. If a candle builder bug is identified, deploy a fix and restart.

## Verify Recovery

Confirm that `decision_skipped_stale_candle` events have stopped. Verify the decision loop emits normal inference events. Check that candle age is below 5,400,000ms and the candle builder is producing fresh candles.
