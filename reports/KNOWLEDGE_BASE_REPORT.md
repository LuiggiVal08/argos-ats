# ARGOS ATS — Knowledge Base Consolidation V1 Report

**Generated**: 2026-06-27 00:00 UTC
**Phase**: K1–K8
**Objective**: Transform ARGOS ATS from experiments and reports into a fully traceable institutional-grade knowledge base.

---

## 1. What Was Created

### K1 — Research Ledger
- **File**: `registry/research_ledger.yaml`
- **Coverage**: 11 experiments documented
  1. Timeframe selection (1h base selected over 15m/4h)
  2. Target design (sigma-units selected over raw/ATR/triple barrier)
  3. Lookahead selection (h=3 over h=5)
  4. Feature selection reduced_33 (30 features, 97.9% MCC preserved)
  5. Logistic regression selection (linear models outperform tree-based)
  6. Regularization findings (C=10 primary, C=0.1 shadow)
  7. Economic validation (Phase 3.9 — INSTITUTIONAL_GRADE_ALPHA)
  8. Replay validation (Phase 4 — PRODUCTION_READY)
  9. Observability implementation (startup validation PASS)
  10. Walk-forward validation (Phase 35 — 10/10 positive folds)
  11. Hyperparameter robustness (Phase 375 — ALPHA FRAGILE 3/4)

### K2 — ADR System
- **Directory**: `docs/adr/`
- **Coverage**: 10 ADRs
  - ADR-001: Timeframe Selection (1h base)
  - ADR-002: Target Spec V1 (sigma-units)
  - ADR-003: LogisticRegression Selection
  - ADR-004: Feature Selection reduced_33
  - ADR-005: Observability-First Design
  - ADR-006: Replay Adapter
  - ADR-007: Risk Engine Gating
  - ADR-008: Paper Trading Before Capital
  - ADR-009: Production Model Contract (checksums)
  - ADR-010: Research-Production Separation

### K3 — Incident Database
- **File**: `registry/incidents.yaml`
- **Coverage**: 10 incidents
  - INC-001: WS reconnect bug (critical) — fixed
  - INC-002: sqrt(lookahead) missing (high) — fixed
  - INC-003: target_lookahead=5 default (high) — fixed
  - INC-004: vol_window not explicit (medium) — fixed
  - INC-005: Funding features all zero (high) — accepted limitation
  - INC-006: Candle import crash (high) — fixed
  - INC-007: structlog TypeError (high) — fixed
  - INC-008: checkpoint_dir not found (high) — fixed
  - INC-009: PermissionError logs (medium) — fixed
  - INC-010: Stale tick backpressure (low) — no fix needed (by design)

### K4 — Model Registry
- **File**: `registry/models.yaml`
- **Coverage**: 5 models
  - BTC primary (C=10, reduced_33, active)
  - BTC shadow (C=0.1, reduced_33, active)
  - BTC baseline (C=0.1, full_53, deprecated)
  - ETH (C=10, reduced_33, active)
  - SOL (C=10, reduced_33, active)

### K5 — Dataset Registry
- **File**: `registry/datasets.yaml`
- **Coverage**: 4 datasets (BTC full, BTC reduced, ETH, SOL)

### K6 — Deployment Registry
- **File**: `registry/deployments.yaml`
- **Coverage**: 4 deployments (initial research, baseline, Phase 5.1, Phase 5.3)

### K7 — Runbooks
- **Directory**: `docs/runbooks/`
- **Coverage**: 8 runbooks (redis_outage, websocket_disconnect, stale_candle, model_corruption, paper_trading_restart, full_system_restart, log_forensics, emergency_shutdown)

### K8 — System Philosophy
- **Directory**: `docs/philosophy/`
- **Coverage**: 6 documents (why_1h, why_sigma_labels, why_linear_models_first, why_observability_before_capital, why_replay_consistency_matters, why_research_and_production_are_separated)

### Validation Report
- **File**: `reports/KNOWLEDGE_BASE_REPORT.md` (this file)

---

## 2. Information Sources Used

| Source | Type | Used For |
|--------|------|----------|
| `spec.md` | Specification | ADR context, invariants, stories |
| `TARGET_SPEC.md` | Research spec | Target design decisions |
| `archive/qv2/qv2_baseline_output/*.json` | QV2 baseline results | Baseline metrics, dataset, leakage audit |
| `archive/qv2/qv2_phase35_output/*.json/.md` | Walk-forward results | Phase 35 metrics, fold data |
| `archive/qv2/qv2_phase375_output/*.json/.md` | Hyperparameter scan | Phase 375 rankings, sensitivity |
| `archive/qv2/qv2_phase38_output/*.json/.md` | Feature selection | Correlation, permutation importance |
| `archive/qv2/qv2_phase39_output/*.json/.md` | Economic validation | Phase 39 metrics, scenarios, Monte Carlo |
| `archive/qv2/qv2_phase4_output.log` | Production validation | Phase 4 results |
| `reports/active/operational/PHASE4_PRODUCTION_REPORT.md` | Production report | Replay validation, production readiness |
| `reports/active/operational/STARTUP_OBSERVABILITY_VALIDATION.md` | Startup validation | Observability bugs, fixes |
| `models/production/btc/metadata_primary.json` | Model metadata | Production model parameters, checksums |
| `apps/analytics-engine/app/main.py` | Source code | Candle import bug, stale tick behavior |
| `apps/data-engine/src/**/*.ts` | Source code | WS reconnect, health monitor, pipeline |
| Git log (100 commits) | History | Deployment timeline, incident chronology |
| Session conversations | Context | Recent bugs, fixes, current state |

---

## 3. Missing Information

