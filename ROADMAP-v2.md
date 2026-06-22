# DOCUMENTO DE ESPECIFICACIÓN TÉCNICA (SPEC V5.0)

# PROYECTO: ARGOS 2.0 – ADAPTIVE MULTI-MODEL QUANTITATIVE TRADING PLATFORM

---

# PARTE I – ARQUITECTURA GENERAL Y MOTOR DE DATOS

---

# 1. Visión General

ARGOS 2.0 evoluciona desde un sistema de trading basado en una única red neuronal LSTM hacia una plataforma cuantitativa modular orientada a eventos y desacoplada por dominios.

La arquitectura se diseña bajo los siguientes principios:

* Event Driven Architecture.
* Arquitectura Hexagonal.
* Clean Architecture.
* Agnosticismo de infraestructura.
* Separación entre inferencia, entrenamiento y ejecución.
* Resiliencia ante fallos parciales.
* Promoción automática de modelos.
* Gestión adaptativa del riesgo.

---

# 2. Arquitectura Global

```text
                Exchange
                    │
                    ▼
             Data Engine (NestJS)
                    │
             Message Bus (RESP)
                    │
        ┌───────────┼────────────┐
        ▼           ▼            ▼

 Feature Engine  Historical    Replay Engine
                 Storage

        ▼
 Analytics Engine (FastAPI)

        │
        ├── Regime Detector
        ├── LSTM Predictor
        ├── XGBoost Predictor
        ├── MetaModel
        ├── Confidence Filter
        └── Trading Signal

        ▼
 Risk Engine

        ▼
 Portfolio Manager

        ▼
 Execution Engine

        ▼
 Notification Engine


Training Engine
        │
        ├── Walk Forward Validator
        ├── Feature Importance
        ├── Model Registry
        └── Model Promotion
```

---

# 3. Arquitectura del Motor de Datos

Responsabilidades:

* Mantener conexiones WebSocket persistentes.
* Recibir ticks en tiempo real.
* Agregar velas OHLCV.
* Publicar eventos.
* Persistir datos históricos.
* Permitir replay del mercado.

El Data Engine nunca realiza inferencia ni entrenamiento.

---

# Épica 1: Market Data Engine

---

## Historia de Usuario 8: Construcción Local de Velas (Candle Builder)

> Como motor de datos,
>
> Quiero transformar ticks en velas OHLCV de múltiples temporalidades,
>
> Para eliminar dependencias REST durante la inferencia.

### Criterios de aceptación

### Happy Path

BinanceWebSocketAdapter recibe ticks.

↓

BuildCandlesUseCase agrega:

* 1m
* 5m
* 15m
* 1h

↓

El MessageBus publica:

```text
candles:btcusdt:1m
candles:btcusdt:5m
candles:btcusdt:15m
candles:btcusdt:1h
```

Analytics Engine consume exclusivamente dichas velas.

---

### Sad Path

Si se detecta pérdida de ticks o inconsistencia temporal:

* la vela se marca como incompleta;
* HistoricalRecoveryAdapter intenta reconstruirla;
* si la recuperación falla, la vela se descarta;

sin detener el pipeline principal.

---

# Épica 2: Feature Engine

---

## Historia de Usuario 9: Publicación de Features Técnicas

> Como motor de datos,
>
> Quiero calcular y publicar indicadores técnicos,
>
> Para evitar recálculos repetitivos dentro del Analytics Engine.

### Features soportadas

* RSI
* EMA9
* EMA21
* EMA50
* MACD
* ATR
* ADX
* BBW
* OBV
* VolSMA
* PctChange

---

### Happy Path

Las velas ingresan al FeatureBuilder.

↓

Se calculan todas las features.

↓

El MessageBus publica:

```text
features:btcusdt:5m
features:ethusdt:5m
features:solusdt:5m
```

---

### Sad Path

Si una feature genera:

* NaN
* infinito
* división por cero

la muestra se invalida y se registra una alerta.

El sistema continúa procesando las muestras siguientes.

---

# Épica 3: Persistencia Histórica

---

## Historia de Usuario 10: Almacenamiento de Mercado

> Como sistema de entrenamiento,
>
> Quiero conservar los eventos históricos,
>
> Para permitir auditoría, replay y entrenamiento.

### Información almacenada

* ticks;
* velas;
* features;
* señales;
* órdenes;
* posiciones;
* métricas.

---

### Happy Path

El HistoricalStorageWriter recibe eventos y los almacena en:

* Parquet;
* DuckDB;
* TimescaleDB;

según la configuración activa.

---

### Sad Path

Si el almacenamiento falla:

* los eventos son enviados a un buffer temporal;
* se registran incidentes;

sin bloquear el MessageBus.

---

# Épica 4: Event Bus Enriquecido

---

## Historia de Usuario 11: Publicación de Eventos del Sistema

> Como arquitectura distribuida,
>
> Quiero utilizar un bus de eventos enriquecido,
>
> Para desacoplar completamente los servicios.

### Streams soportados

```text
ticks
candles
features
signals
orders
positions
notifications
metrics
```

---

### Happy Path

Cada servicio consume únicamente los streams necesarios.

No existen imports directos entre servicios.

---

### Sad Path

Si un consumidor deja de responder:

* los demás servicios continúan funcionando;
* los eventos permanecen almacenados;

evitando pérdidas de información.

---

# Épica 5: Replay Engine

---

## Historia de Usuario 12: Reproducción Histórica del Mercado

