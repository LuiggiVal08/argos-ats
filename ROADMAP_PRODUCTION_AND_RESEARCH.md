# ARGOS — Roadmap de Producción e Investigación

> **Versión**: 1.0  
> **Estado**: Transición de Investigación → Ingeniería de Producción  
> **Este documento reemplaza cualquier roadmap anterior** relacionado con Transformers, GRU, LSTM o nuevas features como prioridad inmediata.  
> **Propósito**: Ser la referencia principal del proyecto para los próximos meses.

---

## SECCIÓN 1 — ESTADO ACTUAL DEL PROYECTO

### 1.1 ¿Dónde estamos?

QV2 (Quant Validation v2) respondió la pregunta científica fundamental:

**"¿Existe alpha direccional en BTC, ETH y SOL?"**

La respuesta es **SÍ**. La evidencia es sólida, múltiple y ha sido falsificada desde varios ángulos.

### 1.2 Estado por fase

| Fase | Descripción | BTC | ETH | SOL | Estado |
|------|-------------|-----|-----|-----|--------|
| 3.5 | Non-overlap labels | ✅ | ✅ | ✅ | Completado |
| 3.75 | Cross-market (ETH/SOL features) | — | ✅ | ✅ | Completado |
| 3.8 | Cross-exchange (Bybit/OKX) | ✅ | ✅ | ✅ | Completado |
| 4 | Cross-regime invariance | ✅ | ✅ | ✅ | Completado |
| 5 | Portfolio validation (CV F1) | 0.72 | 0.72 | 0.73 | Completado |
| 6 | Probability calibration | ✅ | ✅ | ✅ | Completado |
| 7 | Economic alpha (costs 0.31% RT) | ✅ | ✅ | ✅ | Completado |
| 8 | Capacity & friction stress | ✅ | ✅ | ✅ | Completado |
| 9 | Paper trading simulation | ✅ | — | — | Completado |
| 10 | Anti-leakage audit | ❌ | ❌ | ❌ | **Script escrito, no ejecutado** |
| 11 | Event-driven backtester (7007 trades) | ✅ | ✅ | ✅ | Completado |
| 12 | Position sizing (3 risk variants) | ✅ | ✅ | ✅ | Completado |
| 13 | Monte Carlo (1000 permutations) | ✅ | ✅ | ✅ | Completado |
| 14 | Lock test (strict chronological OOS) | ✅ 75.3% | ⚠️ 53.3% | ❌ 50.3% | Completado |

### 1.3 El quiebre: Investigación → Ingeniería de Producción

Hasta ahora, el proyecto operó como un laboratorio de investigación:

```
formular hipótesis → experimentar → medir → documentar → repetir
```

Esa etapa está completa. La pregunta principal del proyecto **ya no es** "¿existe alpha?". Es:

> **"¿Cómo convertir este alpha en un sistema robusto que pueda operar durante años?"**

El cuello de botella dejó de ser la investigación. Pasó a ser la **ingeniería de producción**.

### 1.4 Implicaciones del cambio

| Dimensión | Antes (investigación) | Ahora (producción) |
|-----------|----------------------|--------------------|
| Pregunta | ¿Hay alpha? | ¿El sistema es estable? |
| Prioridad | Descubrir | Construir |
| Éxito | F1, Sharpe | Tiempo vivo sin fallos |
| Ciclo | Semanal (experimentos) | Mensual (monitoreo) |
| Riesgo | Perder tiempo | Perder capital |
| Artefacto | Reporte JSON | Sistema funcionando |

### 1.5 Evidencia acumulada (resumen ejecutivo)

- **53 features** (TA 20 + MTF 4h/1d 30 + Funding 3)
- **Modelo**: LogisticRegression (C=0.1, class_weight=balanced, solver=liblinear)
- **Thresholds**: 0.60 BUY / 0.40 SELL
- **Timeframe**: 1h, hold 5 barras, stride 5
- **Costos**: 0.31% round trip (fee 0.10% + slippage 0.05% + spread/2 0.005% por lado)
- **BTC**: F1 0.72 CV | 75.3% directional accuracy lock test | Sharpe 4.60 backtest (7007 trades)
- **ETH**: F1 0.72 CV | 53.3% lock test (no generaliza desde BTC)
- **SOL**: F1 0.73 CV | 50.3% lock test (no generaliza desde BTC)

