# ROADMAP QUANTITATIVO V2

> **Proyecto:** ARGOS — Pipeline de predicción cuantitativa para criptomonedas
> **Versión roadmap:** 2.0
> **Estado:** ⏳ En definición
> **Principio rector:** Encontrar alpha reproducible. No "cumplir roadmap".

---

## ⚠️ PRINCIPIO FUNDAMENTAL

Este roadmap **NO** es un pipeline de modelado.

Es un **experimento de falsación científica**.

### Hipótesis

| Hipótesis | Enunciado |
|-----------|-----------|
| **H0** | No existe información predictiva explotable en OHLCV + TA en timeframe 1h |
| **H1** | Existe señal estadística débil pero consistente y explotable |

### Regla

- Si H0 no se rechaza → detener ML en este dataset
- Si H0 se rechaza → recién ahí se habilita el roadmap completo

---

## 0. CRITERIO DE ÉXITO GLOBAL + BASELINE DE DESTRUCCIÓN

### Definición de éxito

El sistema se considera operativamente válido **solo si** cumple:

| Métrica | Objetivo | Por qué |
|---------|----------|---------|
| MCC | > 0.25 | Correlación positiva entre predicción y realidad |
| Kappa | > 0.20 | Mejora sustancial sobre el azar |
| Sharpe (OOS, con costos) | > 1.0 | Rentabilidad ajustada por riesgo real |
| BUY/SELL recall | > 35% c/u | Sin colapso a HOLD. Señal utilizable en ambas direcciones |

### Baseline de destrucción

Si el sistema **no supera estos mínimos con significancia estadística**, el framing actual (clasificación supervisada sobre features OHLCV+TA) se considera **hipótesis falsa** y se abandona:

| Baseline | Condición de invalidez |
|----------|------------------------|
| **Always-HOLD** | Accuracy del modelo ≤ accuracy de predecir siempre HOLD (+/- 1% intervalo de confianza) |
| **Shuffle labels** | F1 del modelo ≤ F1 de modelo entrenado con labels aleatorizados (p > 0.05) |
| **Logistic Regression simple** | Modelo complejo no supera a regresión logística en F1 macro + Kappa |

> Si después de Fase 5 ningún modelo supera estos baselines → **el proyecto de ML con este dataset se invalida**. No más features, no más capas, no más stacking. Cambiar framing o abandonar.

---

## 1. BASELINE CONGELADO

### Versión del sistema actual

- **Fecha baseline:** 2026-06-15
- **Modelo:** LSTM (4 capas: 128→64→32→16) + XGBoost + MetaModel (stacking)
- **Lookback:** 20 velas (1h)
- **Features:** 20 (OHLCV + RSI, EMA, MACD, BB, ATR, ADX, OBV, volume_sma, pct_change)
- **Target:** Triple Barrier con lookahead=5, atr_multiplier=1.5
- **Label distribution:** BUY ≈ 24%, SELL ≈ 25%, HOLD ≈ 51%
- **Datos:** 28,032 muestras por símbolo (~3.2 años de velas 1h)

### Checkpoints

| Símbolo | Versión | LSTM val_acc | XGB val_acc | Meta train_acc |
|---------|---------|-------------|-------------|----------------|
| BTC/USDT | v1.0.1781547224 | 0.4844 | 0.4840 | 0.5169 |
| ETH/USDT | v1.0.1781549314 | — | — | 0.5174 |
| SOL/USDT | v1.0.1781551368 | — | — | 0.4983 |
| XRP/USDT | v1.0.1781553434 | — | — | 0.5076 |
| DOGE/USDT | v1.0.1781555468 | — | — | 0.5111 |
| AVAX/USDT | v1.0.1781557604 | — | — | 0.4914 |

### Métricas por símbolo

#### BTC/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.5255 |
| Kappa | 0.1052 |
| BUY recall | 0.0226 |
| SELL recall | 0.1673 |
| HOLD recall | 0.9659 |
| Pred dist | BUY=1.3% SELL=8.7% HOLD=90.0% |
| True dist | BUY=24.7% SELL=26.0% HOLD=49.3% |
| Confianza media | 0.519 |
| Conf matrix | BUY→ {156, 852, 5909} SELL→ {113, 1219, 5954} HOLD→ {91, 381, 13357} |

