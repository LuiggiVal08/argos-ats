# Quant Trading System v3 — Production Architecture (Post-Validation Design)

> Basado en QV2 Phases 3.5–14. La experimentación terminó.  
> Esto es system engineering, no investigación.  
> Aplica a \`argos-ats\` (\`data-engine\` / \`analytics-engine\` / broker RESP).

---

## 1. Executive Summary

### Qué descubrimos

Alpha direccional por activo existe en BTC, ETH y SOL en timeframe 1h usando 53 features (TA + MTF 4h/1d + funding). Los tres activos pasan validación cruzada individual (F1 > 0.71), sobreviven costos (0.31% RT), splits de régimen, cambios de exchange, y estrés de capacidad.

El Lock Test (Phase 14) estableció el hallazgo crítico: **un modelo entrenado en BTC y aplicado a ETH/SOL en OOS cronológico estricto degrada a precisión casi aleatoria (BTC 75% → ETH 53% → SOL 50%)**. Dentro del mismo símbolo, el modelo preserva 99% de su precisión direccional.

Esto fuerza una conclusión arquitectónica clara.

### Qué construimos, no qué "proponemos"

Esto **NO** es:
- Un modelo universal multi-activo (falla en lock test)
- Un sistema de retraining continuo (modelos frozen)
- Un modelo de predicción (predecir dirección no es el negocio)
- Un ensemble black-box

Esto **ES**:

**Un sistema de decisión probabilística multi-activo con edge estructural en BTC y transferibilidad parcial.**

La diferencia es fundamental:

| | Modelo de predicción | Sistema de decisión |
|---|---|---|
| Output | "BTC va a subir" | "Asignar X% a BTC, Y% a ETH, Z% a SOL" |
| Éxito | Accuracy | Sharpe del portafolio |
| Riesgo | Error de clasificación | Exposición correlacionada |
| Componentes | 1 modelo | Models + Signal + Portfolio allocator |

### Arquitectura en 3 capas

```
                    ┌──────────────────────────────────────────┐
                    │     PORTFOLIO ALLOCATOR (Capa 3)         │
                    │  capital allocation · exposure limits    │
                    │  correlation-aware · circuit breakers    │
                    │                                         │
                    │  Output: size_btc, size_eth, size_sol    │
                    └──────────────┬───────────┬───────────────┘
                                   │           │
                    ┌──────────────▼───┐  ┌────▼──────────────┐
                    │  SIGNAL ENGINE   │  │  SIGNAL ENGINE    │
                    │  BTC (Capa 2)    │  │  ETH (Capa 2)     │
                    │  y_proba → BUY/  │  │  y_proba → BUY/   │
                    │  SELL/HOLD       │  │  SELL/HOLD        │
                    └──────┬───────────┘  └─────┬─────────────┘
                           │                    │
                    ┌──────▼───────────┐  ┌─────▼─────────────┐
                    │  MODEL BTC       │  │  MODEL ETH        │
                    │  (Capa 1)        │  │  (Capa 1)         │
                    │  LR frozen       │  │  LR frozen        │
                    │  RobustScaler    │  │  RobustScaler     │
                    │  53 features     │  │  53 features      │
                    └──────┬───────────┘  └─────┬─────────────┘
                           │                    │
                    ┌──────▼────────────────────▼──────────────┐
                    │        FEATURE PIPELINE (compartido)      │
                    │  OHLCV 1h · TA 20 · MTF 4h/1d · Funding  │
                    │  Código compartido, instancias separadas  │
                    └──────────────────────┬────────────────────┘
                                           │
                    ┌──────────────────────▼────────────────────┐
                    │         BROKER (Redis RESP stream)        │
                    │  ticks:{symbol} · signals:{symbol}        │
                    │  positions:{symbol} · portfolio:state     │
                    └───────────────────────────────────────────┘
