# ROADMAP QUANTITATIVO V2

> **Proyecto:** ARGOS — Pipeline de predicción cuantitativa para criptomonedas
> **Versión roadmap:** 2.0
> **Estado:** ⏳ En definición — Ver nota abajo
> **Principio rector:** Encontrar alpha reproducible. No "cumplir roadmap".

> ⚠️ **NOTA DE ESTADO (2026-06-16):**
> Las secciones que referencian FASE 5.5, 6.5, 6.75, Lock Test y sus scripts
> (`experiments/fase65_*.py`, etc.) fueron **diseñadas conceptualmente pero
> nunca implementadas ni committeadas**. El `experiments/` directory no existe
> en el repositorio actual.
>
> La validación cuantitativa se ha reiniciado desde cero en
> `experiments/quant_validation_v1/` con un pipeline de falsación fresco.
> Ver `experiments/quant_validation_v1/ROADMAP.md` para el estado actual.

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

## 6.5. FASE 5.5 — STRATEGY LAYER

> **Estado:** ⏳ En ejecución
> **Propósito:** Determinar si el alpha estadístico (CASO_C) puede convertirse en una estrategia económicamente explotable.

### Hipótesis

El problema no está en el modelo (RandomForest, AUC=0.844) sino en la capa de ejecución y gestión del riesgo. El drawdown del 71% en backtest no es un problema de señal sino de ausencia de stops, sizing y filtros.

### 🔒 HARD RULE

**Prohibido durante FASE 5.5:**
- Modificar hiperparámetros de RandomForest
- Probar XGBoost, LightGBM, CatBoost
- GRU, LSTM, Transformer, TCN, TFT, stacking
- Cualquier cambio al modelo predictivo

La única variable que cambia es la **capa de estrategia**.

### Subfase 5.5.1 — Feature Selection

**Objetivo:** Reducir 68 → ~20-30 features útiles.

| Filtro | Acción |
|--------|--------|
| **MI ranking** | Eliminar features con MI ≈ 0 |
| **Correlation filter** | Si \|r\| > 0.85, conservar la de mayor MI+SHAP. Nunca eliminar solo por correlación |
| **VIF** | Eliminar VIF > 10 |
| **RF Importance** | Ranking desde RandomForest entrenado |
| **SHAP** | TreeExplainer sobre RF (top 20 features) |
| **RFE** | RFE(LogisticRegression, n_features=25) |

**Output:** `reports/feature_selection/` con ranking completo, features retenidas/eliminadas, justificación.

**Happy path:** 68 → ~25 features, AUC ≥ 0.84.
**Sad path:** si AUC cae > 0.02, revertir al conjunto completo y documentar.

### Subfase 5.5.2 — Backtest Realista

**Objetivo:** Reemplazar backtest simplificado con uno con SL/TP real.

| Componente | Valor |
|-----------|-------|
| **SL** | {1, 1.5, 2} × ATR |
| **TP** | {2, 3, 4} × ATR (RR=2:1) |
| **Position sizing** | {0.25%, 0.5%, 1%} del balance |
| **Fees** | 0.1% por lado |
| **Slippage** | 0.05% |
| **Spread** | 0.01% |
| **Latencia** | Ejecución en apertura de vela siguiente |
| **SL/TP check** | Por cada vela, checkear si se tocaron SL/TP antes de evaluar nueva señal |

**Output:** `reports/backtest/sl_tp_results.json`, `reports/backtest/trade_distribution.json`

### Subfase 5.5.3 — Trade Filters

**Objetivo:** Reducir sobreoperación (1111 → ? trades).

Búsqueda jerárquica de 2 etapas:

#### Etapa A — 20 combinaciones

Probar todo el grid de:

| Filter | Valores |
|--------|---------|
| Min probability | [0.425, 0.50, 0.55, 0.60, 0.65] |
| ADX threshold | [0 (none), 20, 25, 30] |

#### Etapa B — Top 5 configuraciones de Etapa A

Sobre cada una, probar:

| Filter | Valores |
|--------|---------|
| HTF alignment | [none, 4h_trend, 1d_trend] |
| Vol regime | [all, low_only, high_only] |
| Max trades/day | [1, 2, 3, inf] |

**Output:** `reports/backtest/filter_grid_results.json` (ranking de configuraciones, top 10)

### Subfase 5.5.3.5 — Monte Carlo Stress Test

**Objetivo:** Validar que la estrategia no es producto del azar en la secuencia de trades.

**Método:**
1. Tomar la secuencia real de trades (PnL, duración)
2. Generar 1000 permutaciones aleatorias del orden de trades
3. En cada permutación, recalcular equity curve, CAGR, max DD