#### ETH/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.4994 |
| Kappa | 0.0045 |
| BUY recall | 0.0094 |
| SELL recall | 0.0008 |
| HOLD recall | 0.9966 |
| Pred dist | BUY=0.5% SELL=0.1% HOLD=99.4% |
| Conf matrix | BUY→ {63, 17, 6621} SELL→ {32, 6, 7316} HOLD→ {40, 7, 13930} |

#### SOL/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.5109 |
| Kappa | 0.0509 |
| BUY recall | 0.0436 |
| SELL recall | 0.0460 |
| HOLD recall | 0.9836 |
| Pred dist | BUY=2.4% SELL=2.4% HOLD=95.2% |

#### XRP/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.4864 |
| Kappa | 0.0032 |
| BUY recall | 0.0017 |
| SELL recall | 0.0038 |
| HOLD recall | 0.9982 |
| Pred dist | BUY=0.1% SELL=0.4% HOLD=99.5% |

#### DOGE/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.5152 |
| Kappa | 0.0849 |
| BUY recall | 0.0318 |
| SELL recall | 0.1372 |
| HOLD recall | 0.9506 |
| Pred dist | BUY=2.0% SELL=8.8% HOLD=89.2% |

#### AVAX/USDT
| Métrica | Valor |
|---------|-------|
| Accuracy | 0.4946 |
| Kappa | 0.0742 |
| BUY recall | 0.0167 |
| SELL recall | 0.1443 |
| HOLD recall | 0.9519 |
| Pred dist | BUY=0.6% SELL=9.1% HOLD=90.3% |

### Diagnóstico consolidado

| Hallazgo | Evidencia |
|----------|-----------|
| **Colapso a HOLD** | BTC 90%, ETH 99.4%, SOL 95.2%, XRP 99.5%, DOGE 89.2%, AVAX 90.3% |
| **Kappa ≈ aleatorio** | ETH 0.004, XRP 0.003 (indistinguibles de random) |
| **BUY/SELL no existen** | BUY recall 0.2-4.4%, SELL recall 0.01-16.7% (solo BTC marginal) |
| **Stacking no mejora** | LSTM val_acc 0.46-0.52, XGB idéntico, Meta hereda debilidad |
| **Target débil** | 50% HOLD → modelo óptimo = predecir siempre HOLD, accuracy ≈ 50% |
| **Señal débil existe** | Shuffle test: F1 original > shuffled por +0.05 a +0.16 |
| **Features insuficientes** | Solo OHLCV+TA básico. Sin lags, retornos, volatilidad, cross-symbol |

---

## GATE DEL SPRINT

### 🔴 GATE 0A — SIGNAL EXISTENCE (STATISTICAL)

Condiciones para pasar (deben cumplirse todas):

- IC mean > 0.01 consistente a través de splits
- IC sign consistency > 55%
- Permutation test p-value < 0.1

### 🔴 GATE 0B — EXPLOITABILITY PROXY

- Directional accuracy > 0.51 estable en walk-forward
  **O**
- IC > 0.02

---

**Si GATE 0A y 0B fallan:**
→ NO existe señal en este feature space.
→ No continuar con ML en este dataset.

**Si GATE 0A pasa pero 0B falla:**
→ Hay señal estadística no explotable (micro-edge).
→ Posible: necesita feature engineering o nuevo framing.

**Si ambos pasan:**
→ Alpha real detectado. Activar pipeline post-sprint.

---

## 🔐 FRAMING LOCK (post-sprint — solo si hay alpha)

Una vez confirmada señal en el sprint, elegir **UN** framing y congelarlo:

| Framing | Cuándo elegirlo |
|---------|-----------------|
| **Clasificación multiclase** (BUY/SELL/HOLD) | Si Kappa > 0.1 y BUY/SELL recall > 15% c/u |
| **Clasificación binaria** (TRADE/NO-TRADE) | Si señal direccional débil pero edge en magnitud |
| **Regresión de retornos** | Si clasificación no separa pero hay correlación lineal |
| **Ranking** (top-k) | Si múltiples activos con señal débil pero rankeable |

