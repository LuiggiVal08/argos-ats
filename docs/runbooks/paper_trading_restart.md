# Paper Trading Restart Runbook

## Detection

This is a planned procedure triggered at end of day, after a parameter change, or following a bug fix deployment.

## Diagnosis

Run `health_health_check` for full system status. Verify current balance, open positions, and open orders on Binance Testnet. Check which session ID is active.

## Mitigation

1. Save any in-progress session data (export SESSION.json and logs). 2. Stop trading: `config_toggle_mode BACKTESTING`. 3. Cancel all open orders via the exchange API. 4. Close any open positions at market. 5. Flush Redis: `redis_redis_flush` (confirm when prompted). 6. Rebuild the stack: `docker compose down && docker compose up -d`. 7. Wait for all containers to become healthy (~30s). 8. Verify health with `health_health_check`. 9. Set the new paper session ID via environment variable or config. 10. Switch to PAPER_TRADING mode: `config_toggle_mode PAPER_TRADING`.

## Verify Recovery

Confirm the health endpoint returns OK, WebSocket is connected, model is loaded, balance matches expected, and there are zero open positions.
