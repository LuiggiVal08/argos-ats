# Regime Generation Dependency Graph

## Production path (after H12 fix)

```
OHLCV buffer (candles)
    │
    ├─► TaDataPreprocessor.build_features()
    │      └─► 30-feature array (model-specific ordering)
    │
    └─► ADX(14) from OHLCV (ta.trend.ADXIndicator)
           │
           ▼
    ADX >= 25 → "TRENDING"
    ADX < 25  → "RANGING"
    Error     → "UNKNOWN"
           │
           ▼
    StreamingInferenceResult.regime
           │
           ├─► TradingSignal.metadata["regime"]
           │      └─► ExecuteSignalUseCase._regime_sl_mult[regime]
           │
           └─► tracker.append(market_regime=regime)
                  └─► inference_timeline.csv
```

## Alternative path (Ensemble pipeline — not used by streaming)

```
OHLCV
    │
    ├─► build_features()
    │      └─► feature array
    │
    └─► _extract_market(features, feature_names)
           │  (name-based lookup: "adx", "bbw", "atr", ...)
           ▼
    MarketContext { regime: TRENDING | RANGING }
           │
           ├─► ConfidenceFilter (regime_ok)
           └─► EDL Prior / BayesianUpdate
```

## Unused path (RuleBasedRegimeDetector — index-incompatible with model features)

```
OHLCV
    │
    ├─► RegimeFeatureExtractor
    │      ├─ ADX from raw OHLCV ✓
    │      ├─ BBW from features[12,13,14] ✗ (wrong indices for model ordering)
    │      ├─ ATR from features[15] ✗
    │      └─ EMA slope from features[6] ✗
    │
    └─► RuleBasedRegimeDetector.detect()
           │  (thresholds + rolling percentiles)
           ▼
    MarketContext { regime: 5-value }
```

## Research/Backtest path

```
OHLCV (historical)
    │
    ├─► build_features()
    │
    └─► ADX(14) from OHLCV
           │
           ▼
    ADX >= 25 → "TRENDING" (51.8%)
    ADX < 25  → "RANGING"  (48.2%)
           │
           ▼
    regime_breakdown.csv
```

## Threshold reference

| Threshold    | Value | Standard?               |
| ------------ | ----- | ----------------------- |
| ADX trending | >= 25 | Yes (Wilder's standard) |
| ADX ranging  | < 25  | Yes                     |
| ADX window   | 14    | Yes                     |