**Regla:** No se permite cambiar framing sin reset completo del pipeline.

---

## 2. FASE 1 — CONGELAR BASELINE

### Objetivo

Preservar una línea base reproducible para comparar cualquier mejora futura.

### Acciones

- [ ] Documentar métricas actuales en este roadmap (sección 1 — hecho)
- [ ] Almacenar checkpoints en `apps/analytics-engine/checkpoints/`
- [ ] Almacenar predicciones en `apps/analytics-engine/data/predictions/`
- [ ] Almacenar validation reports en `apps/analytics-engine/reports/validation/`
- [ ] Exportar confusion matrices y distribución de predicciones

### Happy Path

Todo queda documentado y se puede comparar cualquier mejora futura.

### Sad Path

Si no existe baseline reproducible → detener cualquier entrenamiento nuevo. Generar primero la documentación faltante.

---

## 3. FASE 2 — SIGNAL VALIDATION SPRINT

> **Duración:** 2–5 días. Un solo objetivo: ¿existe alpha?

### Diseño del experimento

**Dataset:**
- Símbolo: BTC/USDT (solo uno)
- Timeframe: 1h
- Split: 70% train / 30% test temporal (sin shuffle)
- Walk-forward: 6 splits (~8 meses c/u)

**Normalización obligatoria:**
```
r_t_adj = r_t / rolling_volatility
```
Sin esto, el régimen de mercado sesga todo el experimento.

### 3 framings de target

#### (A) Clasificación de retorno

| Label | Condición |
|-------|-----------|
| BUY | r > threshold |
| SELL | r < -threshold |
| HOLD | resto |

Threshold fijo y threshold normalizado por volatilidad.

#### (B) Binario (trade / no-trade)

| Label | Condición |
|-------|-----------|
| TRADE | \|r\| > threshold |
| NO-TRADE | resto |

Elimina simetría artificial del problema.

#### (C) Regresión

Target: retorno continuo `r_t`.

Métricas: IC, R², directional consistency.

### Modelos mínimos

Sin deep learning. Sin stacking.

| Modelo | Tipo |
|--------|------|
| Logistic Regression | Baseline lineal |
| Random Forest | max_depth ≤ 7 |
| XGBoost | Default, sin tuning agresivo |
| **Ridge Regression con lags** | Temporal-aware sin LSTM |

### Test suite

| Test | Qué detecta |
|------|-------------|
| **Shuffle test** (10 seeds) | Señal > ruido |
| **Always-HOLD baseline** | Modelo supera a predecir siempre HOLD |
| **Permutation stability** (5 walk-forward runs) | Varianza de la señal |

### Output del sprint

Cada experimento genera:

```json
{
  "framing": "A | B | C",
  "model": "LR | RF | XGB | Ridge",
  "metrics": {
    "accuracy": 0.0,
    "f1_macro": 0.0,
    "ic_mean": 0.0,
    "ic_consistency": 0.0,
    "r2": 0.0
  },
  "shuffle_comparison": {
    "real": 0.0,
    "shuffle_mean": 0.0,
    "delta": 0.0
  },
  "decision": "PASS | FAIL"
}
```

### Reglas del sprint

**PROHIBIDO:**
- LSTM, GRU, Transformer, TCN, stacking
- Feature engineering complejo
- FDR / correcciones múltiples
- Más de 3 framings
- Optimización de hiperparámetros agresiva

**PERMITIDO:**
- Modelos simples (LR, RF, XGB, Ridge)
- Shuffle test, walk-forward
- Análisis estadístico de señal

### Criterio final

| Caso | Condición | Decisión |
|------|-----------|----------|
| **A — No alpha** | Real ≈ Shuffle, IC inconsistente, modelos no superan baseline | No hay señal en este feature space. Detener ML aquí |
| **B — Alpha débil** | Real > Shuffle ligeramente, inconsistente entre splits/modelos | Micro-edge no explotable aún. Documentar y explorar nuevos datasources |
| **C — Alpha real** | Real >> Shuffle consistente, IC estable, ≥2 modelos detectan señal | Activar pipeline post-sprint |