---

## SECCIÓN 2 — PHASE 10: ANTI-LEAKAGE AUDIT (Último paso de validación)

### 2.1 Propósito

Phase 10 **no agrega alpha. No mejora métricas. No cambia el modelo.**

Su único objetivo es cerrar formalmente el bloque de validación documentando que no existe leakage en el pipeline.

Es el equivalente a una auditoría de seguridad antes de poner un sistema en producción.

### 2.2 Estado actual

El script `experiments/quant_validation_v2_phase10/run.py` **está escrito pero nunca se ejecutó**. No existe ningún output en `reports/quant_validation_v2_phase10/`.

### 2.3 Componentes de la auditoría

#### 10.1 Feature Audit

Para cada una de las 53 features, documentar:

- **Nombre**: identificador único
- **Origen**: OHLCV raw / TA library / MTF resample / funding rate
- **Ventana**: 9, 14, 20, 21, 50 (dependiendo del indicador)
- **Shift**: 0 (rolling) o lookahead (ninguno debe tener shift negativo)
- **Primera fila válida**: índice en el dataset donde la feature deja de ser NaN
- **Resultado esperado**: ninguna feature usa datos futuros

#### 10.2 MTF Alignment

Garantizar que para toda barra con timestamp `t_pred`:

```
max(timestamp_feature_mtf) <= t_pred
```

El MTF (Multi-Timeframe) resamplea a 4h y 1d y luego hace `reindex(method="ffill")`. Esto puede propagar el close de un bucket futuro si no se alinea correctamente. El audit debe verificar fila por fila que ningún valor MTF proviene de un timestamp posterior al de la predicción.

#### 10.3 Funding Alignment

Garantizar que para toda barra con timestamp `t_bar`:

```
funding_timestamp <= t_bar
```

El funding rate se alinea con `reindex(method="ffill")`. Verificar que ningún funding rate con timestamp posterior a la barra se use como feature.

#### 10.4 Target Audit

Confirmar que la variable objetivo se calcula exclusivamente como:

```
forward_return = close[t + 5] / close[t] - 1
```

Sin atajos, sin vol-adjustment en la definición del target para el backtest económico (aunque el label de entrenamiento use vol-adj return, el forward_return del backtest debe ser precio puro).

#### 10.5 Walk Forward Audit

Para cada fold de la validación cruzada de Phase 5, confirmar:

```
max(train_idx) + embargo < min(test_idx)
```

Donde `embargo >= 1` (se eliminó 1 barra entre train y test para evitar fuga temporal).

### 2.4 Formato del reporte

La auditoría produce un JSON por cada sub-sección (10.1–10.5) más un resumen global con estado:

- **PASS**: sin leakage
- **WARNING**: leakage potencial documentado con bajo impacto
- **HARD FAIL**: leakage confirmado que invalida resultados

### 2.5 Criterio de aceptación

Si Phase 10 produce **PASS** o **WARNING** (con impacto documentado y despreciable), el bloque de validación se cierra definitivamente y el proyecto avanza a Fase A.

Si produce **HARD FAIL**, se detiene todo hasta corregir la fuente de leakage y re-ejecutar QV2 completo.

---

## SECCIÓN 3 — FASE A REAL: MODELOS DE PRODUCCIÓN

### 3.1 El problema actual

Todos los modelos entrenados en QV2 **viven exclusivamente en RAM**. El flujo actual es:

```
cargar datos → entrenar LR → evaluar → destruir modelo (fin del script)
```

Cada vez que se ejecuta un experimento, se entrena un modelo nuevo. No hay ningún `.pkl` guardado en disco.

Esto impide:

- **Inferencia**: no hay un modelo que cargar para predecir
- **Forward testing**: no hay artefacto congelado que evaluar en vivo
- **Producción**: no hay modelo que sirva un endpoint
- **Paper trading continuo**: no hay modelo estable contra el cual medir desviaciones
- **Reentrenamiento controlado**: no hay baseline (modelo anterior) contra el cual comparar

### 3.2 Arquitectura objetivo

```
models/
├── btc/
│   ├── model.pkl          # LogisticRegression (C=0.1, balanced, liblinear)
│   ├── scaler.pkl         # RobustScaler (fitted)
│   └── metadata.json      # feature_names, training_date, cv_metrics
├── eth/
│   ├── model.pkl
│   ├── scaler.pkl
│   └── metadata.json
└── sol/
    ├── model.pkl
    ├── scaler.pkl
    └── metadata.json
```

