# Quant Validation v2 — ¿Mejora MTF + Funding sobre OHLCV+TA?

> **Pregunta única**: ¿MTF (4h + 1d) y funding rates contienen señal predictiva
> adicional que no existía en QV1 (OHLCV+TA 20 features)?

## Feature space

| Grupo | Features | Método |
|-------|----------|--------|
| TA base | 20 (OHLCV + RSI, EMA, MACD, BB, ATR, ADX, OBV, vol_sma, pct_change) | `ta` library sobre 1h |
| MTF 4h | 15 (mismos indicadores TA sobre OHLCV resampleado a 4h) | `MultiTimeframeAligner` → ffill a 1h |
| MTF 1d | 15 (mismos indicadores sobre OHLCV resampleado a 1d) | `MultiTimeframeAligner` → ffill a 1h |
| Funding | 3 (rate, momentum=diff(3), change) | Parquet → ffill a 1h |
| **Total** | **53** | |

## Excluido

- Open Interest: cobertura histórica insuficiente (~31 días).
  Reevaluar cuando existan ≥2 años de datos.

## Protocolo (idéntico a QV1)

| Framing | Target | Modelos | Shuffle seeds | Nulls |
|---------|--------|---------|---------------|-------|
| 3A multiclass | BUY/HOLD/SELL | LR, RF, HistGB | 10 (42–51) | persist_last_label, always_* |
| 3B binary | BUY vs SELL | LR, RF, HistGB | 10 (42–51) | persist_last_label, always_* |
| 3C regression | vol-adj returns | Ridge, RF Reg, HistGB Reg | 5 (42–46) | persist_last_value, mean |

## Criterio de éxito

QV2 avanza solo si **mejora reproduciblemente sobre QV1**:
1. Mejora clara de métricas (f1, R², dir_acc) vs QV1
2. Shuffle delta positivo y estable en ≥2/3 framings
3. Al menos un modelo supera persist_last_label

Si no mejora:
> H0 extendida: OHLCV + TA + MTF + funding en BTC/USDT 1h
> tampoco contiene alpha explotable suficiente.
> Cerrar línea de investigación.

## Files

| File | Purpose |
|------|---------|
| `common.py` | Data loader (1h + MTF + funding), shared metrics/walkforward/shuffle |
| `run.py` | Execute all 3 framings with QV1 protocol |
| `summary.py` | Compare QV2 vs QV1, emit verdict |
