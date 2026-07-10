# Forward Test Experiment 001

> Experimento oficial de Forward Testing del sistema ARGOS ATS.
> Fecha de inicio: 2026-07-10

---

## Identificación

| Campo | Valor |
|---|---|
| Experiment ID | `FT-001` |
| Fecha de inicio | 2026-07-10 |
| Commit SHA | `619603e` |
| Tag | `v0.9.1-forward-test` |
| Branch | `dev` |

---

## Configuración del Sistema

### Modelo

| Campo | Valor |
|---|---|
| Model ID | `qv2_target_spec_v1_reduced_33_primary` |
| Classifier | LogisticRegression (sklearn) |
| Checksum (model.pkl) | `551be07c74d8cba6363a4852fb5b2488` |
| Checksum (scaler.pkl) | `6f9981c7f95fa72ff0615545e26ddd29` |
| Checksum (metadata.json) | `3380de611149eac0ad2dfba4ffd37151` |
| Features | 30 |
| Encoding | 0=SELL, 1=HOLD, 2=BUY |

### Feature Schema

Las 30 features son calculadas por `DataPreprocessor.build_features()` e incluyen:

- Indicadores técnicos: RSI, EMA, SMA, MACD, Bollinger Bands, ATR, ADX
- Características de precio: returns, volatility, momentum
- Características de volumen: volume ratios, OBV
- Multi-timeframe: features de 4h y 1d aggregados

### Engine de Inferencia

| Campo | Valor |
|---|---|
| Engine | `StreamingInferencePipeline` |
| Ubicación | `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py` |
| Validación | `classes_` vs `SignalSide` (línea 298-305) |
| Model path | `models/production/{symbol}/` |

### Portfolio Context

| Campo | Valor |
|---|---|
| Version | v1 |
| Cluster limit | 3 |
| Dynamic thresholds | enabled |
| Cluster detection | basado en correlación de retornos |

### Risk Engine

| Campo | Valor |
|---|---|
| Risk cap | 1% del free balance por trade |
| SL distance | Derivada de ATR (no porcentaje fijo) |
| Drawdown circuit breaker | 5% pérdida diaria → halt total |
| Order retry | Máx 3 reintentos en 500ms |
| Emergency liquidation | Market order al fallar retries |

### Execution Guard

| Campo | Valor |
|---|---|
| Confidence threshold (TRENDING) | ≥ 0.55 |
| Confidence threshold (RANGING) | ≥ 0.62 |
| Volatility spike detection | ATR/price > 2x trailing avg → 50% size reduction |
| Soft circuit breaker | 3 failures → 30s pause |
| Env vars | `EXECUTION_THRESHOLD_TRENDING`, `EXECUTION_THRESHOLD_RANGING` |

### Trading

| Campo | Valor |
|---|---|
| Mode | `PAPER_TRADING` |
| Symbol | BTC/USDT |
| Timeframe | 1h |
| Exchange | Binance Futures (testnet) |
| Capital inicial | Virtual (configurable) |

---

## Objetivos del Experimento

1. **Validar estabilidad del pipeline en operación continua** — El sistema debe correr 24/7 sin errores críticos.
2. **Medir calidad de señales del modelo** — Distribución BUY/HOLD/SELL, confianza media, calibración.
3. **Evaluar performance de trading** — Win rate, expectancy, profit factor, PnL.
4. **Monitorear riesgo** — Drawdown, rachas, cooldowns, circuit breaker activations.
5. **Detectar drift** — Cambios en distribución de features, regime shifts, degradación del modelo.
6. **Documentar comportamiento real** — Todas las decisiones, anomalías, y ajustes observados.

---

## Hipótesis del Experimento

1. **H1**: El modelo LogisticRegression con 30 features genera señales con edge estadísticamente significativo sobre random walk.
2. **H2**: El ExecutionGuard con thresholds regime-aware filtra señales de baja calidad sin reducir excesivamente la frecuencia de trading.
3. **H3**: El Portfolio Context v1 con cluster detection evita concentración excesiva en activos correlacionados.
4. **H4**: El Risk Engine con ATR-based SL y 1% risk cap protege el capital en escenarios de alta volatilidad.
5. **H5**: El sistema completo es estable en PAPER_TRADING por al menos 30 días sin intervención manual.

---

## Métricas que Serán Observadas

### Modelo
- Inferencias totales
- Distribución BUY/SELL/HOLD
- Confianza media y desviación estándar
- Calibración (predicted vs actual)
- Estabilidad de probabilidades (rolling window)

### Trading
- Trades ejecutados
- Win rate
- Expectancy
- Profit factor
- PnL total y diario
- Turnover rate
- Cost ratio

### Riesgo
- Maximum drawdown
- Drawdown actual
- Rachas ganadoras/perdedoras
- Circuit breaker activations
- Cooldown periods

### Portfolio Context
- Clusters detectados
- Señales bloqueadas por cluster limit
- Tamaño promedio de posición
- Distribución de posiciones

### Mercado
- Régimen dominante (TRENDING/RANGING)
- Volatilidad (ATR/price ratio)
- Feature drift score (si disponible)