### 3.3 train_production_models.py

**Archivo**: `scripts/train_production_models.py` (o en la raíz)

**Objetivo**: Entrenar y serializar los 3 modelos oficiales de producción.

**Parámetros** (congelados de QV2):

| Parámetro | Valor |
|-----------|-------|
| Modelo | LogisticRegression |
| C | 0.1 |
| class_weight | balanced |
| solver | liblinear |
| max_iter | 5000 |
| random_state | 42 |
| Features | 53 (20 TA + 30 MTF + 3 Funding) |
| Timeframe | 1h |
| Lookahead | 5 |
| Scaler | RobustScaler |
| Thresholds | 0.60 BUY / 0.40 SELL |

**Pipeline**:

1. Cargar OHLCV del símbolo (`apps/analytics-engine/data/{symbol}_usdt_1h.parquet`)
2. Cargar funding rates (`apps/analytics-engine/data/{symbol}_funding_rates.parquet`)
3. Computar feature matrix (53 features, igual que Phase 5)
4. Generar labels binarios (vol-adj return, threshold 0.5)
5. Entrenar RobustScaler + LogisticRegression
6. Guardar en `models/{symbol}/model.pkl` + `scaler.pkl` + `metadata.json`
7. Reportar métricas de entrenamiento (accuracy, F1, feature count)

**Output**: 3 directorios (`btc/`, `eth/`, `sol/`) cada uno con `model.pkl`, `scaler.pkl`, `metadata.json`.

### 3.4 Versionado de modelos

Cada `metadata.json` incluye:

```json
{
  "symbol": "BTC",
  "model_version": "1.0.0",
  "training_date": "2026-06-18",
  "training_data_range": ["2022-01-01", "2025-06-01"],
  "features": 53,
  "cv_metrics": {
    "f1": 0.72,
    "accuracy": 0.76
  },
  "backtest_metrics": {
    "n_trades": 7007,
    "sharpe": 4.60,
    "max_drawdown": -0.71,
    "win_rate": 0.78
  },
  "lock_test_results": {
    "directional_accuracy": 0.753,
    "period": "2025-06-01 to 2026-06-16"
  },
  "parameters": {
    "C": 0.1,
    "class_weight": "balanced",
    "solver": "liblinear"
  }
}
```

Esto permite trazabilidad completa: cada modelo desplegado tiene su historial de validación asociado.

---

## SECCIÓN 4 — FASE B: FORWARD TEST

### 4.1 Objetivo

Validar que el modelo entrenado se comporta en datos vivos como lo hizo en backtest.

**Esta es la etapa más valiosa del proyecto en este momento.** Más valiosa que cualquier experimento nuevo.

### 4.2 Pipeline de forward test

```
cargar model.pkl + scaler.pkl (desde models/{symbol}/)

por cada nueva barra 1h:

    1. obtener OHLCV actualizado
    2. calcular 53 features
    3. scaler.transform(X)
    4. model.predict_proba(X_scaled)[0, 1] → y_proba
    5. aplicar thresholds:
       - y_proba > 0.60 → BUY
       - y_proba < 0.40 → SELL
       - else → HOLD
    6. registrar {timestamp, y_proba, signal} en log
    7. si hay posición abierta hace 5 barras → cerrar
    8. si hay flip (señal opuesta) → cerrar + abrir
    9. calcular equity (mark-to-market)
    10. loggear heartbeat cada 30s
```

### 4.3 Métricas a monitorear

| Métrica | Frecuencia | Umbral de alerta |
|---------|-----------|------------------|
| Estabilidad de y_proba | Por barra | Desviación > 2σ vs distribución de training |
| Drawdown | Diario | > 5% → halt |
| Latencia de inferencia | Por barra | > 500ms → warning |
| Degradación de accuracy | Semanal | > 5pp vs backtest |
| Tasa de señales (BUY/SELL) | Diario | < 30% del promedio histórico |
| Desviación vs backtest | Mensual | PnL fuera de ±20% del esperado |
| Costos reales vs modelados | Por trade | Diferencia > 0.05% → revisar modelo de costos |

### 4.4 Duración

**1 a 3 meses** de operación continua.