> Como investigador cuantitativo,
>
> Quiero reproducir sesiones históricas,
>
> Para ejecutar backtests idénticos al entorno LIVE.

---

### Happy Path

```text
Parquet

↓

Replay Engine

↓

MessageBus

↓

Analytics Engine
```

Los componentes trabajan exactamente igual que en producción.

---

### Sad Path

Si faltan fragmentos del histórico:

* la sesión es marcada como incompleta;
* el replay continúa con los datos restantes;
* se genera un reporte de consistencia.

---

# Invariantes Arquitectónicas

### #15

Data Engine nunca ejecuta modelos de IA.

---

### #16

Analytics Engine nunca realiza llamadas REST para obtener OHLCV.

---

### #17

Todos los servicios se comunican exclusivamente mediante MessageBus.

---

### #18

La caída de un consumidor jamás debe bloquear a los demás.

---

### #19

Replay Engine debe reutilizar exactamente el mismo pipeline del entorno LIVE.

---

### #20

La persistencia histórica no debe agregar latencia al flujo principal.

---

---

# PARTE II – DATASET Y FEATURE ENGINE

---

# 4. Arquitectura de Datos de ARGOS 2.0

ARGOS 2.0 utiliza un pipeline de datos desacoplado cuyo objetivo es transformar datos crudos provenientes del mercado en muestras listas para entrenamiento e inferencia.

La arquitectura busca:

* Reducir ruido.
* Incrementar la diversidad del dataset.
* Adaptarse a distintos regímenes de mercado.
* Facilitar el entrenamiento y la reproducibilidad.

```text
Historical Storage
        │
        ▼
Dataset Builder
        │
        ▼
Feature Engineering
        │
        ▼
Labeling Engine
        │
        ▼
Normalizer
        │
        ▼
Window Builder
        │
        ▼
Training Dataset
```

---

# Épica 6: Dataset Multi-Par

---

## Historia de Usuario 13: Construcción del Dataset Multi-Par

> Como investigador cuantitativo,
>
> Quiero consolidar múltiples activos en un único dataset,
>
> Para aumentar la diversidad y reducir el sobreajuste.

---

### Activos Soportados

* BTC/USDT
* ETH/USDT
* SOL/USDT

---

### Happy Path

Historical Storage proporciona los datos.

↓

DatasetBuilder combina los activos.

↓

Se agrega:

```python
symbol_id
```

↓

Se genera un único dataset.

---

### Sad Path

Si un activo presenta:

* datos insuficientes;
* discontinuidades temporales;
* datos corruptos;

el símbolo es excluido automáticamente.

Los demás activos continúan siendo procesados.

---

# Épica 7: Feature Engineering

---

## Historia de Usuario 14: Construcción de Features

> Como sistema analítico,
>
> Quiero enriquecer las velas OHLCV,
>
> Para proporcionar más contexto a los modelos.

---

### Features Soportadas

#### Momentum

* RSI
* ROC

#### Tendencia

* EMA9
* EMA21
* EMA50

#### Volatilidad

* ATR
* BBW

#### Fuerza del Mercado

* ADX

#### Volumen

* OBV
* Volume SMA

#### Variación

* Percent Change

---

### Happy Path

El FeatureBuilder recibe las velas.

↓

Calcula todas las variables.

↓

Publica:

```text
FeatureVector
```

---

### Sad Path

Si una feature produce:

* NaN;
* infinito;
* división por cero;

la muestra es invalidada.

El procesamiento continúa.

---

# Épica 8: Labeling Adaptativo

---

## Historia de Usuario 15: Etiquetado Dinámico mediante ATR

> Como sistema de entrenamiento,
>
> Quiero generar targets dinámicos,
>
> Para adaptarme a las condiciones de volatilidad.

---

### Clases

```text
BUY

SELL

HOLD
```

---

### Reglas

BUY

```python
future_return > 1.5 * ATR
```

SELL

```python
future_return < -1.5 * ATR
```

HOLD

En cualquier otro escenario.

---

### Happy Path

LabelingEngine calcula ATR.

↓

Genera las etiquetas.

↓

Produce las clases.

---

### Sad Path

Si el ATR no puede calcularse:

* la muestra es descartada;
* se registra una advertencia;

sin detener el proceso.

---

# Épica 9: Normalización

---

## Historia de Usuario 16: Escalado de Variables

> Como motor de entrenamiento,
>
> Quiero normalizar las features,
>
> Para mejorar la estabilidad del aprendizaje.

---

### Métodos Soportados

* StandardScaler.
* MinMaxScaler.
* RobustScaler.

---

### Happy Path

Las features son transformadas.

↓

Se almacenan los parámetros del scaler.

↓

Se garantiza consistencia entre entrenamiento e inferencia.

---

### Sad Path

Si la normalización falla:

el dataset es rechazado.

No se inicia el entrenamiento.

---

# Épica 10: Window Builder

---

## Historia de Usuario 17: Construcción de Secuencias

> Como modelo temporal,
>
> Quiero agrupar observaciones consecutivas,
>
> Para aprender patrones en el tiempo.

---

### Ventana por Defecto

```text
60 velas
```

---

### Happy Path

WindowBuilder transforma:

```text
OHLCV + Features
```

en:

```text
[60, n_features]
```

listas para la LSTM.

---

### Sad Path

Si una ventana está incompleta:

la secuencia es descartada.

---

# Épica 11: Feature Store

---

## Historia de Usuario 18: Persistencia de Features

