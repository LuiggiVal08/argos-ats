# Model Corruption Runbook

## Detection

Analytics-engine logs contain `checkpoint load failed`, inference logs show a checksum mismatch (stored `model_checksum` differs from loaded), or `load_checkpoint()` reports ERROR. The system may enter a degraded state.

## Diagnosis

Inspect `inference.log` for checksum fields — compare stored vs expected checksums. Verify model file integrity with `ls -la models/production/btc/` and cross-reference file sizes and timestamps against `metadata.json` checksums.

## Mitigation

If the primary model is corrupted, promote the shadow model by restarting AE with `ARGOS_MODEL_VERSION=shadow` set as an environment variable. If both primary and shadow are corrupted, restore from the archive backup. If all backups are corrupted, re-run the Phase 4 training pipeline to regenerate the model from the historical feature store.

## Verify Recovery

Confirm AE startup passes `load_checkpoint()` without error. Verify inference logs show correct and matching checksums. Run `health_health_check` and confirm `system_status=SAFE` or `NORMAL`.