---

# SECCIÓN B — POST-SPRINT (solo si hay alpha)

> Las siguientes fases solo se ejecutan si el Sprint (Sección A) confirma **Caso C — Alpha real**.
> Si no hay señal, ir directamente a Sección C.

## 4. FASE 3 — AUDITORÍA DE FEATURES

### Hipótesis

Parte de las 20 features actuales son redundantes o ruidosas. Identificarlas puede mejorar la relación señal/ruido.

### Experimentos

| Técnica | Aplica a |
|---------|----------|
| **SHAP values** | MetaModel (última capa interpretable) |
| **Permutation Importance** | Features individuales |
| **Mutual Information** | Features vs target (cada framing) |
| **Correlación de Pearson** | Entre features (multicolinealidad) |
| **VIF** | Factor de inflación de varianza |

### Ranking esperado

```
Rank  Feature         MI    SHAP   VIF
──────────────────────────────────────
1     atr             0.03   0.02   2.1
2     rsi             0.02   0.01   1.8
...
20    close           0.001  0.00   15.3
```

### 🟡 GATE 2 — Feature utility

**Soft progression si:** Kappa > 0.1 con las mejores features.

Si no → el problema es el target, no las features. Volver a Fase 2.

---

## 5. FASE 4 — CREACIÓN DE NUEVAS FEATURES

### Hipótesis

Agregar features con poder predictivo conocido en finanzas (retornos, lags, volatilidad, regímenes) aumenta la señal disponible.

### Nuevas features por grupo

#### RETORNOS
- `log_return_1`, `log_return_3`, `log_return_6`, `log_return_12`, `log_return_24`

#### LAGS
- `close_lag_1`, `close_lag_3`, `close_lag_6`, `close_lag_12`

#### VOLATILIDAD
- `rolling_std_6`, `rolling_std_12`, `rolling_std_24`

#### Z-SCORES
- `zscore_close`, `zscore_volume`

#### MOMENTUM
- `roc_3`, `roc_6`, `roc_12`

#### REGIME
- `trend_regime` (EMA slope cuantizado)
- `volatility_regime` (ATR percentil)

#### CROSS-SYMBOL (solo si hay datos multi-símbolo)
- BTC dominance
- ETH/BTC ratio
- Correlaciones rolling entre símbolos

### 🟡 GATE 3 — Feature improvement

**Soft progression si:** MI del nuevo feature set > MI del baseline.

Si no → stop de feature engineering.

---

## 6. FASE 5 — BASELINES SIMPLES

### Hipótesis

Modelos simples (árboles, regresión logística) pueden igualar o superar al stacking LSTM+XGB+Meta actual, con menor complejidad y mayor interpretabilidad.

### Prohibido
❌ LSTM, GRU, RNN, Transformer, TCN, o cualquier red profunda
❌ Stacking de más de 2 niveles
❌ MetaModel sobre baselines simples (eso se prueba después)

### Modelos

| Modelo | Librería | Por qué |
|--------|----------|---------|
| Logistic Regression | sklearn | Baseline lineal, interpretable |
| Random Forest | sklearn | Benchmark tree-based estándar |
| LightGBM | lightgbm | Gradient boosting optimizado |
| XGBoost | xgboost | Benchmark boosting |
| CatBoost | catboost | Robust handling de categoricals |

### Métricas de comparación

| Métrica | Por qué es crítica |
|---------|-------------------|
| F1 macro | Promedio no sesgado por clase mayoritaria |
| Kappa | Correlación corregida por azar |
| MCC | Métrica balanceada para clasificación |
| Balanced Accuracy | Accuracy corregida por clase |
| Precision/Recall por clase | Detectar colapso a HOLD |

### Baseline de destrucción

Si ningún modelo supera **Always-HOLD** con significancia estadística (p < 0.05):
→ **Proyecto inválido con este framing.** Cambiar a regresión o abandonar.