> Como sistema,
>
> Quiero almacenar features calculadas,
>
> Para evitar recomputaciones.

---

### Happy Path

Las features son almacenadas junto con:

* timestamp;
* símbolo;
* timeframe.

---

### Sad Path

Si la persistencia falla:

el sistema continúa utilizando cálculo en tiempo real.

---

# Épica 12: Dataset Builder

---

## Historia de Usuario 19: Construcción del Dataset Final

> Como Training Engine,
>
> Quiero generar datasets reproducibles,
>
> Para garantizar experimentos consistentes.

---

### Pipeline

```text
Historical Storage

↓

Feature Builder

↓

Labeling Engine

↓

Normalizer

↓

Window Builder

↓

Training Dataset
```

---

### Happy Path

El dataset es construido exitosamente.

Se almacena:

* versión;
* features utilizadas;
* parámetros del scaler;
* configuración del labeling.

---

### Sad Path

Si alguna etapa falla:

el dataset es marcado como:

```text
INVALID_DATASET
```

y el entrenamiento es cancelado.

---

# Épica 13: Balanceo de Clases

---

## Historia de Usuario 20: Distribución Balanceada

> Como investigador cuantitativo,
>
> Quiero evitar sesgos en las clases,
>
> Para mejorar la capacidad de generalización.

---

### Objetivo

Reducir dominancia de:

```text
HOLD
```

sobre:

```text
BUY

SELL
```

---

### Métodos

* Weighted Loss.
* Oversampling.
* Undersampling.

---

### Happy Path

Las clases mantienen una distribución razonable.

---

### Sad Path

Si el desbalance supera el límite permitido:

el entrenamiento es rechazado.

---

# Invariantes Arquitectónicas

### #21

El dataset siempre debe ser reproducible.

---

### #22

Las mismas transformaciones utilizadas en entrenamiento deben ser utilizadas en inferencia.

---

### #23

Toda muestra inválida debe ser descartada.

---

### #24

La ausencia de ATR invalida el labeling.

---

### #25

Las ventanas incompletas nunca deben llegar a los modelos.

---

### #26

Las features deben permanecer desacopladas de los modelos.

---

### #27

Los datasets deben ser versionados.

---

### #28

El balance entre clases debe ser supervisado continuamente.

---

### #29

La corrupción de una muestra no debe detener la construcción del dataset completo.

---

---

# PARTE III – ARQUITECTURA DE IA Y MOTOR DE PREDICCIÓN

---

# 5. Arquitectura Analítica de ARGOS 2.0

ARGOS 2.0 evoluciona desde una arquitectura basada en un único modelo LSTM hacia un sistema de inferencia multicapa capaz de adaptarse dinámicamente a distintos regímenes de mercado.

La señal final no depende de un único predictor, sino de una cadena de componentes especializados.

```text
Market Context
      │
      ▼
Regime Detector
      │
      ▼
 ┌────────────┐
 │ LSTM Model │
 └────────────┘
      │
 ┌──────────────┐
 │ XGBoost Model│
 └──────────────┘
      │
      ▼
 MetaModel
      │
      ▼
 Probability Calibration
      │
      ▼
 Uncertainty Estimator
      │
      ▼
 Confidence Filter
      │
      ▼
 Trading Signal
```

---

# Épica 14: Dataset Multi-Par

---

## Historia de Usuario 21: Entrenamiento Multi-Par

> Como investigador cuantitativo,
>
> Quiero entrenar modelos utilizando múltiples pares,
>
> Para aumentar la diversidad de escenarios y reducir el sobreajuste.

---

### Activos soportados

* BTC/USDT
* ETH/USDT
* SOL/USDT

---

### Happy Path

El DatasetBuilder consolida todos los activos.

Se añade:

```python
symbol_id
```

Se genera un dataset único de entrenamiento.

---

### Sad Path

Si un activo presenta:

* datos insuficientes;
* discontinuidades temporales;
* corrupción de información;

el símbolo es excluido automáticamente.

El entrenamiento continúa con los demás pares.

---

# Épica 15: Labeling Dinámico por ATR

---

## Historia de Usuario 22: Etiquetado Adaptativo

> Como sistema de entrenamiento,
>
> Quiero utilizar ATR para generar los targets,
>
> Para adaptar las etiquetas a la volatilidad del mercado.

---

### Clases

```text
BUY
SELL
HOLD
```

---

### Reglas

BUY

```python
future_return > 1.5 * ATR
```

SELL

```python
future_return < -1.5 * ATR
```

HOLD

En cualquier otro escenario.

---

### Happy Path

El LabelingEngine calcula el ATR.

↓

Genera las etiquetas.

↓

Produce un dataset balanceado.

---

### Sad Path

Si el ATR es inválido:

* la muestra se descarta;
* se registra una advertencia;

sin detener el entrenamiento.

---

# Épica 16: Detector de Régimen

---

## Historia de Usuario 23: Clasificación del Mercado

> Como motor de inferencia,
>
> Quiero identificar el contexto del mercado,
>
> Para adaptar dinámicamente la estrategia.

---

### Features utilizadas

* ADX
* BBW
* ATR
* EMA Slope

---

### Estados posibles

```text
TRENDING

RANGING

HIGH_VOLATILITY

LOW_VOLATILITY

UNKNOWN
```

---

### Happy Path

El RegimeDetector produce:

```python
MarketContext
```

La clasificación se adjunta a la inferencia.

Posteriormente se seleccionan:

