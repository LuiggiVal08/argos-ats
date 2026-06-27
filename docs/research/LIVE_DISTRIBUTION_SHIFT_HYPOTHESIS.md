# LIVE DISTRIBUTION SHIFT — Hypothesis Document

**Status**: DRAFT — Hypothesis phase
**Date**: 2026-06-27
**Mode**: LIVE_SIMULATION
**Author**: argos-ats audit agent

---

## Estado actual

| Métrica | Valor |
|---------|-------|
| Inferencias observadas | 12 (backfilled) + futuras streaming |
| Trades ejecutados | 0 |
| Rango P(HOLD) | [0.906, 0.956] |
| Media P(HOLD) | 0.933 |
| Media P(SELL) | 0.040 |
| Media P(BUY) | 0.026 |
| Pipeline | ✅ Cargado correctamente |
| Model checksum | ✅ `c08d5ae6e98ebe60` |
| Scaler checksum | ✅ `554eccdfc237595c` |
| Thresholds | ✅ BUY=0.50, SELL=0.50 |
| Lookahead | ✅ 3 |
| Feature set | ✅ reduced_33 (30 features) |
| Contract audit | ✅ 10/10 PASS |

---

## Hipótesis candidatas

Asignación de probabilidad subjetiva inicial (prior beliefs, NO conclusiones):

| # | Hipótesis | P | Descripción |
|---|-----------|---|-------------|
| H1 | **Mercado sin edge** | 35% | El mercado actual (jun 2026, BTC ~$60K) no presenta patrones que el modelo pueda explotar. El modelo predice correctamente HOLD porque efectivamente no hay señales. |
| H2 | **Drift parcial de features** | 30% | 1-5 features específicas han drifting fuera del rango de entrenamiento del RobustScaler. Esto causa que las features escaladas se disparen y el sigmoide de la regresión logística se sature hacia HOLD. |
| H3 | **Dataset research optimista** | 15% | El research (Phase 3.9) sobrestimó la frecuencia de señales debido a walk-forward validation con ventanas traslapadas, data leakage inadvertido, o selección de folds favorables. |
| H4 | **Pipeline mismatch** | 10% | La generación de features en producción difiere de research en aspectos como ordenamiento de columnas, manejo de NaN, lookback window, o parámetros de indicadores técnicos. |
| H5 | **RobustScaler saturation** | 10% | El RobustScaler fue entrenado en datos 2020-01 a 2026-06 pero el mercado actual está en un régimen de precios extremos (~$60K). Las features escaladas caen fuera del rango [-3, +3] visto en training, saturando el sigmoide. |

**Total**: 100%

**Nota**: H2 y H5 están relacionadas. Si el drift de features es lo suficientemente severo, el RobustScaler se satura (H5). La diferencia es si el problema es específico (1-5 features puntuales) o generalizado (muchas features fuera de rango).

---

## Evidencia actual

### Evidencia fuerte

1. **Contract audit 10/10 PASS** — El modelo desplegado es idéntico al validado en Phase 3.9. No hay error de configuración o deployment.
2. **P(HOLD) media = 0.933 en live vs 0.541 en research** — Diferencia de +0.392, Z-score = +8.39σ. Estadísticamente imposible por azar con n=12 si las distribuciones fueran iguales.
3. **KS test p < 1e-9 para las 3 clases** — Las distribuciones de probabilidad son cualitativamente diferentes. No es ruido de muestreo.
4. **PSI 14-20 >> 0.2** — Las 3 métricas de PSI indican drift severo por estándares de la industria.
5. **0% threshold crossings vs 17.85% esperado** — En research, el 17.85% de las decisiones horarias cruzan el umbral 0.50. En live: 0%.

### Evidencia débil

1. **n=12** — El tamaño de muestra es insuficiente para diagnóstico concluyente. Con n=12, la probabilidad de observar 0 trades bajo la tasa histórica (4.22%) es del 59.6% — no significativa.
2. **Solo BTC/USDT** — No hay evidencia de si el mismo comportamiento ocurre en ETH, SOL, etc.
3. **Solo features top-10 loggeadas** — No tenemos acceso a las 30 features completas en los logs actuales para hacer feature-level PSI.
4. **No se capturaron las features escaladas** — No podemos verificar directamente la hipótesis de saturación del RobustScaler.

### Suposiciones

1. **La tasa de trades de research (4.22%) es aplicable al mercado actual** — Asumimos que la frecuencia de señales es estacionaria. Si el mercado actual tiene menos edge, la tasa esperada sería menor.
2. **El pipeline de features es idéntico** — Asumimos que `TaDataPreprocessor.build_features()` produce el mismo output en research y en producción. Esto no se ha verificado directamente.
3. **El RobustScaler no ha sido modificado** — Los checksums coinciden, pero los valores escalados podrían diferir si las features crudas difieren.

### Preguntas abiertas

1. **¿Cuál es el PSI por feature individual?** — No podemos calcularlo sin las 30 features crudas de ambos entornos.
2. **¿Están las features escaladas fuera del rango de entrenamiento?** — Necesitamos capturar `features_norm` en los logs (actualmente no se loggean).
3. **¿El modelo produce el mismo output en un backtest sobre datos recientes (2026)?** — Podemos ejecutar el modelo desplegado sobre un subconjunto reciente del dataset de research para aislar el efecto "mercado actual" del efecto "pipeline".
4. **¿Hay data leakage en el pipeline de research?** — Si las features usan información futura inadvertidamente (e.g., lookahead mal implementado), el research sobrestimaria el edge.
5. **¿Qué pasa si corremos 100 inferencias más?** — La distribución converge o se mantiene igual?

---

## Próximos pasos

Ver `docs/research/LIVE_VALIDATION_PROTOCOL.md` para el plan de investigación detallado.

**Regla fundamental**: No modificar el modelo hasta completar 100 inferencias mínimas o 14 días de operación continua.
