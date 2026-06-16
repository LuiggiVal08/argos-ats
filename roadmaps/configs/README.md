# Configuraciones de experimentos

> Cada archivo YAML define una configuración completa de pipeline para un experimento.
> Los experimentos se ejecutan desde la branch de su fase correspondiente.
> Los resultados se comparan contra el baseline congelado.

## Convención de nombres

```
target_<metodo>.yaml        → Configuraciones de target/labeling
features_<version>.yaml     → Feature sets
model_<tipo>.yaml           → Arquitecturas de modelo
pipeline_<experimento>.yaml → Pipeline completo
```

## Estructura de un config

```yaml
name: "experimento-descriptivo"
version: "0.1.0"
date: "2026-06-15"

data:
  symbols: ["BTC/USDT"]
  timeframe: "1h"
  start: "2020-01-01"
  end: "2026-06-01"

target:
  method: "triple_barrier"  # triple_barrier | momentum | regression | binary
  params:
    lookahead: 5
    atr_multiplier: 1.5

features:
  groups: ["ohlcv", "ta"]  # ohlcv | ta | returns | lags | volatility | zscore | momentum | regime | cross_symbol
  lookback: 20

model:
  type: "lstm"  # lstm | xgb | rf | lr | lightgbm | catboost | gru | tcn | transformer
  params:
    layers: [128, 64, 32, 16]
    dropout: 0.2

evaluation:
  metrics: ["f1_macro", "kappa", "mcc", "balanced_accuracy"]
  gates: ["g0_no_alpha", "g1_signal_existence"]
```

## Fases y configs asociados

| Fase | Configs |
|------|---------|
| F2 — Target audit | `target_triple_barrier.yaml`, `target_momentum.yaml`, `target_regression.yaml`, `target_binary.yaml` |
| F3 — Feature audit | `features_v1.yaml` |
| F4 — New features | `features_v2.yaml` |
| F5 — Simple baselines | `model_lr.yaml`, `model_rf.yaml`, `model_lightgbm.yaml`, `model_xgboost.yaml`, `model_catboost.yaml` |