No se toma ninguna decisión sobre capital real hasta completar este período.

### 4.5 Criterio de éxito

El forward test se considera exitoso si después de 1 mes:

- Sin errores de infraestructura (reconexiones, data gaps, crashes)
- Drawdown máximo < 10%
- Latencia P99 < 200ms por barra
- Distribución de y_proba consistente con training
- Sistema operando sin intervención manual

### 4.6 Riesgos

| Riesgo | Probabilidad | Mitigación |
|--------|-------------|------------|
| Data drift (cambio de distribución) | Alta | Monitoreo de KS test en y_proba |
| Conexión exchange caída | Media | Reconexión automática, buffer de datos |
| Error de feature computation | Baja | Validación contra pipeline de entrenamiento |
| Slippage real > modelado | Media | Monitorear diferencia costo real vs modelado |
| Latencia de broker (Redis) | Baja | Heartbeat cada 30s |

---

## SECCIÓN 5 — FASE C: CAPITAL REAL

### 5.1 Progresión

```
Fase B (forward test 1-3 meses)
    ↓
$100 USD (1 mes mínimo)
    ↓
$500 USD (1 mes mínimo)
    ↓
$1,000 USD (1 mes mínimo)
    ↓
Escalado posterior
```

### 5.2 Reglas

- **Sin apalancamiento** en ninguna etapa
- Cada nivel se mantiene al menos 1 mes sin drawdown > 5%
- El paso al siguiente nivel requiere: mes completo sin intervención manual, drawdown máximo < 5%, y revisión de todas las métricas de la Fase B
- Si en cualquier nivel hay un drawdown > 10%, se retrocede al nivel anterior
- Si hay dos retrocesos consecutivos, se vuelve a Fase B (forward test sin capital)

### 5.3 Prioridad

**Supervivencia > Crecimiento.**

No importa cuánto tarde escalar. Lo que importa es que el sistema no se destruya en el intento.

---

## SECCIÓN 6 — FASE D: EXPANSIÓN MULTI-SÍMBOLO

### 6.1 Justificación

El Lock Test (Phase 14) demostró que **un modelo entrenado en BTC no generaliza a ETH ni SOL**:

| Símbolo | Lock test (directional accuracy) | Fase 5 CV (F1) |
|---------|--------------------------------|-----------------|
| BTC | 75.3% | 0.72 |
| ETH | 53.3% | 0.72 |
| SOL | 50.3% | 0.73 |

Conclusión: **cada símbolo necesita su propio modelo.**

### 6.2 Progresión

Cuando BTC esté estable en Fase C (capital real), se replican los pasos de Fases A, B y C para ETH y SOL:

```
Para cada símbolo (ETH → SOL):

    1. Entrenar modelo propio (train_production_models.py)
    2. Serializar .pkl en models/{symbol}/
    3. Forward test (1-3 meses)
    4. Capital real ($100 → $500 → $1000)
```

Cada símbolo tiene:

- Dataset propio
- Entrenamiento propio
- Validación propia (backtest individual)
- Lock test propio
- Versionado propio

No comparten parámetros. Lo único compartido es el código del pipeline (feature computation, scaler, LR, thresholds).

### 6.3 Por qué no un modelo global

| Enfoque | Lock test | Conclusión |
|---------|-----------|------------|
| Modelo único entrenado en todos los símbolos | ❌ Falla (Phase 14) | No generaliza entre símbolos |
| Modelo por símbolo | ✅ BTC pasa, ETH/SOL marginal/falla pero son independientes | Cada símbolo tiene su propia dinámica |

### 6.4 Estado de ETH y SOL

Por ahora, ETH y SOL **no son prioridad**. Su validación es más débil que BTC. Entrarán al sistema solo después de que BTC demuestre estabilidad en capital real.

---

## SECCIÓN 7 — FASE E: ORQUESTADOR MULTI-MODELO

### 7.1 Arquitectura objetivo