**Métricas:**
- Distribución de CAGR (mean, std, 5% percentile)
- Distribución de max DD (mean, std, 95% percentile)
- Probabilidad de ruina (DD > 99%)
- Peor DD al 95% de confianza

**Output:** `reports/backtest/monte_carlo_results.json`

### Subfase 5.5.4 — GATE 4 & 6 Re-run

**Objetivo:** Evaluar si la mejor configuración de 5.5.1-3 pasa los gates.

#### GATE 4 — Economic viability

| Métrica | Objetivo |
|---------|----------|
| Sharpe (con costos) | > 1.0 |
| Profit Factor | > 1.5 |
| Retorno neto | > 0 (positivo) |

#### GATE 6 — Exploitability

| Métrica | Objetivo |
|---------|----------|
| EV neto | > 0 |
| Max Drawdown | < 15% |
| Turnover | < 5 trades/día |
| **Calmar ratio** | **> 1.0** |

#### Output

`reports/backtest/gate_results.json` con PASS/FAIL y todas las métricas.

### Criterio final

Si GATE 6 sigue fallando después de FASE 5.5:
- **NO** asumir que hacen falta redes más complejas
- Documentar si la señal estadística es monetizable
- Identificar qué componente produce el drawdown (entrada, salida o gestión de riesgo)
- Solo si la estrategia demuestra explotabilidad económica se autoriza FASE 7

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

## 8. FASE 6.5 — OUT-OF-SAMPLE VALIDATION LAYER

> **Estado:** ✅ Completada — GATE 6.5: PASS CONDICIONAL (4/5 condiciones)
> **Propósito:** Verificar que el alpha descubierto en FASE 5.5 es real y no producto de overfitting, selection bias, data snooping, leakage temporal, o sobreoptimización del grid search.

### Resultado

Walk-forward: 4/4 folds PASS (Sharpe>1, Calmar>1, PF>1.5, EV>0)
Monte Carlo: 4/4 folds PASS (ruin=0%)
Stress Test: PASS (Calmar=2.08 con 2× friction)
Benchmark vs B&H: PASS (3/4 folds)
White RC: ⚠️ No significativo (p=0.40) — pero alpha es ROBUSTO (40% de configs aleatorias también funcionan)

**GATE 6.5: 🟡 PASS CONDICIONAL → FASE 6.75 autorizada**

### Configuración congelada

Usar exactamente la mejor configuración encontrada en FASE 5.5. Sin reoptimización entre folds:

| Parámetro | Valor |
|-----------|-------|
| threshold | 0.6 |
| ADX | 0 (desactivado) |
| SL | 2 ATR |
| TP | 4 ATR |
| risk_pct | 1% |
| Feature subset | 41 features retenidas (FASE 5.5.1) |

### 8.1 — Script 1: Walk-Forward Validation

`experiments/fase65_walkforward.py`

4 folds expanding window con configuración congelada:

| Fold | Train | Test |
|------|-------|------|
| 0 | 2019–2021 (sin últimas 5 barras) | 2022 |
| 1 | 2020–2022 (sin últimas 5 barras) | 2023 |
| 2 | 2021–2023 (sin últimas 5 barras) | 2024 |
| 3 | 2022–2024 (sin últimas 5 barras) | 2025 |

**Protección contra leakage:**
- Features pre-computadas (backward-looking rolling windows, seguras)
- Labels: últimas 5 barras del train excluidas (evita que `shift(-5)` cruce al test)
- Scaler fit en train, transform en test

Por fold: train RF → predict test → BacktestEngine con config congelada.

**Output:** `reports/walkforward/fold_{0,1,2,3}.json`, `summary.json`

### 8.2 — Script 2: Monte Carlo por Fold

`experiments/fase65_montecarlo_per_fold.py`

Usar trades reales de cada fold. 1000 permutaciones independientes.

Métricas por fold: CAGR mean, DD mean, DD p95, ruin probability.

**Output:** `reports/montecarlo_fold/fold_{0,1,2,3}.json`

### 8.3 — Script 3: Stress Test

`experiments/fase65_stress_test.py`

Degradar costos al 2×:

| Costo | Normal | Stress |
|-------|--------|--------|
| Fees | 0.1% | 0.2% |
| Slippage | 0.05% | 0.1% |
| Spread | 0.01% | 0.02% |

PASS si Calmar > 1 sobrevive.

**Output:** `reports/stress_test/stress_test.json`

### 8.4 — Script 4: Benchmark vs Buy & Hold

