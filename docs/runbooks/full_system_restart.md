# Full System Restart Runbook

## Detection

This is a planned procedure for system maintenance, version upgrade, or post-incident recovery.

## Diagnosis

Check `git status` for uncommitted changes. Verify pending changes in config.json and .env. List running containers. Confirm backup status of stateful data.

## Mitigation

1. Backup SESSION.json, the logs/ directory, state/ directory, and .env file. 2. Tear down: `docker compose down`. 3. Pull latest: `git pull`. 4. Review config.json and .env for breaking changes against the new release. 5. Bring up: `docker compose up -d`. 6. Wait for all three containers (broker, data-engine, analytics-engine) to report healthy (~30s). 7. Run `health_health_check`. 8. Restore state from backup if needed. 9. Verify the tick pipeline: WebSocket connected, ticks flowing into Redis, candles building, decision loop active.

## Verify Recovery

All three containers are UP and healthy. Health endpoint reports WS open, model loaded, ticks flowing, Redis reachable, and the decision loop running normally.