```

### Por qué 3 capas y no 1

Cada capa resuelve un problema diferente y puede fallar independientemente:

| Capa | Problema que resuelve | Falla sin detener |
|------|----------------------|-------------------|
| 1 — Models | Inferencia estadística por activo | Solo ese activo |
| 2 — Signal Engine | Decisión de trading por activo (thresholds, flip logic) | Solo ese activo |
| 3 — Portfolio Allocator | Asignación de capital entre activos | Todo el portafolio |

---

## 2. System Architecture — Especificación de Implementación

### 2.1 Capa 1 — Models (por símbolo)

**Qué es**: Un archivo `.pkl` por activo conteniendo:
- `LogisticRegression` entrenado (coeficientes + intercepto frozen)
- `RobustScaler` fitted (centro y escala frozen)
- Lista de feature names (53, ordenadas)

**Reglas**:
- Cada modelo se entrena offline con su propio dataset (OHLCV de ese símbolo)
- No comparten datos de entrenamiento
- No comparten parámetros
- Son inmutables en producción

**Formato de artefacto**:
```
models/
├── btc/
│   ├── model.pkl          # LogisticRegression
│   ├── scaler.pkl         # RobustScaler
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

**NO**: modelo multi-output, shared layers, entrenamiento conjunto, fine-tuning.

### 2.2 Capa 2 — Signal Engine (paralelo)

**Qué es**: Para cada activo, un proceso/hilo que en cada barra:
1. Recibe OHLCV actualizado
2. Computa features (53)
3. Escala con RobustScaler del activo
4. Inference: `LR.predict_proba(X_scaled)[0, 1]` → `y_proba`
5. Aplica thresholds:
   - `y_proba > 0.60` → BUY
   - `y_proba < 0.40` → SELL
   - else → HOLD
6. Publica `{symbol, timestamp, y_proba, signal}` a `signals:{symbol}`

**Características**:
- Los 3 signal engines corren en paralelo en el mismo timestep
- No se esperan entre sí
- No comparten estado interno
- Si uno falla, los otros continúan

**Output (a broker stream)**:
```json
{
  "symbol": "BTC",
  "timestamp": "2026-06-17T10:00:00Z",
  "y_proba": 0.72,
  "signal": "BUY",
  "bar_idx": 38500
}
```

### 2.3 Capa 3 — Portfolio Allocator

**Qué es**: Un proceso que consume los 3 streams `signals:{symbol}` y decide:
- Qué posición tomar para cada activo
- Con qué tamaño
- Con qué límites de riesgo

**Input**: Los últimos signals de cada activo (publicados por los Signal Engines).

**Output**: `orders:{symbol}` con `(side, size, timestamp)`.

**Reglas**:
1. No ejecuta sin señal
2. No sobrepasa exposure caps
3. Respeta correlation-aware limits
4. Aplica circuit breakers antes de emitir órdenes

---

## 3. Execution Model

### 3.1 Bar processing loop

Para cada barra `t` de 1h:

```
1. Tick llega a data-engine → Redis ticks:{symbol}
2. analytics-engine consume tick → completa OHLCV barra
3. Feature pipeline: 53 features con datos ≤ t (backward-safe)
4. Signal Engine (cada símbolo en paralelo):
   a. scale(X) con scaler del símbolo
   b. predict_proba → y_proba
   c. threshold → BUY/SELL/HOLD
   d. publish a signals:{symbol}
5. Portfolio Allocator (consume todos los signals):
   a. Lee positions actuales de positions:{symbol}
   b. Decide: abrir/cerrar/reducir por activo
   c. Calcula size con ATR
   d. Aplica exposure caps
   e. Publica orders:{symbol}
6. data-engine (o execution adapter): ejecuta órdenes
7. Logging: heartbeat, trade_log, equity
```

### 3.2 Entry / Exit rules

**Entry** (cuando no hay posición y signal ≠ HOLD):
- Precio: `close[t]`
- Size: `free_balance × target_fraction × risk_pct / (ATR20[t] × 2)`
- Cap: `free_balance × target_fraction × 0.5`
- Cost: `size × 0.00155`

**Exit por hold** (cuando `bars_held >= 5` y signal = HOLD):
- Precio: `close[t]`
- PnL: `size × (close[t] / entry_close − 1) × side`
- Cost: `size × 0.00155`
- Capital retorna + net PnL