`experiments/fase65_benchmark.py`

Comparar cada fold contra B&H en CAGR, Sharpe, Sortino, Calmar, Max DD.

**Output:** `reports/benchmark/fold_{0,1,2,3}.json`

### 8.5 — Script 5: White's Reality Check

`experiments/fase65_white_reality_check.py`

Distribución nula por bootstrap de todas las configs evaluadas en FASE 5.5. p-value ajustado.

**Output:** `reports/benchmark/white_reality_check.json`

### 8.6 — Script 6: GATE 6.5

`experiments/fase65_gate.py`

**Condiciones obligatorias para autorizar FASE 7:**

| Condición | Threshold | Cumplir en |
|-----------|-----------|------------|
| Sharpe | > 1.0 | ≥3 folds |
| Calmar | > 1.0 | ≥3 folds |
| Profit Factor | > 1.5 | ≥3 folds |
| Expectancy | > 0 | ≥3 folds |
| MC ruin probability | < 5% | ALL folds |
| Stress Test Calmar | > 1.0 | PASS |
| Supera B&H risk-adjusted | Sharpe/Calmar | ≥3 folds |
| White RC p-value | < 0.05 | PASS |

**Resultado:** 4/5 condiciones PASS. White RC no significativo (p=0.40) pero alpha robusto.

---

## 9. FASE 6.75 — ADVANCED STATISTICAL VALIDATION

> **Estado:** ✅ Completada — GATE 6.75: PASS DÉBIL (6/8 fuerte, 8/8 débil)
> **Propósito:** Antes de autorizar modelos profundos, destruir el alpha con pruebas más exigentes. El RandomForest champion debe sobrevivir.

### Hard Rule

- **FASE 7 bloqueada hasta completar FASE 6.75.**
- Todos los modelos futuros deben vencer al champion (GATE 7 — Complexity Premium).
- El burden of proof pertenece a los modelos complejos.

### Champion (nuevo)

El RandomForest con 41 features, threshold=0.6, SL=2 ATR, TP=4 ATR, risk=1% pasa a ser el campeón. Ningún modelo avanzado lo reemplaza sin demostrar superioridad.

### 9.1 — Script 1: Purged K-Fold CV

`experiments/fase675_purged_cv.py`

K=6 folds expanding-window purged CV con embargo=5 barras. Train siempre antes que test, purga leakage de labels por `shift(-5)`.

| Fold | Train end | Test | Sharpe | Calmar |
|------|-----------|------|--------|--------|
| 0 | 2022-07 | 2022-08→2023-03 | 3.61 | 4.64 |
| 1 | 2023-03 | 2023-04→2023-10 | 6.26 | 40.05 |
| 2 | 2023-10 | 2023-11→2024-05 | 5.28 | 45.13 |
| 3 | 2024-05 | 2024-06→2024-12 | 8.96 | 128.53 |
| 4 | 2024-12 | 2025-01→2025-07 | 7.31 | 85.99 |
| 5 | 2025-07 | 2025-08→2026-06 | 8.25 | 109.56 |

Resultado: Sharpe medio 6.61 ± 1.81, Calmar medio 68.98 ± 42.87. 6/6 folds > thresholds.

**Output:** `reports/fase675/purged_cv.json`

### 9.2 — Script 2: Combinatorial Purged CV

`experiments/fase675_combinatorial_cv.py`

N=8 grupos, K=2 test, C(8,2)=28 combinaciones. Media, desviación, estabilidad (CV).

| Métrica | Media | Std | CV |
|---------|-------|-----|----|
| Sharpe | 2.94 | 2.28 | 0.78 |
| Calmar | 15.76 | 18.87 | 1.20 |

CV(Sharpe)=0.78 → estable pero con dependencia de régimen.

**Output:** `reports/fase675/combinatorial_cv.json`

### 9.3 — Script 3: Deflated Sharpe Ratio

`experiments/fase675_deflated_sharpe.py`

Corrige Sharpe por múltiples pruebas y no-normalidad de retornos.

| M (trials) | DSR |
|-----------|------|
| 20 | 1.0000 |
| 60 | 1.0000 |
| 80 | 1.0000 |
| 200 | 1.0000 |

**DSR > 0 → PASS.** Incluso con M=200, el alpha no se explica por selección múltiple.

**Output:** `reports/fase675/deflated_sharpe.json`

### 9.4 — Script 4: Probability of Backtest Overfitting

`experiments/fase675_pbo.py`

M=60 configs (28 reales de FASE 5.5 + 32 sintéticas vecinas) evaluadas en 21 splits CPCV viables.