```
                       ┌──────────────────────┐
                       │   ORCHESTRATOR        │
                       │                       │
                       │   ┌──────────────┐   │
Data ─────────────────►│   │ BTC Model    │───┼──► SIGNAL BTC
                       │   └──────────────┘   │
                       │   ┌──────────────┐   │
Data ─────────────────►│   │ ETH Model    │───┼──► SIGNAL ETH
                       │   └──────────────┘   │
                       │   ┌──────────────┐   │
Data ─────────────────►│   │ SOL Model    │───┼──► SIGNAL SOL
                       │   └──────────────┘   │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │  PORTFOLIO MANAGER    │
                       │  Capital allocation   │
                       │  Correlation caps     │
                       │  Exposure limits      │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │  RISK MANAGER         │
                       │  Drawdown breakers    │
                       │  ATR sizing           │
                       │  Frequency control    │
                       └───────────┬──────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │  EXCHANGE EXECUTOR    │
                       │  Order placement      │
                       │  Slippage tracking    │
                       └──────────────────────┘
```

### 7.2 Características

- **No es un modelo multi-activo**: cada modelo es independiente, entrena por separado, tiene su propio ciclo de vida
- **Todos ejecutan en paralelo** en el mismo timestep (misma barra 1h)
- **No comparten parámetros** ni pesos
- **Comparten capital, monitoreo y riesgo** a través del Portfolio Manager y Risk Manager
- **El Orchestrator** es solo un dispatcher: recibe data, la distribuye a cada modelo, recoge señales

### 7.3 Portfolio Manager

Componente más importante del sistema (el que realmente genera valor). Responsabilidades:

- Asignar capital entre BTC, ETH, SOL según signal strength
- Aplicar correlation-aware exposure caps
- Reducir tamaño total cuando hay overconfidence
- Monitorear correlación rolling 30d entre activos

### 7.4 Risk Manager

- ATR-based position sizing (stop_distance = ATR20 × 2)
- Drawdown circuit breaker (diario ≥ 5% → HALT)
- Exposure caps (máx 90% del capital, máx 50% por activo)
- Trade frequency control (máx 3 trades/día por activo por diseño)
- Circuit breakers: soft (reduce tamaño) y hard (detiene todo)

### 7.5 Exchange Executor

- Coloca órdenes en el exchange
- Trackea slippage real vs modelado
- Reintentos con backoff (máx 3 en 500ms, si falla → market order)
- Logging de cada orden

### 7.6 Timeline estimado

Fase E es posterior a Fase D. No se construye hasta que al menos 2 símbolos (BTC + ETH o BTC + SOL) estén en producción estable con capital real.

---

## SECCIÓN 8 — RAMA DE INVESTIGACIÓN FUTURA

### 8.1 Separación estricta

Producción e investigación son **dominios separados**. Producción nunca debe depender de experimentos. Investigación nunca debe ejecutarse sobre infraestructura de producción.

```
production/     → código estable, modelos frozen, sin experimentos
research/       → experimentos, prototipos, modelos no validados
```

### 8.2 Ramas de investigación (conceptual)

```
research/
├── xgboost/          → relaciones no lineales, feature importance
├── lightgbm/         → eficiencia computacional, leaf-wise trees
├── catboost/         → categorical features, ordered boosting
├── calibrators/      → Platt scaling, isotonic regression, beta calibration
├── stacking/         → meta-model que combina LR + tree-based
├── meta_models/      → modelos que filtran señales del LR base
├── transformers/     → attention-based sequence modeling (time series)
├── lstm/             → memoria larga, dependencias temporales
├── gru/              → variante eficiente de LSTM
└── new_features/     → nuevas fuentes de alpha (order book, on-chain, sentiment)
```

### 8.3 Propósito de cada rama

| Rama | Por qué podría importar | Madurez actual |
|------|------------------------|----------------|
| XGBoost/LightGBM/CatBoost | Capturar relaciones no lineales que LR no puede | Baja — no hay evidencia de que existan |
| Calibrators | Mejorar calibración de probabilidades | Media — Phase 6 mostró que LR ya está bien calibrado (ECE ~0.039) |
| Stacking | Combinar fortalezas de múltiples modelos | Baja — requiere primero tener modelos base validados |
| Meta-models | Segunda capa de decisión sobre señales del LR | Baja — agregar complejidad sin evidencia de beneficio |
| Transformers | Secuencias complejas, atención a contexto largo | Muy baja — requiere datos masivos, GPU, hiperparam tuning |
| LSTM/GRU | Dependencias temporales que LR no captura | Baja — el stride=5 + hold=5 ya captura la ventana relevante |
| New features | Fuentes adicionales de alpha | Baja — 53 features actuales ya dan alpha significativo |