**Flip** (cuando hay posición Y signal es opuesto):
- Cerrar posición actual a `close[t]` (con costos)
- Abrir nueva posición a `close[t]` (con costos)
- Misma barra

### 3.3 Cost model (fijo)

| Componente | Por lado | Round trip |
|-----------|----------|------------|
| Fee exchange | 0.10% | 0.20% |
| Slippage | 0.05% | 0.10% |
| Spread/2 | 0.005% | 0.01% |
| **Total** | **0.155%** | **0.31%** |

Este modelo se usó en QV2 Phases 7, 11, 12 y está validado contra datos reales de exchange.

### 3.4 Invariantes de no-lookahead

- `close[t]` es el ÚNICO precio disponible para entry/exit en barra `t`
- `high[t]`, `low[t]` NO se usan para ejecución
- TA features: solo `rolling(window)` (hacia atrás)
- MTF: resample de buckets completados únicamente
- Funding: `ffill` del último rate conocido
- Labels: se evalúan en `t+5`, NUNCA en inferencia
- ATR20: precomputado sobre OHLCV completo, indexado por barra (backward-safe por construcción con rolling(20))

---

## 4. Parallel Execution Model

### 4.1 Concurrent inference

Cada Signal Engine es una función stateless:

```python
X = build_features(ohlcv[sym], funding[sym], bar_idx)
X_scaled = scalers[sym].transform([X])
proba = models[sym].predict_proba(X_scaled)[0, 1]
```

No hay shared state, no hay mutex, no hay comunicación entre signal engines. El único punto de sincronización es el Portfolio Allocator.

### 4.2 Multi-agent, no multi-task

Multi-task learning (un solo modelo prediciendo todos los símbolos) se probó implícitamente en Phase 14 y **falló**. Los modelos son:

- **Arquitectónicamente idénticos** (mismo LR config, mismas 53 features)
- **Paramétricamente independientes** (coeficientes distintos, scalers distintos)
- **Training-data-disjoint** (dataset de cada símbolo es su propio OHLCV)

Esto es un **swarm de agentes homogéneos**, no un sistema multi-output. Cada agente se especializa en la dinámica de un activo.

### 4.3 Requirements

3 inferencias LR paralelas sobre 53 floats cada una: < 1ms total. El bottleneck real es feature computation (~100ms por barra para 3 símbolos con pandas vectorizado).

### 4.4 Dependency between models (lo que importa)

Lo importante NO es que los modelos corran en paralelo. Lo importante es:

**La dependencia entre ellos.**

BTC sube → ETH suele reaccionar → SOL amplifica. Las correlaciones cambian por régimen.

Por eso el Portfolio Allocator existe como capa separada: su trabajo es modelar y responder a estas dependencias, no los modelos individuales.

---

## 5. Portfolio Construction Logic

### 5.1 Capital pool

$100,000 USD. Todas las posiciones draw de este pool y retornan a él.

### 5.2 Asignación baseline (floor caps)

| Asset | Allocación | Ratio | Lock Test |
|-------|-----------|-------|-----------|
| BTC | 50% | Anchor | PASS (75.3% directional acc) |
| ETH | 30% | Extension | MARGINAL (53.3%) |
| SOL | 20% | Extension | FAIL (50.3%) |

Estos son topes máximos (floor caps), no objetivos. El capital realmente desplegado depende de:
- Signal strength (y_proba − 0.50)
- ATR (volatilidad)
- Correlación actual entre activos

### 5.3 Dynamic allocation by signal strength

```
confidence = abs(y_proba − 0.50) × 2    # range [0.0, 1.0]
target_frac = base_allocation[sym] × confidence
```

Si `y_proba = 0.55` (confianza 0.10), BTC asigna solo 5% de su 50%. Esto evita overcapitalizar señales débiles.

### 5.4 Position sizing (ATR-based)

```
atr = ATR20[sym][t]
stop_distance = atr × 2.0
risk_amount = target_frac × free_balance × 0.01    # 1% del allocation
raw_size = risk_amount / stop_distance
cap = free_balance × target_frac × 0.5             # hard cap
size = min(raw_size, cap)
```