* modelo;
* threshold;
* parámetros de riesgo.

---

### Sad Path

Si las features son inválidas:

```text
UNKNOWN
```

↓

El sistema produce automáticamente:

```text
HOLD
```

---

# Épica 17: Predictor Modular

---

## Historia de Usuario 24: Separación del Pipeline de Inferencia

> Como arquitecto del sistema,
>
> Quiero desacoplar las etapas del predictor,
>
> Para facilitar mantenimiento y pruebas.

---

### Pipeline

```text
MarketDataProvider

↓

FeatureNormalizer

↓

WindowBuilder

↓

ModelPredictor

↓

SignalPostProcessor

↓

TradingSignal
```

---

### Happy Path

Cada componente trabaja independientemente.

---

### Sad Path

Si una etapa falla:

* la excepción es contenida;
* se registra el incidente;

sin derribar el Analytics Engine.

---

# Épica 18: Ensemble Predictivo

---

## Historia de Usuario 25: LSTM + XGBoost

> Como motor analítico,
>
> Quiero combinar modelos temporales y tabulares,
>
> Para reducir falsos positivos.

---

### Arquitectura

```text
Ventana temporal
      │
      ▼
 LSTM Predictor

Última observación
      │
      ▼
 XGBoost Predictor
```

---

### Happy Path

Ambos modelos generan probabilidades independientes.

El EnsembleCoordinator combina ambas salidas.

---

### Sad Path

Si uno de los modelos falla:

* se entra en modo degradado;
* el predictor restante continúa funcionando.

Si ambos fallan:

```text
TradingSignal(HOLD)
```

y se genera una alerta crítica.

---

# Épica 19: Ensemble Real mediante Stacking

---

## Historia de Usuario 26: MetaModel

> Como motor de decisión,
>
> Quiero utilizar un modelo superior,
>
> Para combinar diferentes fuentes de información.

---

### Inputs

* Probabilidad LSTM.
* Probabilidad XGBoost.
* ADX.
* BBW.
* ATR.
* RSI.
* Volumen.

---

### Output

```text
BUY

SELL

HOLD
```

---

### Happy Path

El MetaModel genera una única probabilidad final.

---

### Sad Path

Si MetaModel no está disponible:

el sistema utiliza las probabilidades del ensemble base.

---

# Épica 20: Calibración de Probabilidades

---

## Historia de Usuario 27: Probability Calibration

> Como sistema de decisión,
>
> Quiero que las probabilidades sean estadísticamente consistentes,
>
> Para evitar exceso de confianza.

---

### Métodos soportados

* Platt Scaling.
* Isotonic Regression.

---

### Happy Path

Una probabilidad:

```text
0.80
```

representa aproximadamente un 80% de éxito esperado.

---

### Sad Path

Si la calibración falla:

se utilizan las probabilidades originales y se registra una advertencia.

---

# Épica 21: Predicción de Incertidumbre

---

## Historia de Usuario 28: Uncertainty Estimation

> Como motor de IA,
>
> Quiero estimar la incertidumbre,
>
> Para evitar operaciones ambiguas.

---

### Métodos

* Monte Carlo Dropout.
* Deep Ensembles.

---

### Resultado

```python
PredictionResult(
    probability=0.83,
    uncertainty=0.11
)
```

---

### Happy Path

La incertidumbre permanece por debajo del umbral permitido.

La señal continúa.

---

### Sad Path

Si:

```python
uncertainty > max_uncertainty
```

la operación es descartada.

---

# Épica 22: Confidence Filter

---

## Historia de Usuario 29: Filtrado Final de Señales

> Como sistema de ejecución,
>
> Quiero validar la confianza antes de operar,
>
> Para reducir falsos positivos.

---

### Variables evaluadas

* probabilidad final;
* incertidumbre;
* régimen de mercado;
* threshold adaptativo.

---

### Happy Path

Si:

```python
probability >= threshold
```

y

```python
uncertainty <= max_uncertainty
```

se produce:

```text
BUY
```

o

```text
SELL
```

---

### Sad Path

Si alguna condición falla:

```text
HOLD
```

---

# Invariantes Arquitectónicas

### #30

Analytics Engine nunca depende de un único modelo.

---

### #31

Una falla parcial de un predictor no debe detener el sistema.

---

### #32

La ausencia de contexto de mercado obliga a producir HOLD.

---

### #33

Toda señal debe atravesar el Confidence Filter.

---

### #34

La incertidumbre tiene prioridad sobre la probabilidad.

---

### #35

La salida final siempre pertenece al conjunto:

```text
BUY
SELL
HOLD
```

---

### #36

Ningún predictor individual puede ejecutar operaciones.

La decisión final pertenece exclusivamente al:

```text
MetaModel
+
Confidence Filter
```

---

### #37

La degradación controlada tiene prioridad sobre la interrupción completa del Analytics Engine.

---

---

# PARTE IV – GESTIÓN DE POSICIONES, RIESGO Y PORTAFOLIO

---

# 6. Arquitectura de Riesgo de ARGOS 2.0

ARGOS 2.0 separa completamente la predicción de la administración del capital.

La rentabilidad del sistema no depende únicamente de la precisión de los modelos, sino de la capacidad para controlar pérdidas, exposición y correlación entre activos.

```text
Trading Signal
      │
      ▼
Position Manager
      │
      ▼
Risk Engine
      │
      ▼
Portfolio Manager
      │
      ▼
Execution Engine
```

---

# Épica 23: Position Management Engine

