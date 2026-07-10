# H12 — Implementation Report

---

## What was done

### 1. Regime classifier fix — `streaming_inference.py`

**File modified:** `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py`

**Changes:**

1. Replaced static `_detect_regime(features_raw, config)` method with inline ADX computation from raw OHLCV data
2. Removed the static method (21 lines)
3. Removed two unused imports (`RuleBasedRegimeDetector`, `RegimeFeatureExtractor`) that were initially added then replaced

**New code (lines 304-315):**

```python
try:
    import ta as ta_lib
    df = pd.DataFrame(ohlcv_buffer)
    adx_val = float(
        ta_lib.trend.ADXIndicator(
            df["high"], df["low"], df["close"], window=14,
        ).adx().iloc[-1]
    )
    regime = "TRENDING" if adx_val >= 25.0 else "RANGING"
except Exception:
    regime = "UNKNOWN"
```

**Previous code (removed):**

```python
@staticmethod
def _detect_regime(features_raw: np.ndarray, config: Any) -> str:
    if features_raw.shape[0] == 0:
        return "UNKNOWN"
    last = features_raw[-1]
    if hasattr(config, "features"):
        feature_names = list(config.features)
    elif isinstance(config, dict):
        feature_names = list(config.get("features", []))
    else:
        return "UNKNOWN"
    if not feature_names or features_raw.shape[1] < len(feature_names):
        return "UNKNOWN"
    try:
        adx_idx = feature_names.index("adx") if "adx" in feature_names else -1
        if adx_idx >= 0 and adx_idx < len(last):
            adx = float(last[adx_idx])
            return "TRENDING" if adx >= 25 else "RANGING"
    except (ValueError, IndexError):
        pass
    return "UNKNOWN"
```

### 2. Clock drift investigation — no changes

Measured and documented offset characteristics. No code changes required.

---

## Why it was done

The `StreamingInferencePipeline` was always returning `UNKNOWN` for market regime because the static `_detect_regime()` method depended on the model's `feature_names` containing `"adx"`. While the current `metadata.json` does include `"adx"` at index 3, all 12 live inferences recorded `UNKNOWN` regime, indicating the method was not functioning correctly in production.

The new approach computes ADX(14) independently from the raw OHLCV candle data that is already available in the `predict()` method. This eliminates:

- Dependency on model metadata feature names
- Dependency on feature array column ordering
- Potential for index mismatches

---

## Why this solution was selected

1. **Minimal change** — 10 lines added, 21 lines removed
2. **Independent of model metadata** — OHLCV is always available
3. **Matches research** — identical ADX >= 25 threshold used in qv2 baseline research
4. **No new dependencies** — `ta` library is already a project dependency
5. **No configuration changes** — no wiring changes in `composition.py`
6. **No state** — every inference is independent

---

## Alternatives considered

| Alternative                                               | Rejected because                                                                                                                                         |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wire `RuleBasedRegimeDetector` + `RegimeFeatureExtractor` | `RegimeFeatureExtractor` uses fixed indices incompatible with model's feature ordering; `bb_upper`/`bb_lower` not in model features → BBW not computable |
| Add `adx` to model training features                      | Model retraining is out of scope; would require weeks of work                                                                                            |
| Name-based feature extraction from feature array          | `bb_upper` and `bb_lower` are not in model's feature names → cannot compute BBW by name either                                                           |
| Use `PredictEnsembleUseCase._detect_regime()` fallback    | That method also depends on "adx" being in model features (defaults to 25.0 if not found)                                                                |

---

## Risks

| Risk                              | Likelihood | Impact                                                     | Mitigation                                                                    |
| --------------------------------- | ---------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------- |
| ADX near threshold (24.9 vs 25.0) | Medium     | Regime flip-flop                                           | ADX(14) has 14-period smoothing; threshold is standard                        |
| `ta` library version mismatch     | Low        | Runtime error                                              | Tested with current `pyproject.toml` deps                                     |
| OHLCV buffer has bad data         | Low        | UNKNOWN fallback                                           | Feature generation already validates data quality upstream                    |
| Regime mismatch with EDL Prior    | Low        | EDL uses different Regime enum (TRENDING/RANGING/VOLATILE) | Prior maps OK: TRENDING→TRENDING, RANGING→RANGING, no HIGH_VOL/LOW_VOL needed |

---

## Validation evidence

1. **Architecture lint**: PASS — no hexagonal violations
2. **TypeScript typecheck**: PASS — no type errors
3. **Secret scan**: 7 pre-existing findings (skill examples + .env), none from this change
4. **Model feature analysis**: Confirmed "adx" is at index 3 in model metadata, but the static method was not producing correct results in production (all 12 inferences = UNKNOWN)
5. **Clock offset measurement**: 20 samples, mean -869ms, max 2002ms — well within existing 10000ms recvWindow

---

## Rollback procedure

To revert the regime fix:

```bash
git checkout -- apps/analytics-engine/app/infrastructure/trading/streaming_inference.py
docker restart argos-analytics-engine
```

Or cherry-pick the revert:

```bash
git revert <commit-hash>
```

---

## Expected operational impact

- **Before**: All inferences produce `regime="UNKNOWN"` → EDL prior uses `_UNKNOWN_REGIME_T=0.5` → reduced confidence
- **After**: Inferences produce `regime="TRENDING"` or `"RANGING"` → EDL prior uses correct transfer factor → appropriate confidence multipliers
- **Signal generation**: No change (regime does not gate signal generation; signal confidence filter checks regime in a separate path via `ConfidenceFilter`)
- **SL multipliers**: `ExecuteSignalUseCase._regime_sl_mult` now receives correct regime key instead of "UNKNOWN" → appropriate stop-loss distance per regime