Validado en Phase 12: risk_1 da Sharpe 0.38 con Max DD −8.6%. risk_2 da Sharpe 0.47 con Max DD −17.1%. El default es risk_1.

### 5.5 Correlation-aware allocation

Este es el componente crítico que la mayoría de los sistemas no implementa bien.

**Reglas**:
1. Monitoreo diario de correlación rolling 30d entre pares (BTC-ETH, BTC-SOL, ETH-SOL)
2. Si correlación BTC-ETH > 0.85: exposición combinada máxima 60% del capital total
3. Si los 3 activos señalan simultáneamente: reducir cada uno proporcionalmente
4. Si correlación cruzada promedio > 0.75: activar modo "low correlation regime" → reducir tamaño total 25%

**Implementación**:
```python
rho_btc_eth = correlation(returns_btc, returns_eth, 30d)
if rho_btc_eth > 0.85:
    combined_exposure = pos_btc + pos_eth
    if combined_exposure > 0.60:
        scale = 0.60 / combined_exposure
        pos_btc *= scale
        pos_eth *= scale
```

Esto no es un modelo de portafolio Markowitz completo. Es una capa de sentido común que evita la ruina por correlación.

---

## 6. Risk Management Layer

### 6.1 Trade-level

| Regla | Valor |
|-------|-------|
| Pérdida máxima por trade | 1% del free balance (vía ATR sizing) |
| Stop distance | ATR20 × 2 |
| Costos incluidos | Gross PnL − entry_cost − exit_cost |
| Frecuencia | Máx 1 trade por símbolo cada 5 barras (por diseño) |

### 6.2 Portfolio-level

| Métrica | Threshold | Acción |
|---------|-----------|--------|
| Drawdown diario | ≥ 5% | HALT total: cerrar posiciones, modo PASIVO |
| Exposición total | ≥ 90% de free capital | Rechazar nuevas posiciones |
| Exposición por activo | > 50% de su allocation cap | Hard floor cap |
| Calidad de señal | y_proba ∈ [0.40, 0.60] | Forzar HOLD |
| Pérdidas consecutivas | ≥ 5 por símbolo | Reducir allocación 50%, log para revisión |

### 6.3 Circuit breakers

**Soft breaker** (no detiene, solo reduce):
- ATR20 > 3× mediana 30d → reducir tamaño 50%
- Volumen de señales < 20% del promedio 30d → log regime-shift alert

**Hard breaker** (detiene todo):
- Drawdown diario ≥ 5% → cerrar todo, modo PASIVO, halt loop
- Pérdida intra-hora > 2% → cerrar todo, skip siguiente barra
- Broker Redis unreachable > 5s → halt, retry, fail-safe close

### 6.4 Frequency control

Sin límite explícito por hora (el hold de 5 barras es el throttle natural). En el peor caso donde cada QUINTA barra tiene un flip, el sistema ejecuta máximo `3 × (24/5) ≈ 14` trades por día. Dentro de límites de exchange y dentro del budget de costos.

---

## 7. Deployment Roadmap — Fases de Implementación

### Principio rector

BTC es el activo comprobado. Todo lo demás es extensión.  
El orden de deploy **no es opcional**. Sigue la jerarquía de riesgo.

### Fase 1 — BTC Production Core (Semanas 1–3)

**Qué**: Deploy solo BTC en paper trading.

**Por qué primero**: BTC es el único activo que pasa el Lock Test con 75% directional accuracy. Es el que tiene más evidencia. Cualquier bug en el pipeline de ejecución se detecta en BTC sin arriesgar ETH/SOL.

**Acciones**:
1. Congelar BTC model.pkl + scaler.pkl del pipeline validado
2. Deploy feature pipeline en `analytics-engine` sirviendo endpoint `/infer/btc`
3. Deploy `data-engine` consumiendo WebSocket OHLCV 1h → Redis
4. Configurar `analytics-engine` para: escuchar stream → computar features → inferir → publicar señal
5. Logging de TODAS las señales a parquet (sin ejecución trades — solo monitoreo)
6. Verificar que distribución de y_proba coincide con Phase 9