### 🔴 GATE 4 — Economic viability

**Hard kill si:** Backtest simulado con costos (0.1% fees + slippage) no supera break-even.

### 🔴 GATE 5 — Multiplicity correction

**Hard kill si:** El mejor resultado no sobrevive corrección FDR.

### 🔴 GATE 6 — Exploitability

**Hard kill si:** EV neto ≤ 0, drawdown > 15%, turnover > 5x/día, o edge inestable.

---

## 7. FASE 6 — VALIDACIÓN DEL ALPHA

### Hipótesis

Si hay señal, debe persistir fuera de muestra bajo protocolos de validación rigurosos.

### Métodos

| Método | Qué valida |
|--------|------------|
| **Shuffle labels** | Señal > ruido (repetir con 10 seeds) |
| **Walk Forward** | Estabilidad temporal (ventanas móviles) |
| **Purged KFold** | Sin data leakage entre train/test |
| **Combinatorial Purged CV** | Robustez ante distintos cortes temporales |

### 🟡 GATE 7 — Stability

**Soft progression si:** Resultado consistente en ≥ 2 símbolos.

Si no → volver a Fase 2 o Fase 4.

---

## 8. FASE 7 — MODELOS AVANZADOS

### Condición de entrada

Solo si:
- F1 macro mejora significativamente vs Fase 5
- Kappa > 0.15
- MCC positivo
- Balanced accuracy superior al baseline
- GATE 6 aprobado (explotabilidad económica)

### Prohibido
❌ Agregar profundidad por intuición
❌ Más capas LSTM sin experimento controlado
❌ Stacking más complejo que 2 niveles

### Modelos permitidos

| Modelo | Cuándo probarlo |
|--------|-----------------|
| GRU | Si hay dependencias temporales largas |
| TCN | Si hay patrones multi-escala |
| Transformer temporal | Si hay atención relevantes en el pasado |
| TFT | Si se necesita interpretabilidad |
| Ensemble ligero (2-3 modelos) | Si modelos individuales tienen errores no correlacionados |

---

## 9. FASE 8 — LOSS FUNCTIONS

### Condición de entrada

Solo si existe alpha validado (Fase 6 aprobada).

### Experimentos

- Class weights (inversamente proporcionales a frecuencia)
- Focal Loss (reduce peso de muestras fáciles)
- Weighted Cross Entropy
- Threshold tuning post-hoc
- Probability calibration (Platt, Isotonic)
- Temperature scaling

---

## 10. FASE 9 — BACKTEST REALISTA

### Condición de entrada

Modelo con alpha validado + loss function óptima.

### Componentes obligatorios

| Componente | Por qué |
|------------|---------|
| Fees (0.1% por lado) | Realismo de costos |
| Slippage (0.05% estimado) | Impacto de mercado |
| Spread bid-ask | Fricción real |
| Latencia (1-5s) | Retardo de ejecución |
| Stop Loss (ATR × 1.5) | Protección |
| Take Profit (ATR × 2-3) | Captura de beneficio |
| Position sizing (1% riesgo) | Gestión de capital |

### Métricas

| Métrica | Objetivo |
|---------|----------|
| Sharpe (con costos) | > 1.0 |
| Sortino | > 1.5 |
| Calmar | > 1.0 |
| Max Drawdown | < 15% |
| Win rate | > 40% |
| Profit Factor | > 1.5 |
| MCC direccional | > 0.2 |

### Prohibido
❌ Sharpe inflado por profit fijo sin costos
❌ Ignorar drawdown porque "el modelo es bueno"
❌ Optimizar parámetros en backtest sin validación fuera de muestra

---

## 11. FASE 10 — BTC PRIMERO

### Estrategia

1. Optimizar exclusivamente **BTC/USDT** hasta que cumpla criterio de éxito global
2. Congelar configuración de BTC
3. Replicar a **ETH, SOL, DOGE, AVAX, XRP**
4. Si un símbolo no mantiene performance → documentar y excluir
5. Si ≥ 3 símbolos fallan → el sistema no generaliza. Revisar hipótesis.

