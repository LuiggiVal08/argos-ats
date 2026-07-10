# ARGOS Forward Test Baseline

> Fecha de baseline: 2026-07-10
> Propósito: versión candidata para forward test largo en PAPER_TRADING.

---

## Version

| Campo | Valor |
|---|---|
| Tag | `v0.9.1-forward-test` |
| Commit | `30fcd17` |
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

## Model Checksums (BTC/USDT)

| Archivo | MD5 |
|---|---|
| `models/production/btc/model.pkl` | `551be07c74d8cba6363a4852fb5b2488` |
| `models/production/btc/scaler.pkl` | `6f9981c7f95fa72ff0615545e26ddd29` |
| `models/production/btc/metadata.json` | `3380de611149eac0ad2dfba4ffd37151` |

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

## Commits in this baseline

| SHA | Description |
|---|---|
| `30fcd17` | docs: ADRs, runbooks, forensic reports, research |
| `5ef390e` | test: EDL, replay, event store, observability, target leakage |
| `c2c3dce` | feat: runtime modules (EDL, Event Sourcing, Replay, Infra, Logging) |
| `4fec059` | docs: forward test baseline doc + tracking issues |
| `f875c6d` | fix: label encoding in create_targets |

---

## How to reproduce this baseline

```bash
git checkout v0.9.1-forward-test
pip install -r apps/analytics-engine/requirements.txt
cd apps/analytics-engine && uvicorn app.main:app
```

## How to compare against this baseline

Any future change should be identified by:

```json
{
  "git_commit": "30fcd17",
  "model_id": "qv2_target_spec_v1_reduced_33_primary",
  "model_checksum": "551be07c74d8cba6363a4852fb5b2488",
  "feature_schema_version": 1,
  "portfolio_context_version": "v1"
}
```

If a critical bug is found during forward test:
1. Fix it on a feature branch
2. Create new baseline: `v0.9.2-forward-test`
3. Document what changed and why