---

## Historia de Usuario 30: Gestión Inteligente de Posiciones

> Como sistema de ejecución,
>
> Quiero administrar dinámicamente las operaciones abiertas,
>
> Para maximizar beneficios y limitar pérdidas.

---

### Capacidades

* Stop Loss dinámico.
* Break Even automático.
* Trailing Stop.
* Take Profit parcial.
* Take Profit múltiple.

---

### Happy Path

La posición se abre.

↓

Se establece:

```python
SL = 1.5 × ATR
```

Cuando el beneficio alcanza:

```python
1R
```

↓

El SL se mueve a Break Even.

Cuando alcanza:

```python
2R
```

↓

Se activa el Trailing Stop.

---

### Sad Path

Si ocurre:

* pérdida de conexión;
* fallo del exchange;
* reinicio del sistema;

las posiciones son reconstruidas desde el PositionRepository.

---

# Épica 24: Salidas Parciales

---

## Historia de Usuario 31: Gestión Escalonada de Beneficios

> Como sistema de trading,
>
> Quiero cerrar posiciones parcialmente,
>
> Para asegurar beneficios y reducir exposición.

---

### Configuración

```text
50% → TP1

25% → TP2

25% → Trailing Stop
```

---

### Happy Path

La posición alcanza los objetivos progresivamente.

Los beneficios son asegurados.

---

### Sad Path

Si uno de los cierres parciales falla:

* se reintenta automáticamente;
* se registra el incidente;
* el resto de la posición permanece protegida.

---

# Épica 25: Risk Engine

---

## Historia de Usuario 32: Motor de Riesgo Desacoplado

> Como arquitectura del sistema,
>
> Quiero aislar las reglas de riesgo,
>
> Para evitar que la lógica de predicción controle directamente el capital.

---

### Variables evaluadas

* Daily Drawdown.
* Max Drawdown.
* Riesgo por operación.
* Riesgo por símbolo.
* Número máximo de posiciones.
* Exposición total.
* Pérdidas consecutivas.

---

### Happy Path

Cada nueva operación es validada por el Risk Engine.

Solo las operaciones aprobadas llegan al Execution Engine.

---

### Sad Path

Si alguna regla es violada:

```text
TRADE_REJECTED
```

La operación es cancelada.

---

# Épica 26: Drawdown Protection

---

## Historia de Usuario 33: Protección ante Pérdidas Acumuladas

> Como sistema,
>
> Quiero limitar las pérdidas,
>
> Para preservar el capital.

---

### Reglas

Daily Drawdown:

```text
3%
```

Max Drawdown:

```text
10%
```

---

### Happy Path

El sistema continúa operando normalmente.

---

### Sad Path

Si el límite es superado:

```text
TRADING_DISABLED
```

Todas las nuevas entradas quedan bloqueadas.

---

# Épica 27: Circuit Breaker

---

## Historia de Usuario 34: Detención Automática del Sistema

> Como sistema de protección,
>
> Quiero detener la operativa en condiciones anómalas,
>
> Para evitar pérdidas catastróficas.

---

### Eventos monitoreados

* Múltiples pérdidas consecutivas.
* Volatilidad extrema.
* Fallos del exchange.
* Fallos de conectividad.
* Fallos de inferencia.

---

### Happy Path

Los eventos permanecen dentro de los límites establecidos.

---

### Sad Path

Si los límites son superados:

```text
SYSTEM_PAUSED
```

Se suspenden nuevas operaciones.

---

# Épica 28: Portfolio Manager

---

## Historia de Usuario 35: Gestión del Riesgo de Cartera

> Como sistema,
>
> Quiero evaluar la cartera como un conjunto,
>
> Para evitar concentraciones excesivas.

---

### Activos soportados

* BTC
* ETH
* SOL

---

### Happy Path

El Portfolio Manager calcula:

* exposición total;
* correlaciones;
* peso relativo.

La asignación permanece dentro de los límites.

---

### Sad Path

Si la exposición supera el máximo permitido:

```text
EXPOSURE_LIMIT_REACHED
```

Nuevas operaciones son rechazadas.

---

# Épica 29: Correlation Engine

---

## Historia de Usuario 36: Detección de Correlación

> Como sistema,
>
> Quiero medir la correlación entre activos,
>
> Para evitar duplicar riesgos.

---

### Happy Path

La correlación es aceptable.

```python
correlation < 0.80
```

Las operaciones continúan.

---

### Sad Path

Si:

```python
correlation >= 0.80
```

el tamaño de posición es reducido.

Si:

```python
correlation >= 0.95
```

la operación es rechazada.

---

# Épica 30: Position Sizing

---

## Historia de Usuario 37: Tamaño Dinámico de Posición

> Como sistema,
>
> Quiero ajustar el tamaño de las operaciones,
>
> Para mantener constante el riesgo.

---

### Variables utilizadas

* ATR.
* Volatilidad.
* Confianza del modelo.
* Drawdown actual.

---

### Happy Path

El tamaño es calculado automáticamente.

```python
risk_per_trade = 1%
```

---

### Sad Path

Si no es posible calcular el riesgo:

la posición no es abierta.

---

# Épica 31: Portfolio Heat

---

## Historia de Usuario 38: Exposición Global

> Como sistema,
>
> Quiero limitar la exposición total,
>
> Para evitar un riesgo agregado excesivo.

---

### Límite

```text
5%
```

del capital total.

---

### Happy Path

La suma de todas las posiciones permanece dentro del límite.