**Check**:
- Feature pipeline < 200ms P99 por barra
- Inferencia < 10ms por barra
- Distribución de señales coincide con validación ±5% por decil
- Sin NaN, sin zero-division errors
- Mínimo: 200+ barras de operación continua

### Fase 2 — BTC Paper Trading (Semanas 4–5)

**Qué**: Habilitar ejecución paper para BTC.

**Acciones**:
1. `ENVIRONMENT_MODE = PAPER_TRADING`
2. Ejecutar trades en entorno simulado
3. Track: PnL, equity curve, trade_log
4. Comparar equity curve paper vs Phase 9 simulation
5. Verificar cost model contra slippage real

**Check**:
- PnL profile dentro de ±20% de Phase 9 en período comparable
- Cero errores de ejecución (flips perdidos, double entries, stale positions)
- Equity curve loggeada a broker stream cada barra
- Mínimo: 300 barras (~12.5 días)

### Fase 3 — ETH Shadow Mode (Semanas 6–7)

**Qué**: Deploy ETH en paralelo con BTC paper. **Cero capital real.**

**Por qué shadow**: ETH no está validado en lock test (53% directional acc). No se le puede asignar capital hasta ver su comportamiento en vivo.

**Acciones**:
1. Congelar ETH model.pkl + scaler.pkl
2. Deploy Signal Engine ETH separado en `analytics-engine`
3. Publicar a `signals:eth` (misma estructura)
4. BTC paper continúa sin cambios
5. Comparar distribución de señales ETH contra Phase 5 validation

**Check**:
- ETH inference corre sin afectar latencia de BTC
- Distribución de señales coincide con validación
- Sin bugs de shared state

### Fase 4 — SOL Shadow Mode (Semanas 8–9)

**Qué**: SOL en shadow mode, idéntico a ETH.

**Check**: Los 3 símbolos generando señales, solo BTC ejecutando trades.

### Fase 5 — Portfolio Activation (Semana 10)

**Qué**: Habilitar Portfolio Allocator para los 3 símbolos.

**Por qué último**: El Portfolio Allocator depende de los 3 streams de señal. No puede existir hasta que todos los signal engines estén funcionando y validados.

**Acciones**:
1. Deploy Portfolio Allocator en `analytics-engine`
2. Habilitar paper execution para ETH y SOL
3. Semana 1: equal-weight baseline (sin dynamic allocation)
4. Semana 2: habilitar dynamic allocation por signal strength
5. Semana 3: habilitar correlation-aware allocation
6. Monitoreo diario de correlación y exposición

**Check**:
- Equity curve del portafolio loggeada cada barra
- PnL por activo trackeado independientemente
- Sin double-counting, sin overallocation
- Correlation monitoring operacional

### Fase 6 — Estabilidad (Semanas 11–16) — LA FASE MÁS IMPORTANTE

**Qué**: SEIS SEMANAS de monitoreo puro. **Cero cambios de parámetros.**

**Por qué**: Esta fase es la que mata sistemas cuantitativos. La tentación de "optimizar" basado en lo que se ve en las primeras semanas es la causa #1 de overfitting a la data de deploy.

**Reglas**:
- Zero parameter modifications: no threshold changes, no weight adjustments, no model updates
- Track diario: Sharpe rolling 30d, Max DD, Win Rate, Signal count, ATR regime
- Log de anomalías a `anomalies:portfolio`
- Si UNA semana tiene PnL negativo: flag para revisión, NO intervención
- Si TRES semanas consecutivas tienen PnL negativo: revisión del sistema

**Check**: 6 semanas sin intervención humana en parámetros.

### Fase 7 — Live Activation (Semana 17+)

**Qué**: Switch a `ENVIRONMENT_MODE = LIVE` con BTC solamente.

**Acciones**:
1. Verificar secrets en env vars
2. API keys con permisos restringidos (trade only, sin withdraw)
3. LIVE mode para BTC (ETH y SOL siguen en paper)
4. Tamaño mínimo de posición ($10–$50)
5. Primera semana: revisión manual diaria de todos los trades
6. Si no hay anomalías en 2 semanas: escalar BTC a target 50%

### Fase 8 — Portfolio Live (Semana 20+)