PBO = 0.333 → **DUDOSO (0.2 ≤ PBO < 0.4).**
7/21 splits donde la mejor config IS está bajo la mediana en OOS.
El sobreajuste está en los parámetros SL/TP/threshold, no en el modelo.

**Output:** `reports/fase675/pbo.json`

### 9.5 — Script 5: Superior Predictive Ability Test

`experiments/fase675_spa_test.py`

(Hansen SPA) Champion vs B&H + random baseline + 30 configs alternativas. 21 splits CPCV, stationary bootstrap 1000 muestras.

| Benchmark | Mean Sharpe |
|-----------|-------------|
| Champion RF | **3.50** |
| B&H | 1.18 |
| Alternativas | 1.29 |
| Random | -13.49 |

SPA p-value = **0.0000** → Champion significativamente superior a todos los benchmarks.

**Output:** `reports/fase675/spa_test.json`

### 9.6 — Script 6: GATE 6.75

`experiments/fase675_gate.py`

| Condición | Fuerte | Débil | Resultado |
|-----------|--------|-------|-----------|
| Purged CV Sharpe | >1.5 en ≥4/6 | >1.0 en ≥4/6 | **✓ 6/6** |
| Purged CV Calmar | >2.0 en ≥4/6 | >1.0 en ≥4/6 | **✓ 6/6** |
| CPCV CV(SR) | <0.5 | <1.0 | **~ 0.78** |
| DSR | >0 | >0 | **✓ 1.000** |
| PBO | <0.2 | <0.4 | **~ 0.333** |
| SPA | p<0.05 | p<0.10 | **✓ p=0.000** |
| Profit Factor | >1.5 | >1.3 | **✓ inf** |
| MC Ruin | <1% | <5% | **✓ 0%** |

**🟡 GATE 6.75: PASS DÉBIL** (6/8 fuerte, 8/8 débil)

FASE 7 autorizada condicionalmente. RandomForest se mantiene como champion. Complexity Premium obligatorio.

**Output:** `reports/fase675/gate_675.json`

---

## 10. FASE 7 — MODELOS AVANZADOS

### Condición de entrada

Solo si:
- FASE 6.5 completa con PASS CONDICIONAL ✅
- FASE 6.75 completa con PASS DÉBIL ✅
- GATE 6.75 aprobado
- Alpha validado por 9+ pruebas estadísticas independientes

### 🟢 GATE 7 — Complexity Premium

> **Regla:** El RandomForest champion NO es reemplazable automáticamente. Los modelos avanzados son challengers.

#### Orden de challengers

1. TCN (más simple, mejor punto de partida)
2. GRU simple
3. TFT (si se necesita interpretabilidad)
4. Transformer Encoder
5. LSTM

#### Requisitos para aceptar un challenger

Debe superar al champion **en todas**:

| Métrica | Premium requerido |
|---------|------------------|
| Calmar | ≥ +10% sobre champion |
| Profit Factor | ≥ +10% sobre champion |
| Expectancy | ≥ +10% sobre champion |
| Max DD | ≤ champion |
| Monte Carlo ruin | < 5% |
| Stress 2× Calmar | > 1.0 |
| White RC p-value | < 0.05 |
| Purged CV Sharpe | > 1.0 en ≥4/6 folds |
| CPCV CV(SR) | < 0.5 |

#### Regla de parada

Si **TCN y GRU** no logran superar al champion consistentemente:
- **Detener exploración de deep learning.**
- Redirigir esfuerzo a: portfolio construction, position sizing, ejecución.
- Documentar: "No se encontró Complexity Premium en este feature space."

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

## 11. FASE 8 — LOSS FUNCTIONS

### Condición de entrada

Solo si existe alpha validado (FASE 7 aprobada).

### Experimentos

- Class weights (inversamente proporcionales a frecuencia)
- Focal Loss (reduce peso de muestras fáciles)
- Weighted Cross Entropy
- Threshold tuning post-hoc
- Probability calibration (Platt, Isotonic)
- Temperature scaling

---

## 12. FASE 9 — BACKTEST REALISTA

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

## 13. FASE 10 — BTC PRIMERO

### Estrategia

1. Optimizar exclusivamente **BTC/USDT** hasta que cumpla criterio de éxito global
2. Congelar configuración de BTC
3. Replicar a **ETH, SOL, DOGE, AVAX, XRP**
4. Si un símbolo no mantiene performance → documentar y excluir
5. Si ≥ 3 símbolos fallan → el sistema no generaliza. Revisar hipótesis.

---

## 14. FASE X — AUDITORÍA TEMPORAL

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