---

### Sad Path

Si el límite es superado:

```text
PORTFOLIO_HEAT_LIMIT
```

no se permiten nuevas entradas.

---

# Épica 32: Execution Engine

---

## Historia de Usuario 39: Ejecución Segura

> Como sistema,
>
> Quiero desacoplar la ejecución del análisis,
>
> Para garantizar resiliencia.

---

### Happy Path

```text
Signal

↓

Risk Engine

↓

Portfolio Manager

↓

Execution Engine

↓

Exchange
```

---

### Sad Path

Si el exchange rechaza una orden:

* se registra el evento;
* se reintenta si es seguro;
* se notifica al sistema de monitoreo.

---

# Invariantes Arquitectónicas

### #38

Los modelos de IA nunca administran directamente el capital.

---

### #39

Toda operación debe ser aprobada por el Risk Engine.

---

### #40

El Execution Engine jamás recibe señales directamente desde los predictores.

---

### #41

El Position Manager es responsable exclusivo de las posiciones abiertas.

---

### #42

La protección del capital tiene prioridad sobre la maximización del beneficio.

---

### #43

La exposición total de la cartera siempre está limitada.

---

### #44

Una condición de emergencia puede detener completamente la operativa.

---

### #45

El Portfolio Manager evalúa el riesgo agregado y no únicamente cada activo individual.

---

### #46

La ausencia de información crítica implica:

```text
NO TRADE
```

---

### #47

La degradación controlada tiene prioridad sobre el fallo total del sistema.

---

---

# PARTE V – TRAINING ENGINE Y CICLO DE VIDA DE LOS MODELOS

---

# 7. Arquitectura de Entrenamiento de ARGOS 2.0

ARGOS 2.0 desacopla completamente la inferencia del entrenamiento.

El Analytics Engine tiene como única responsabilidad realizar predicciones en tiempo real, mientras que el Training Engine se encarga del ciclo de vida completo de los modelos.

```text
Historical Storage
        │
        ▼
 Dataset Builder
        │
        ▼
 Feature Engineering
        │
        ▼
 Labeling Engine
        │
        ▼
 Trainer
        │
        ▼
 Walk Forward Validator
        │
        ▼
 Feature Importance
        │
        ▼
 Model Registry
        │
        ▼
 Promotion Engine
```

---

# Épica 33: Training Engine

---

## Historia de Usuario 40: Entrenamiento Desacoplado

> Como investigador cuantitativo,
>
> Quiero separar entrenamiento e inferencia,
>
> Para evitar que los procesos de entrenamiento afecten el rendimiento del sistema live.

---

### Happy Path

El Training Engine:

* obtiene datos históricos;
* construye datasets;
* entrena modelos;
* valida resultados;
* registra métricas.

Todo ocurre sin interferir con el Analytics Engine.

---

### Sad Path

Si ocurre una excepción:

* el entrenamiento se cancela;
* el modelo vigente permanece activo;
* se registra el incidente.

---

# Épica 34: Feature Importance

---

## Historia de Usuario 41: Interpretabilidad del Modelo

> Como investigador,
>
> Quiero conocer qué variables aportan más información,
>
> Para eliminar ruido y mejorar el sistema.

---

### Métodos soportados

* SHAP.
* Gain Importance.
* Permutation Importance.

---

### Happy Path

El sistema genera:

```text
ATR      18%
ADX      14%
RSI      10%
MACD      7%
OBV       5%
```

Los resultados son almacenados.

---

### Sad Path

Si el cálculo falla:

* las importancias anteriores son conservadas;
* el entrenamiento continúa.

---

# Épica 35: Walk Forward Validation

---

## Historia de Usuario 42: Validación Temporal

> Como investigador cuantitativo,
>
> Quiero evaluar los modelos utilizando ventanas deslizantes,
>
> Para evitar sobreajuste.

---

### Pipeline

```text
Train

↓

Validation

↓

Test

↓

Advance Window
```

---

### Métricas calculadas

* Sharpe Ratio.
* Profit Factor.
* Win Rate.
* Maximum Drawdown.
* Precision.
* Recall.
* F1 Score.

---

### Happy Path

Todas las ventanas cumplen los criterios mínimos.

El modelo es marcado como:

```text
VALIDATED
```

---

### Sad Path

Si alguna ventana falla:

```text
REJECTED
```

El modelo no puede ser promovido.

---

# Épica 36: Walk Forward Trainer

---

## Historia de Usuario 43: Reentrenamiento Automático

> Como sistema,
>
> Quiero reentrenar periódicamente los modelos,
>
> Para adaptarme a nuevos regímenes de mercado.

---

### Happy Path

El Scheduler ejecuta:

```text
Descargar datos

↓

Construir dataset

↓

Entrenar

↓

Validar

↓

Comparar

↓

Promover o rechazar
```

---

### Frecuencia recomendada

* Semanal.
* Quincenal.

---

### Sad Path

Si las métricas son inferiores:

el entrenamiento es descartado.

---

# Épica 37: Model Registry

---

## Historia de Usuario 44: Versionado de Modelos

> Como sistema,
>
> Quiero mantener múltiples versiones,
>
> Para permitir promociones y rollbacks seguros.

---

### Ejemplo

```text
v1.0

v1.1

v1.2

v2.0
```

---

### Información almacenada

* Fecha de entrenamiento.
* Dataset utilizado.
* Features utilizadas.
* Métricas.
* Hiperparámetros.

---

### Happy Path