---

## 12. FASE X — AUDITORÍA TEMPORAL

### Hipótesis

El horizonte temporal actual (1h, lookback=20) puede no ser óptimo. La señal puede existir en otros timeframes o combinaciones.

### Experimentos

#### A) Variar timeframes

Construir datasets equivalentes en: 15m, 30m, 1h, 4h, 1d

Comparar: MI, F1 Macro, Kappa, MCC, Balanced Accuracy

#### B) Variar lookback

Probar en horas: 20 (actual), 48, 72, 96, 168

#### C) Multi-timeframe features

Incorporar simultáneamente:
- Corto plazo (1h): RSI, ATR, EMA
- Medio plazo (4h): ADX, EMA50, BB Width
- Largo plazo (1d): tendencia, volatilidad, régimen

#### D) Regímenes de mercado

Separar y evaluar señal en:
- Bull markets vs Bear markets
- Alta volatilidad vs Baja volatilidad

#### E) Más histórico (solo BTC)

Comparar: 4 años vs 6 años vs 8 años vs 10 años
Medir: estabilidad de features, MI, performance OOS

### Prohibido
❌ Asumir que más datos implican mejor modelo
❌ Descargar más histórico sin experimento controlado

---

## PHASE 2 — AUTOMATION LAYER (diseño)

> **Estado:** 📐 Diseño únicamente. No implementar hasta tener alpha validado.

### Visión

Convertir el pipeline manual en un **Quant Research Operating System** con:

- Generación automática de hipótesis
- Testing paralelo de configuraciones
- Scoring y ranking de estrategias
- Evaluación por gates automatizada
- Logging de experimentos tipo laboratorio
- Control de drift de señal en producción

### Componentes futuros

```
Hypothesis Generator
  │
  ├── Config Runner
  │     ├── Parallel execution
  │     └── Resource allocation
  │
  ├── Metrics Collector
  │     ├── Gate evaluation
  │     └── FDR correction
  │
  ├── Strategy Scorer
  │     ├── Alpha ranking
  │     └── Exploitability check
  │
  └── Model Registry
        ├── Versioning
        └── Promotion pipeline
```

### Regla

No se construye hasta que exista al menos un alpha validado económicamente.

---

# SECCIÓN C — SI NO HAY ALPHA

> Esta sección se activa si el Sprint concluye **Caso A (No alpha)** o **Caso B (Alpha débil)**.

### Diagnóstico

Si no hay señal en OHLCV + TA en 1h, esto significa:

- **Este feature space** no contiene alpha explotable
- No es problema de modelo, es problema de datos
- No hay cantidad de LSTM, stacking, o gates que lo resuelvan

### Caminos alternativos

Para encontrar alpha se necesitan nuevos datasources:

| Fuente | Qué aporta |
|--------|------------|
| **Order flow** | Desequilibrio de órdenes, delta cumulative, agresividad |
| **Funding rates** | Costo de financiación perpetuos,sentimiento direccional |
| **Open Interest** | Apalancamiento agregado, posiciones abiertas |
| **On-chain** | Flujo de exchange, whales, actividad de red |
| **Cross-exchange spreads** | Diferencias de precio entre exchanges y pares |
| **Multi-timeframe alignment** | Convergencia/divergencia entre 15m/1h/4h/1d |

### Regla

No continuar con ML en este dataset sin al menos uno de los nuevos datasources arriba.

---

## ANEXO: ARCHIVOS Y UBICACIONES

| Recurso | Ubicación |
|---------|-----------|
| Roadmap | `roadmaps/ROADMAP_QUANT_V2.md` |
| Configs de experimentos | `roadmaps/configs/*.yaml` |
| Checkpoints | `apps/analytics-engine/checkpoints/` |
| Predicciones | `apps/analytics-engine/data/predictions/` |
| Reports de validación | `apps/analytics-engine/reports/validation/` |
| Código de experimentos | `apps/analytics-engine/experiments/` |

---

*Este documento es vivo. Se actualiza al finalizar cada fase con resultados, decisiones y nuevas hipótesis.*
