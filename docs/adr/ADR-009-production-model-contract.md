# ADR-009: Production Model Contract

**Status:** ACCEPTED (2026-06-26)

## Context

Production model files (model weights, scaler parameters, metadata, feature names) must be validated before loading to prevent silent corruption. A mismatch between training-time configuration and production-time loading can cause incorrect inferences without any error signal — the most dangerous class of bug in an automated trading system.

## Options Considered

- **No validation** — rejected; silent corruption risk is unacceptable.
- **Git-based model versioning** — rejected; model files are too large for git.
- **Checksum-based validation** — selected as the enforcement mechanism.
- **Signed model files** — overkill for v1; adds key management overhead.

## Decision

Every model checkpoint includes checksums for model weights, scaler parameters, feature configuration, and metadata. At inference startup, `load_checkpoint()` verifies all checksums before loading. Mismatch triggers an ERROR log entry and transitions the system to a degraded state.

Checksums are stored in `metadata.json`:

```json
{
  "model_checksum": "c08d5ae6e98ebe60",
  "scaler_checksum": "554eccdfc237595c",
  "feature_checksum": "b6493da7c9176a6f"
}
```

## Consequences

**Positive:**
- Silent corruption is detected at startup — prevents incorrect inference execution
- Forensic traceability — every inference is linked to a specific model version
- Clear degraded state signaling for automated recovery

**Negative:**
- Additional 2-3ms at startup for checksum verification
- Checksum metadata must be updated when model files change

## Alternatives Rejected

- **No validation:** silent corruption risk is unacceptable for a financial system.
- **Git LFS:** operational overhead without meaningful benefit over checksum validation.

## References

- `models/production/btc/metadata_primary.json`
- `apps/analytics-engine/app/infrastructure/ml/checkpoint.py`