La nueva versión se registra correctamente.

---

### Sad Path

Si ocurre un error:

el modelo actual permanece activo.

---

# Épica 38: Promotion Engine

---

## Historia de Usuario 45: Promoción Automática

> Como sistema,
>
> Quiero promover únicamente modelos superiores,
>
> Para garantizar estabilidad.

---

### Happy Path

Si:

```text
Sharpe Ratio ↑

Profit Factor ↑

Max Drawdown ↓
```

el modelo es promovido.

---

### Sad Path

Si las métricas son inferiores:

```text
PROMOTION_DENIED
```

---

# Épica 39: Rollback Engine

---

## Historia de Usuario 46: Recuperación Automática

> Como sistema,
>
> Quiero regresar a una versión estable,
>
> Para evitar degradaciones.

---

### Happy Path

El sistema detecta degradación.

↓

Activa:

```text
ROLLBACK
```

↓

Restaura el último modelo estable.

---

### Sad Path

Si el rollback falla:

se activa el modo seguro.

---

# Épica 40: Champion vs Challenger

---

## Historia de Usuario 47: Competencia entre Modelos

> Como sistema,
>
> Quiero comparar modelos candidatos,
>
> Para seleccionar al mejor.

---

### Arquitectura

```text
Champion

vs

Challenger
```

---

### Happy Path

El Challenger supera al Champion.

↓

Promotion Engine.

---

### Sad Path

Si no lo supera:

el Champion permanece activo.

---

# Épica 41: Shadow Models

---

## Historia de Usuario 48: Evaluación Pasiva

> Como sistema,
>
> Quiero ejecutar modelos experimentales en paralelo,
>
> Para medir su desempeño sin arriesgar capital.

---

### Happy Path

El Shadow Model recibe los mismos datos.

Sus resultados son almacenados.

No ejecuta órdenes.

---

### Sad Path

Si falla:

el sistema principal continúa funcionando.

---

# Épica 42: Replay Learning

---

## Historia de Usuario 49: Reentrenamiento Mediante Replay

> Como investigador cuantitativo,
>
> Quiero reutilizar sesiones históricas,
>
> Para evaluar estrategias en condiciones idénticas al entorno live.

---

### Pipeline

```text
Historical Storage

↓

Replay Engine

↓

Training Engine

↓

Validator
```

---

### Happy Path

La simulación reproduce fielmente el mercado.

---

### Sad Path

Si el histórico está incompleto:

la sesión es marcada como:

```text
PARTIAL
```

---

# Épica 43: Auto-Retraining

---

## Historia de Usuario 50: Adaptación Continua

> Como sistema,
>
> Quiero actualizar automáticamente los modelos,
>
> Para responder a cambios estructurales del mercado.

---

### Disparadores

* tiempo;
* degradación;
* cambio de volatilidad;
* cambio de régimen.

---

### Happy Path

Se genera una nueva versión del modelo.

---

### Sad Path

Si el nuevo modelo es inferior:

el modelo vigente permanece activo.

---

# Invariantes Arquitectónicas

### #48

Entrenamiento e inferencia son procesos completamente independientes.

---

### #49

Un modelo nunca puede reemplazar al vigente sin validación.

---

### #50

Todo modelo debe pasar Walk Forward Validation.

---

### #51

Los modelos rechazados nunca pueden ser promovidos.

---

### #52

La degradación de un modelo debe ser reversible.

---

### #53

El sistema siempre debe ser capaz de volver a una versión estable.

---

### #54

Los Shadow Models jamás ejecutan operaciones.

---

### #55

La promoción automática tiene prioridad sobre la intervención manual.

---

### #56

La seguridad y estabilidad tienen prioridad sobre la frecuencia de reentrenamiento.

---

### #57

Todo modelo debe ser completamente reproducible.

---

---

# PARTE VI – OBSERVABILIDAD, MONITOREO Y EVOLUCIÓN DEL SISTEMA

---

# 8. Observabilidad y Operación de ARGOS 2.0

ARGOS 2.0 debe ser capaz de observarse a sí mismo.

Todos los componentes deben emitir eventos, métricas y registros que permitan:

* detectar anomalías;
* reconstruir incidentes;
* medir rendimiento;
* auditar decisiones;
* facilitar la evolución del sistema.

---

# Arquitectura de Observabilidad

```text
Analytics Engine
Risk Engine
Execution Engine
Training Engine
Portfolio Engine
Data Engine
       │
       ▼
Telemetry Engine
       │
       ├── Metrics
       ├── Logs
       ├── Alerts
       └── Dashboards
```

---

# Épica 44: Telemetry Engine

---

## Historia de Usuario 51: Recolección de Métricas

> Como sistema,
>
> Quiero recopilar información operativa,
>
> Para supervisar el comportamiento de ARGOS.

---

### Métricas monitoreadas

#### Data Engine

* ticks recibidos;
* latencia;
* pérdida de mensajes;
* reconexiones.

#### Analytics Engine

* tiempo de inferencia;
* señales generadas;
* incertidumbre media;
* confianza promedio.

#### Execution Engine

* órdenes enviadas;
* órdenes rechazadas;
* slippage.

#### Training Engine

* duración del entrenamiento;
* métricas obtenidas;
* promociones realizadas.

---

### Happy Path

Las métricas son enviadas periódicamente al sistema de monitoreo.

---

### Sad Path

Si la telemetría falla:

el sistema principal continúa funcionando.

---

# Épica 45: Logging Centralizado