### 8.4 Condición de activación

Ninguna rama de investigación se activa hasta completar:

1. ✅ Phase 10 (anti-leakage audit)
2. ✅ Fase A (modelos serializados)
3. ✅ Fase B (forward test 1-3 meses)
4. ✅ Fase C (capital real, al menos nivel $100)
5. ✅ Documentación de lecciones aprendidas en Fase B

Solo después de esos 5 hitos se puede considerar abrir una rama de investigación. Y aún así, solo si hay una **pregunta específica** que responder, no "a ver qué sale".

### 8.5 Regla de reemplazo

Ningún modelo nuevo reemplaza al LR champion sin:

1. Backtest completo (Phase 5-style CV)
2. Lock test cronológico (Phase 14)
3. Forward test (Fase B, mínimo 1 mes)
4. Evidencia de mejora significativa vs LR (F1 > 0.75, Sharpe > LR actual)

---

## SECCIÓN 9 — PRINCIPIOS DEL PROYECTO

### 1. Producción primero

El sistema funcionando 24/7 por 3 meses sin intervención vale más que cualquier experimento nuevo.

### 2. No perseguir complejidad innecesaria

LogisticRegression con 53 features lineales produce F1 > 0.71 en CV y 75.3% de accuracy direccional en lock test. No hay evidencia de que un modelo más complejo mejore esto. No se agrega complejidad sin evidencia.

### 3. No reemplazar Logistic Regression sin evidencia contundente

LR es:
- Interpretable (coeficientes con signo y magnitud)
- Rápido (inferencia < 1ms)
- Estable (misma semilla → mismos resultados)
- Barato computacionalmente (entrenamiento < 1s)

Cualquier reemplazo debe superar a LR en todas estas dimensiones, no solo en F1.

### 4. Separación estricta producción ↔ investigación

- Producción: código estable, modelos frozen, sin experimentos
- Investigación: notebooks, scripts, prototipos
- Nunca los dos en el mismo proceso
- Nunca un experimento corriendo en infraestructura de producción

### 5. La prioridad ya no es descubrir alpha

La prioridad es **construir un sistema robusto que pueda sobrevivir durante años**.

Eso significa:
- Forward testing antes de capital real
- Meses de estabilidad antes de escalar
- Monitoreo antes de optimización
- Supervivencia antes de crecimiento

---

## APÉNDICE A — Mapa de transición: viejo roadmap → nuevo roadmap

| Viejo roadmap (obsoleto) | Nuevo roadmap | Estado |
|--------------------------|---------------|--------|
| Phase 7 — Transformers | ❌ Reemplazado por Economic Alpha (QV2 Phase 7) | No necesario |
| Phase 8 — LSTM/GRU | ❌ Reemplazado por Capacity & Friction (QV2 Phase 8) | No necesario |
| Phase 9 — Redes profundas | ❌ Reemplazado por Paper Simulation (QV2 Phase 9) | No necesario |
| Phase 10 — ? | QV2 Phase 10 — Anti-Leakage Audit | **Pendiente** |
| — | Fase A — Modelos producción (serialización .pkl) | **Pendiente** |
| — | Fase B — Forward Test 1-3m | **Pendiente** |
| — | Fase C — Capital Real $100/$500/$1000 | **Pendiente** |
| — | Fase D — Expansión ETH/SOL | **Pendiente** |
| — | Fase E — Orquestador Multi-Modelo | **Pendiente** |
| — | Rama de investigación futura | **Postergado** |

---

## APÉNDICE B — Definiciones

| Término | Significado |
|---------|------------|
| QV2 | Quant Validation v2 — serie de experimentos que validaron el alpha |
| Lock test | Prueba cronológica estricta: train ≤ cutoff, test > cutoff, sin solapamiento |
| Forward test | Ejecución del modelo en datos vivos sin capital real |
| Directional accuracy | Precisión del modelo en barras con etiqueta direccional (excluye HOLD) |
| F1 | Media armónica de precisión y recall sobre la clase positiva |
| MTF | Multi-Timeframe — features derivadas de resample 4h y 1d |
| VS | Volatility Scoring — no implementado, no necesario |

---

*Este documento reemplaza cualquier roadmap anterior y es la referencia principal del proyecto.*  
*Próxima revisión: después de completar Fase B (Forward Test).*
