# ARGOS Forward Test Baseline

> Fecha de baseline: 2026-07-10
> Propósito: versión candidata para forward test largo en PAPER_TRADING.

---

## Version

| Campo | Valor |
|---|---|
| Tag | `v0.9.0-forward-test` |
| Commit | `f875c6dbbfd35e1c69387d0a0ae9c8b6c34fb4a8` |
| Branch | `dev` |

---

## Inference

| Campo | Valor |
|---|---|
| Engine | `StreamingInferencePipeline` |
| Model ID | `qv2_target_spec_v1_reduced_33_primary` |
| Classifier | LogisticRegression (sklearn) |
| Features | 30 |
| Encoding | 0=SELL, 1=HOLD, 2=BUY |
| Safety check | `streaming_inference.py:298-305` validates `classes_` against `SignalSide` |

---

## Trading

| Campo | Valor |
|---|---|
| Mode | `PAPER_TRADING` |
| Symbol | BTC/USDT |
| Timeframe | 1h |
| Portfolio Context | v1 (cluster-based position sizing) |
| Cluster limit | 3 |
| Dynamic thresholds | enabled |
| Risk engine | enabled (1% risk cap, ATR-based SL) |
| Circuit breaker | 5% daily drawdown halt |

---

## Architecture

| Check | Estado |
|---|---|
| Hexagonal arch lint | PASS |
| Typecheck (data-engine) | PASS |
| Lint (data-engine) | 0 errors, 9 warnings (all `no-explicit-any`) |
| Secret scan | PASS (no hardcoded secrets in production code) |

---

## Tests

| Suite | Result |
|---|---|
| Analytics-engine | 868 passed, 3 skipped, 1 warning |
| Data-engine | PASS |
| Pipeline consistency (encoding) | 14/14 PASS |
| Smoke: SELL encoding | PASS |
| Smoke: HOLD encoding | PASS |
| Smoke: BUY encoding | PASS |

---

## Known Issues (non-blocking)

| # | Issue | Prioridad | Impacto |
|---|---|---|---|
| 1 | `PredictEnsembleSignalUseCase` — `_ensemble_decision_raw` has inverted class mapping | Media | Affects `POST /model/predict` endpoint only; NOT used by production streaming loop |
| 2 | `create_targets_triple_barrier()` — encoding swapped vs global standard | Baja | Only called in `scripts/export_training_data.py`, not in production training |
| 3 | `export_training_data.py` — print labels inconsistent with column encoding | Baja | Cosmetic; does not affect exported data |

---

## What is frozen during forward test

- Model weights and architecture
- Feature set (30 features)
- Confidence thresholds
- Portfolio Context v1
- Risk rules (1% cap, ATR-based SL, 5% drawdown halt)
- Inference logic (`StreamingInferencePipeline`)

## What is allowed during forward test

- Dashboards, metrics, logging
- Analysis scripts
- Replay and reporting tools
- Observability improvements (non-behavioral)

---

## How to compare against this baseline

Any future change should be identified by:

```json
{
  "git_commit": "<sha>",
  "model_id": "qv2_target_spec_v1_reduced_33_primary",
  "model_checksum": "<hash del archivo del modelo>",
  "feature_schema_version": 1,
  "portfolio_context_version": "v1"
}
```

If a critical bug is found during forward test:
1. Fix it on a feature branch
2. Create new baseline: `v0.9.1-forward-test`
3. Document what changed and why