1. ETH a PAPER_TRADING (1 semana)
2. ETH a LIVE con tamaño mínimo
3. SOL a PAPER_TRADING (1 semana)
4. SOL a LIVE con tamaño mínimo
5. Scaling gradual sobre 4 semanas

### Fase 9 — Optimización (Semana 24+)

**CONDICIONAL**: Solo después de estabilidad confirmada en Fase 7–8.

**Permitido**:
- Ajustar allocation percentages por activo
- Tuneo de ATR multiplier (default 2.0)
- Ajustar confidence mapping en dynamic allocation

**Prohibido**:
- Retrain LR models
- Cambiar feature set
- Ajustar BUY/SELL thresholds
- Cambiar lookahead o stride
- Agregar nuevos activos
- Cualquier cambio en el pipeline de inferencia

---

## 8. System Constraints (No Negociables)

### 8.1 Model immutability

Los modelos se entrenan OFFLINE vía pipeline QV2 y se deployan como pickle + scaler. **No hay retraining en el loop de producción**. Si se detecta degradación (vía monitoreo), la respuesta es HALT, no "adaptar".

### 8.2 Feature pipeline freeze

Las 53 features están lockeadas:
- 20 base TA (open, high, low, close, volume, rsi, ema_9/21/50, macd, bb_20/2, atr, adx, obv, volume_sma, pct_change)
- 30 MTF (15 indicadores × 2 timeframes: 4h, 1d)
- 3 funding (rate, momentum, change)

No se agregan features en producción. No se hace feature selection en el execution path.

### 8.3 Threshold immutability

BUY threshold = 0.60, SELL threshold = 0.40. Congelados desde Phase 7. Cualquier cambio en thresholds crea una estrategia nueva que no ha pasado lock test.

### 8.4 Strict chronology

- Feature computation en barra `t` usa datos de barras `[0..t]` únicamente
- Entry en barra `t` usa `close[t]` como precio de entrada
- Exit en barra `t+5` usa `close[t+5]` como precio de salida
- Sin lookahead de ningún tipo en el execution path
- ATR20 precomputado sobre OHLCV completo (backward-safe por rolling(20))

### 8.5 Cross-symbol independence

Models no comparten parámetros, gradientes, ni datos de entrenamiento. El código del feature pipeline es compartido (un solo codebase), pero cada instancia por símbolo es separada. Una falla en el pipeline de cualquier símbolo NO debe propagarse.

### 8.6 No más experimentación

QV2 terminó. No se agregan fases nuevas, no se prueban features nuevos, no se testean modelos alternativos en producción. El pipeline de investigación está cerrado. Lo que sigue es system engineering.

---

## 9. Monitoring & Logging

### 9.1 Heartbeat (cada 30s)

| Stream | Contenido |
|--------|-----------|
| `heartbeat:portfolio` | timestamp, equity, open_positions, free_balance, mode |
| `heartbeat:btc` | timestamp, bar_idx, latency_ms, signal_count |
| `heartbeat:eth` | mismo schema |
| `heartbeat:sol` | mismo schema |

### 9.2 Trade-level logging

Cada exit produce un registro a `trades:parquet` (disco) y `trades:portfolio` (broker stream):

```json
{
  "entry_ts": "2026-06-17T10:00:00Z",
  "exit_ts": "2026-06-17T15:00:00Z",
  "symbol": "BTC",
  "side": "LONG",
  "size": 0.42,
  "entry_price": 67500.00,
  "exit_price": 68200.00,
  "gross_pnl": 294.00,
  "entry_cost": 43.89,
  "exit_cost": 44.07,
  "net_pnl": 206.04,
  "duration_bars": 5,
  "exit_reason": "signal|signal_flip",
  "atr_entry": 380.00
}
```

### 9.3 Performance metrics (daily)

| Métrica | Stream | Frecuencia |
|---------|--------|------------|
| Sharpe rolling 30d | `metrics:portfolio` | Diario |
| Max Drawdown | `metrics:portfolio` | Por barra (si cambia) |
| Win Rate por símbolo | `metrics:portfolio` | Diario |
| Signal count (7d avg) | `metrics:portfolio` | Diario |
| ATR regime (30d median) | `metrics:{sym}` | Por barra |
| y_proba distribution | `metrics:{sym}` | Cada 100 barras |
| Correlación cruzada 30d | `metrics:correlation` | Diario |
| Time Under Water | `metrics:portfolio` | Diario |