---

## Historia de Usuario 52: Registro de Eventos

> Como operador,
>
> Quiero disponer de registros centralizados,
>
> Para analizar incidentes.

---

### Niveles

```text
DEBUG

INFO

WARNING

ERROR

CRITICAL
```

---

### Happy Path

Todos los eventos relevantes son almacenados.

---

### Sad Path

Si el almacenamiento falla:

los logs son enviados a un buffer temporal.

---

# Épica 46: Notification Engine

---

## Historia de Usuario 53: Notificaciones del Sistema

> Como operador,
>
> Quiero recibir alertas importantes,
>
> Para actuar rápidamente.

---

### Eventos notificables

* nuevas posiciones;
* cierres;
* drawdown máximo;
* promoción de modelos;
* rollback;
* fallos críticos;
* desconexión del exchange.

---

### Happy Path

Las alertas son enviadas correctamente.

---

### Sad Path

Si un canal falla:

los demás canales continúan funcionando.

---

# Épica 47: Dashboard Engine

---

## Historia de Usuario 54: Panel de Supervisión

> Como operador,
>
> Quiero visualizar el estado del sistema,
>
> Para monitorear su funcionamiento.

---

### Información disponible

#### Mercado

* activos monitoreados;
* volatilidad;
* régimen detectado.

#### IA

* probabilidades;
* incertidumbre;
* modelo activo.

#### Riesgo

* drawdown;
* exposición;
* portfolio heat.

#### Entrenamiento

* champion actual;
* challenger;
* métricas históricas.

---

### Happy Path

Toda la información se encuentra disponible en tiempo real.

---

### Sad Path

Si una fuente falla:

el resto del dashboard continúa operativo.

---

# Épica 48: Disaster Recovery

---

## Historia de Usuario 55: Recuperación ante Fallos

> Como sistema,
>
> Quiero recuperarme automáticamente,
>
> Para minimizar interrupciones.

---

### Eventos contemplados

* reinicio inesperado;
* caída del exchange;
* caída del Message Bus;
* fallo de inferencia;
* corrupción del histórico.

---

### Happy Path

El sistema recupera:

* posiciones;
* modelos;
* métricas;
* contexto operativo.

---

### Sad Path

Si la recuperación no es posible:

se activa:

```text
SAFE_MODE
```

y se bloquean nuevas operaciones.

---

# Épica 49: Requisitos No Funcionales

---

## Historia de Usuario 56: Escalabilidad

> Como arquitecto,
>
> Quiero que ARGOS sea escalable,
>
> Para soportar crecimiento futuro.

---

### Objetivos

* múltiples activos;
* múltiples exchanges;
* múltiples estrategias;
* múltiples modelos.

---

---

## Historia de Usuario 57: Alta Disponibilidad

### Objetivo

Garantizar continuidad operativa.

---

### Requisitos

* reconexión automática;
* degradación controlada;
* recuperación automática.

---

---

## Historia de Usuario 58: Rendimiento

### Objetivos

Tiempo máximo de inferencia:

```text
<100 ms
```

Tiempo máximo de ejecución:

```text
<250 ms
```

---

---

## Historia de Usuario 59: Seguridad

### Requisitos

* secretos cifrados;
* rotación de credenciales;
* separación de permisos;
* auditoría completa.

---

# Épica 50: Roadmap de Implementación

---

## Fase 1

Infraestructura Base

```text
Data Engine
Feature Engine
Historical Storage
Replay Engine
```

---

## Fase 2

Motor Analítico

```text
Dataset Multi-Par
ATR Labeling
LSTM
XGBoost
Regime Detector
```

---

## Fase 3

Ensemble Avanzado

```text
MetaModel
Calibration
Uncertainty Estimation
Confidence Filter
```

---

## Fase 4

Gestión Financiera

```text
Position Manager
Risk Engine
Portfolio Manager
```

---

## Fase 5

Entrenamiento Continuo

```text
Walk Forward Validation
Feature Importance
Model Registry
Promotion Engine
Shadow Models
```

---

## Fase 6

Observabilidad

```text
Telemetry
Dashboard
Notifications
Disaster Recovery
```

---

# Invariantes Globales

### #58

La protección del capital tiene prioridad sobre la rentabilidad.

---

### #59

La degradación controlada tiene prioridad sobre la interrupción total.

---

### #60

Todo componente debe ser reemplazable.

---

### #61

Todo modelo debe ser reproducible.

---

### #62

Toda señal debe ser auditable.

---

### #63

La inferencia nunca debe depender del entrenamiento.

---

### #64

Los fallos parciales nunca deben detener el sistema completo.

---

### #65

Todo evento importante debe ser observable.

---

### #66

Todo cambio de modelo debe ser reversible.

---

### #67

La arquitectura debe permanecer desacoplada.

---

### #68

La ausencia de información crítica implica:

```text
NO TRADE
```

---

### #69

La preservación del capital es el objetivo supremo del sistema.

---

# VISIÓN ARGOS 3.0

ARGOS 2.0 constituye la base para futuras capacidades:

* Multi-Exchange.
* Multi-Estrategia.
* Reinforcement Learning.
* AutoML.
* Federated Learning.
* Adaptive Portfolio Allocation.
* Meta-Risk Engine.
* Hierarchical Ensembles.
* Distributed Training.

---

# FIN DEL DOCUMENTO

SPEC V5.0

ARGOS 2.0

Adaptive Multi-Model Quantitative Trading Platform
