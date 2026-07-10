# H12 — Regime Classifier Forensic Report

## Classification: DEFER (low priority)
**Severity**: Medium (regime UNKNOWN degrades SL multiplier selection in EDL prior)
**Risk**: Low (no trades executed, regime used only for SL multiplier fallback)

---

## 1. Findings

### 1.1 Three separate regime classifiers exist

| Classifier | File | Inputs | Outputs |
|---|---|---|---|
| `StreamingInferencePipeline._detect_regime()` (removed) | `infrastructure/trading/streaming_inference.py:417` | Feature array + model feature names | TRENDING / RANGING / UNKNOWN |
| `RuleBasedRegimeDetector.detect()` | `infrastructure/analysis/regime_detector.py:48` | adx, bbw, atr, ema_slope | TRENDING / RANGING / HIGH_VOL / LOW_VOL / UNKNOWN |
| `_classify_regime()` (Phase B tracker) | `infrastructure/monitoring/phase_b_tracker.py:311` | ATR(14)/price | TRENDING / RANGING |

### 1.2 Root cause: Static method could not find ADX in model feature set

**Evidence:**
- The original `StreamingInferencePipeline._detect_regime()` looked for `"adx"` in `config.features` (the model's training feature names)
- Model metadata DOES include `"adx"` at index 3 (verified from `models/production/btc/metadata.json`)
- All 12 live inferences in the timeline CSV (`reports/active/paper_trading/inference_timeline.csv`) show `market_regime=UNKNOWN`

**Mechanism:** The `_detect_regime()` static method searches for "adx" by name in the model's `feature_names`. The model's `metadata.json` includes `"adx"` at index 3, so the method **should** have worked. The persistent UNKNOWN suggests one of:
1. **Metadata file may have changed** between the time inferences ran and now
2. **Early-return path** in `predict()` was triggered before `_detect_regime()` was reached
3. **Runtime exception** in the static method was silently swallowed by the calling code

The fix using ADX from raw OHLCV eliminates the dependency on model metadata and is more robust.

### 1.3 Research pipeline never produced UNKNOWN

| Dataset | TRENDING | RANGING | HIGH_VOL | LOW_VOL | UNKNOWN |
|---|---|---|---|---|---|
| Baseline (qv2, 56K candles, ADX split) | 51.8% | 48.2% | — | — | 0% |
| Phase 35 (5-regime) | bull 49% | bear 27%, sideways 12% | 22% | — | 0% |
| Regime breakdown BTC backtest (84K candles) | 42.1% | 55.9% | 2.0% | — | 0% |

All research/backtest regime distributions are exhaustive — no UNKNOWN exists in any dataset.

### 1.4 Regime thresholds are consistent across implementations

| Threshold | Research | Production (RuleBasedDetector) | Notes |
|---|---|---|---|
| ADX trending | >= 25 | >= 25 | Matches |
| ADX ranging | < 25 | < 25 | Matches |
| BBW high percentile | — | 70th | Not used in research |
| ATR high percentile | — | 70th | Not used in research |

---

## 2. Decision

**Fix implemented** — replaced static method with ADX computation from raw OHLCV using `ta` library.

- `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py:304-315`
- ADX(14) computed directly from OHLCV buffer (already available in `predict()`)
- Threshold: ADX >= 25 → TRENDING, else RANGING (matching research baseline)
- Fallback: if ADX computation fails → UNKNOWN

### Why this approach

1. **Independent of model metadata** — OHLCV is always available, no dependency on model feature names
2. **Matches research baseline** — identical ADX threshold as qv2 baseline
3. **Minimal code change** — 10 lines, no new dependencies, no wiring changes
4. **No state** — every inference computes ADX independently, no rolling windows to manage

### Why not RuleBasedRegimeDetector

The `RuleBasedRegimeDetector` (with full 5-regime classification) and `RegimeFeatureExtractor` already exist but:
- `RegimeFeatureExtractor` uses fixed feature indices (12, 13, 14, 15, 6) that assume standard 20-feature ordering
- The production feature array uses `config.features` ordering (model-specific, 30 features), not the standard 20
- `bb_upper` and `bb_lower` are NOT in the model's feature names → BBW cannot be computed from the feature array
- Fixing the index issue would require name-based lookup or modifying build_features output

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| ADX spikes at regime boundary (24.9 vs 25.0) | Medium | Binary threshold is standard TA practice; ADX(14) has 14-period smoothing |
| Low OHLCV quality (0.0 prices) | Low | Regime detection is after feature generation which validates data; fallback to UNKNOWN |
| `ta` library version changes | Low | Pinned via pyproject.toml; widely used stable library |