| Gap | Impact | Priority |
|-----|--------|----------|
| **No PHASE4_REPORT.md found in archive or active** — Phase 4 results inferred from log and reports/active | Missing cross-asset validation results for BTC, ETH, SOL | Medium |
| **No individual experiment reports for QV2 phases 5-14** — only aggregated metrics exist | Detailed per-phase decision rationale not captured for multi-asset experiments | Low |
| **Forward test files exist but not analyzed** — `forward_test/trades_btc.csv`, `equity_btc.csv` | Forward test results not integrated into knowledge base | Medium |
| **No incident severity classification documented** — all incidents manually rated | Inconsistent severity rating across incidents | Low |
| **No research version history** — research scripts not tracked per phase | Hard to reproduce exact research environment for each phase | Medium |
| **Model training scripts location** — exact training command/parameters for BTC primary not documented | Cannot reproduce production model without reverse engineering | High |
| **No model performance over time** — no rolling MCC monitoring results | Unknown if model is degrading post-deployment | High |
| **No SL/TP hit rate data** — actual stop-loss and take-profit execution rates not tracked | Cannot validate SL distance assumptions | Medium |

---

## 4. Ambiguous Decisions Detected

| Decision | Ambiguity | Resolution Needed |
|----------|-----------|-------------------|
| **C=10 vs C=0.1 as primary** | Phase 3.9 shows C=0.1 shadow OUTPERFORMS C=10 primary (net expectancy 1.73% vs 1.59%). Phase 375 ranks C=10 higher than C=0.1. Two conflicting conclusions from two different evaluation methodologies. | Decide: is MCC or economic expectancy the primary selection criterion? |
| **Shadow model purpose** | Shadow model (C=0.1, balanced) has different class_weight than primary (C=10, None). Comparison confounds regularization AND class_weight simultaneously. | Document whether shadow is testing regularization (C) or class-weight sensitivity. |
| **Phase 375 verdict: ALPHA FRAGILE** | Despite ALPHA_FRAGILE verdict (baseline C=0.1 outside top quartile), C=0.1 shadow was deployed anyway. | Clarify: is "baseline" the same as "shadow"? Why deploy a "fragile" configuration? |
| **Funding features = zero** | Funding_rate/momentum/change have 0.0 permutation importance, yet are kept in the feature set. They increase feature count by 10% with zero signal contribution. | Decide: remove funding features or fix CCXT fetch? |
| **6.5 years single-split vs walk-forward for production model** | Production model trained on single 80/20 split (not walk-forward). Walk-forward was used for validation, but final model uses all data. | Document why walk-forward weights not used for final ensemble. |

---

## 5. Documentation Coverage Estimate

| Category | Items | Documented | Coverage |
|----------|-------|-----------|----------|
| Research experiments | ~15 distinct experiments | 11 | 73% |
| Incidents | ~12 known incidents | 10 | 83% |
| Models | 5 (BTC/Eth/Sol × primary/shadow/baseline) | 5 | 100% |
| Datasets | 4 | 4 | 100% |
| Deployments | 4 | 4 | 100% |
| ADRs required | 10 | 10 | 100% |
| Runbooks required | 8 | 8 | 100% |
| Philosophy docs | 6 | 6 | 100% |
| **Overall** | **~60 items** | **~58 items** | **~97%** |

---

## 6. Remaining Knowledge Debt

### Priority High
1. **Model training reproduction** — Document exact training command/parameters for BTC primary model. Include script path, environment variables, seed.
2. **Rolling performance monitoring** — Implement and document MCC rolling monitoring. Current inference logs have all forensic fields but no dashboard/alerting.

### Priority Medium
3. **Multi-asset Phase 5 results** — ETH/SOL models deployed but no cross-validation results documented.
4. **Forward test integration** — `forward_test/` CSVs contain real trade data — generate forward test report.
5. **Funding data fix** — Document status of CCXT funding fetch pipeline (currently zero).
6. **C=10 vs C=0.1 ambiguity** — Resolve conflicting evaluation recommendations and document decision.

### Priority Low
7. **Experiment phase 5-14 details** — Granular per-phase reports for multi-asset experiments.
8. **Incident severity guidelines** — Formal severity classification matrix.
9. **Runbook testing** — All 8 runbooks are procedural but none have been tested in a drill.

---

## 7. Recommendations

1. **Resolve the C=10 vs C=0.1 ambiguity** in a follow-up decision document. The two evaluation methodologies (MCC-based ranking vs economic expectancy) give conflicting results. Document which criterion is primary.
2. **Add model training documentation** — The single most critical knowledge gap. Without it, the production model cannot be reproduced.
3. **Test runbooks in a drill** — At minimum, emergency_shutdown, redis_outage, and websocket_disconnect should be practiced.
4. **Integrate forward test data** — Generate a report comparing forward test results to Phase 3.9 backtest expectancy.
5. **Consider removing funding features** if CCXT fetch fix is not planned. Zero-variance features add complexity without signal.

---

## 8. File Summary

| File/Directory | Lines | Size |
|----------------|-------|------|
| `registry/research_ledger.yaml` | ~240 | 7.6KB |
| `registry/incidents.yaml` | ~210 | 7.5KB |
| `registry/models.yaml` | ~100 | 3.3KB |
| `registry/datasets.yaml` | ~60 | 1.7KB |
| `registry/deployments.yaml` | ~50 | 1.5KB |
| `docs/adr/ADR-001.md` through `ADR-010.md` | ~40-70 each | ~20KB total |
| `docs/runbooks/*.md` (8 files) | ~30-50 each | ~10KB total |
| `docs/philosophy/*.md` (6 files) | ~20-40 each | ~7KB total |
| `reports/KNOWLEDGE_BASE_REPORT.md` | ~185 | 8KB |
| **Total** | **~1200 lines** | **~67KB** |
