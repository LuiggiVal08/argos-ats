# Log Forensics Runbook

## Detection

Incident investigation requires structured forensic analysis of production logs. This runbook is used whenever a post-mortem or root-cause analysis is needed.

## Diagnosis

Six rotating log files reside at `ARGOS_LOG_DIR` (default `/app/logs` or `/tmp/argos-logs`): `system.log` (general runtime events), `health.log` (health monitor state changes), `inference.log` (ML inference records), `orders.log` (order lifecycle), `trades.log` (trade execution), and `errors.log` (ERROR-level events only). Each record contains a ULID timestamp, log level, event name, and structured JSON fields.

## Analysis

1. Identify the incident time window. 2. Scan `errors.log` for ERROR-level events within that window. 3. Cross-reference with `health.log` for degradation events. 4. Find the last successful inference in `inference.log` — check `model_checksum`, `feature_hash`, and predicted probabilities. 5. Inspect `orders.log` and `trades.log` for any order activity during the window. 6. Use forensic fields from inference records: `model_version`, `feature_checksum`, `scaler_checksum`, `model_checksum`, `coefficient_hash`, `feature_hash`, `top_10_features`, `latency_ms`, and `candle_age_ms`. 7. Reconstruct the event timeline across all six log files using ULID timestamps.

## Verify Recovery

A complete incident timeline is reconstructed with identified root cause. All relevant log entries are correlated across files and the failure path is fully understood before system resume.