### 9.4 Anomaly detection

| Anomalía | Detección | Acción |
|----------|-----------|--------|
| y_proba distribution shift | KS test vs training (p < 0.01) | WARNING, flag review |
| Signal collapse | < 20% de volumen esperado 7d | Regime-shift alert |
| ATR spike | ATR20 > 3× mediana 30d | Soft breaker: −50% size |
| Drawdown spike | Daily loss > 5% | Hard breaker: HALT |
| NaN en feature pipeline | Input validation | Skip bar, log ERROR |
| Broker disconnection | Heartbeat timeout > 5s | HALT, retry, fail-safe |
| Correlación alta | 30d cross-correlation > 0.85 | Cap exposición 60% |

### 9.5 Alerting

| Nivel | Tiempo respuesta | Acción |
|-------|------------------|--------|
| WARNING | 24h | Log, continúa, revisión programada |
| ERROR | 1h | Violación de constraint, atención inmediata |
| HALT | Inmediato | Circuit breaker, sistema detenido |

Salidas:
1. `alerts:portfolio` broker stream (persistente)
2. STDOUT (container logs)
3. Webhook opcional → Telegram/Discord vía broker consumer

---

## 10. Key Insight Summary

### Lo que la validación demostró

1. **El alpha es real pero específico por activo**. Lock Test (Phase 14) probó que un modelo entrenado en BTC no generaliza a ETH/SOL (75% → 53% → 50%). Dentro del mismo símbolo, preserva 99%. Cada activo necesita su propio modelo frozen.

2. **BTC es el anchor de producción**. Tres validaciones independientes:
   - CV F1 = 0.72 (Phase 5)
   - Lock test directional accuracy = 75.3% (Phase 14)
   - 7007 trades backtest Sharpe = 4.60 (Phase 11)
   - Sobrevive costos, regímenes, switches de exchange

3. **ETH y SOL son activos de extensión**. Validan en CV pero degradan en lock test. Su rol en producción es secundario: proveen diversificación cuando el portfolio layer está activo, pero no deben bloquear el deploy de BTC.

4. **El Portfolio Allocator es donde se realiza el edge**. Los modelos individuales producen señales direccionales. El allocator convierte esas señales en retornos ajustados por riesgo mediante:
   - Asignación dinámica por signal strength
   - Sizing ajustado por volatilidad (ATR)
   - Exposure limits correlation-aware
   - Circuit breakers

5. **La experimentación terminó**. QV2 está completo. Lo que sigue es system engineering: convertir un pipeline de investigación validado en un sistema de ejecución estable.

### Lo que NO estamos haciendo

- No construir un modelo universal — lock test probó que falla
- No retraining en producción — frozen artifacts only
- No agregar features — 53-feature set lockeado
- No tunear thresholds — 0.60/0.40 congelados
- No deployar ETH/SOL con capital real junto a BTC — solo por fases
- No abrir nuevas fases de investigación QV
- No correr en LIVE sin 6 semanas de estabilidad primero

### Postura de riesgo

Este sistema está diseñado para preservación de capital primero, captura de alpha después. Cada decisión arquitectónica — modelos independientes, deploy por fases, circuit breakers, ATR sizing, thresholds congelados — prioriza prevenir pérdida catastrófica sobre maximizar retorno.

El Sharpe validado de 0.34–0.47 con ATR sizing conservador (Phase 12) confirma que el sistema produce retornos positivos bajo constraints realistas de riesgo. El Sharpe de 18.10 de Phase 9 fue paper trading sin risk management — **no es el target de producción**.

---

**Documento**: v1.1  
**Basado en**: QV2 Phases 3.5–14 (completado)  
**Aplica a**: argos-ats (data-engine / analytics-engine / broker RESP)  
**Estado**: Implementación — la experimentación terminó  
**Próxima revisión**: Después de Fase 1 stability period (Semana 4)