### Observabilidad
- Errores por tipo
- Reinicios del servicio
- Latencia de inferencia (p50, p95)
- Health checks
- Buffer occupancy

---

## Condiciones para Dar por Terminado el Experimento

### Finalización exitosa (30 días)
- El sistema ha operado continuamente por 30 días sin errores críticos
- Se han recopilado al menos 720 inferencias (1/hora × 30 días)
- No se ha activado el drawdown circuit breaker más de 3 veces
- El PnL acumulado es mayor a -5% del capital inicial

### Finalización por fracaso
- El sistema ha tenido más de 5 reinicios forzados
- El drawdown ha superado el 10% del capital inicial
- Se ha detectado un bug crítico en la lógica de trading
- El modelo ha dejado de generar señales (todas HOLD)

### Finalización por decisión manual
- Se ha identificado un cambio de régimen que invalida la hipótesis
- Se necesita actualizar el modelo o features (requiere nuevo experimento)
- Condiciones de mercado extremas que hacen el experimento no representativo

---

## Issues Conocidos (NO forman parte del experimento)

| # | Issue | Prioridad | Impacto |
|---|---|---|---|
| 1 | `PredictEnsembleSignalUseCase` — `_ensemble_decision_raw` tiene mapping de clases invertido | Media | Afecta solo `POST /model/predict`; NO afecta el streaming loop |
| 2 | `create_targets_triple_barrier()` — encoding swap vs global standard | Baja | Solo se llama en `scripts/export_training_data.py`, no en producción |
| 3 | `export_training_data.py` — print labels inconsistentes con column encoding | Baja | Cosmético; no afecta datos exportados |

---

## Cambios que Invalidarían la Comparación con Este Baseline

Cualquier modificación de:

- **Modelo**: pesos, arquitectura, tipo de classifier
- **Features**: lista de features, número de features, cálculo
- **Inferencia**: lógica de `StreamingInferencePipeline`
- **Thresholds**: ExecutionGuard thresholds, confidence thresholds
- **Portfolio Context**: cluster detection, cluster limit, dynamic thresholds
- **Risk Engine**: risk cap, SL calculation, drawdown threshold
- **Reglas de ejecución**: order retry, emergency liquidation, position sizing

**Si cualquiera de estos cambia, se debe crear un nuevo experimento (FT-002) con un nuevo baseline.**

---

## Freeze Policy

> **POLÍTICA DE CONGELAMIENTO**
>
> Durante la vigencia de este experimento (FT-001), los siguientes
> componentes están CONGELADOS y NO deben ser modificados:
>
> 1. **Modelo**: pesos, arquitectura, hyperparameters
> 2. **Features**: lista completa de 30 features y su cálculo
> 3. **Inferencia**: `StreamingInferencePipeline` completa
> 4. **Thresholds**: ExecutionGuard (TRENDING ≥ 0.55, RANGING ≥ 0.62)
> 5. **Portfolio Context v1**: cluster detection, limits, dynamic thresholds
> 6. **Risk Engine**: 1% cap, ATR-based SL, 5% drawdown halt
> 7. **Execution rules**: order retry, emergency liquidation
>
> **Cualquier cambio en estos componentes requiere:**
> 1. Documentar el cambio en `DECISIONS.md`
> 2. Crear un nuevo experimento (FT-002)
> 3. Crear un nuevo baseline (`v0.9.2-forward-test` o superior)
> 4. Reiniciar el forward test desde cero
>
> **Cambios PERMITIDOS durante el experimento:**
> - Dashboards, métricas, logging
> - Scripts de análisis
> - Replay y reporting tools
> - Observability improvements (no-behavioral)
> - Bug fixes que no alteren el comportamiento de trading

---

## Infraestructura de Observability

### Archivos generados

| Archivo | Propósito |
|---|---|
| `reports/forward-test/YYYY-MM-DD.json` | Snapshot diario del experimento |
| `reports/forward-test/daily_report.json` | Reporte diario generado por MetricsTracker |
| `reports/forward-test/trades.csv` | Log de trades del día |
| `forward_test/signals_btc.csv` | Señales en tiempo real |
| `forward_test/equity_btc.csv` | Curva de equity |
| `forward_test/heartbeat_btc.log` | Heartbeat del engine |
| `forward_test/trades_btc.csv` | Trades ejecutados |
| `forward_test/state_btc.json` | Estado actual del engine |

### Scripts disponibles

| Script | Propósito |
|---|---|
| `scripts/forward_test/engine.py` | Engine principal de forward test |
| `scripts/forward_test/metrics.py` | MetricsTracker — acumulación y reportes |
| `scripts/forward_test/watchdog.py` | Monitoreo de salud del sistema |
| `scripts/forward_test/policy.py` | Política de trading (thresholds) |
| `scripts/forward_test/regime.py` | Clasificación de régimen de mercado |
| `scripts/generate_daily_snapshot.py` | Generador de snapshots diarios |

---

## Registro de Cambios

| Fecha | Cambio | Autor | Impacto |
|---|---|---|---|
| 2026-07-10 | Experimento FT-001 creado | AI Agent | Baseline inicial |

---

**Última actualización**: 2026-07-10
