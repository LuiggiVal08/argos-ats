# DOCUMENTO DE ESPECIFICACIÓN TÉCNICA (SPEC V4.1)

## PROYECTO: SISTEMA DE TRADING AUTÓNOMO (ATS) DE GRADO DE PRODUCCIÓNOB
---

## 1. Arquitectura del Sistema e Infraestructura

El sistema se diseña bajo una arquitectura de microservicios orientada a eventos, **agnóstica al modelo de deployment** (contenedores Docker, bare metal en Linux/WSL2/macOS/Windows nativo son todos soportados de primera clase), aislando el procesamiento de alta concurrencia I/O del cómputo intensivo de datos, la IA y el motor de riesgo coercitivo.

### 1.0 Principios de Agnosticismo de Infraestructura

* **Agnosticismo de broker:** el código de aplicación no se acopla a un broker específico. Usa un `MessageBus` port (interface). El adapter concreto (`RedisProtocolBus` u otro) implementa contra cualquier broker compatible con el protocolo RESP. Esto cubre: Redis 7+, Memurai, Dragonfly, Valkey, KeyDB, Garnet, Redict. Cambiar de broker no requiere reescribir lógica de negocio.
* **Agnosticismo de deployment:** `docker-compose.yml` es una de las opciones soportadas, no la única. El stack puede correr bare metal con `node` y `python` nativos, con cualquier broker RESP instalado a nivel de sistema operativo. La elección entre Docker y bare metal es operacional, no arquitectónica.
* **Agnosticismo de OS:** paths via `path.join` (Node) / `os.path.join` (Python); entry points via `npm run` cross-platform; `.gitattributes` normaliza line endings; PowerShell provisto solo donde aporta valor real.
* **Agnosticismo de exchange:** el adaptador de conectividad al exchange es un `ExchangeGateway` port. Cambiar de Binance a Bybit/OKX/etc. es swap de adapter, no reescritura de caso de uso.

### 1.1 Diagrama de Flujo de Datos e Infraestructura

```
[ Exchange WebSockets ] 
        │ (Mensajes Ticks / Libro de Órdenes en tiempo real)
        ▼
 ┌────────────────────────────────────────────────────────┐
 │ NestJS Container (Motor de Conectividad)               │
 │  └─► Infraestructura: WebSockets Gateways (Binance/etc)│
 │  └─► Aplicación: Sanitizar e Inyectar Ticks            │
 └──────┬─────────────────────────────────────────────────┘
        │ 
        ▼ (Inyección en memoria < 2ms)
 ┌──────────────┐
 │ Redis Stream │ (Broker de Mensajería / Buffer de Contención)
 └──────┬───────┘
        │
        ▼ (Consumo Asíncrono No Bloqueante)      
 ┌────────────────────────────────────────┐       
 │ FastAPI Container (Motor Analítico/IA) │       
 │  └─► Infraestructura: Redis Consumers   │       
 │  └─► Aplicación: ProcesarSeñalIA       │       
 │  └─► Dominio: Modelos IA, Reglas Core  │              
 └──────┬─────────────────────────────────┘              
        │                                         
        ▼ (Validación de Capital, ATR, SL/TP, Circuit Breaker)
 [ Exchange REST API (Vía CCXT) ]
        │
        ▼ (Fase Final Postergada)
 ┌────────────────────────────────────────────────────────┐
 │ Módulo de Telemetría (Fase Final / Sujeto a Revisión)  │
 │  └─► Monitoreo de Estados (Webhooks / Posible Front)   │
 └────────────────────────────────────────────────────────┘

```

### 1.2 Stack Tecnológico, Frameworks y Gobierno de Código

| Contenedor / Servicio | Tecnología Base | Framework Core | Librerías Críticas | Propósito y Gobierno |
| --- | --- | --- | --- | --- |
| **Data Engine** | Node.js v20-alpine | **NestJS** (TypeScript) | `ws`, `ioredis`, `@nestjs/microservices` | Mantenimiento de conexiones persistentes con el Exchange. Código modularizado con inyección de dependencias y tipado estricto. |
| **Analytics & IA Engine** | Python 3.11-slim | **FastAPI** + `asyncio` | `ta`, `pandas`, `numpy`, `tensorflow`/`pytorch` | Ingesta asíncrona, procesamiento del DataFrame y ejecución de modelos predictivos (LSTM/Transformers) sin bloquear hilos de cómputo. |
| **Message Broker** | Cualquier implementación RESP-compatibile (Redis 7+, Memurai, Dragonfly, Valkey, KeyDB, Garnet, Redict) | RESP | Streams / Pub-Sub | Buffer inter-proceso ultrarrápido en memoria para mitigar picos de latencia. La elección del broker concreto es operacional, no arquitectónica: el código consume el `MessageBus` port. |
| **Execution & Risk** | Python (Módulo Interno) | Integrado en FastAPI | `ccxt` (CryptoCurrency eXchange Trading) | Abstracción unificada para interactuar con múltiples exchanges, gestión de órdenes de mercado y reintentos. |
| **Telemetría y UI** | *Por determinar* | *En revisión* | *Postergado* | **Última fase de desarrollo.** Se evaluará si se implementa mediante webhooks asíncronos o se integra un panel Frontend dedicado. |

### 1.3 Patrón de Diseño Interno: Arquitectura Hexagonal (Clean Architecture)

Para asegurar que las reglas del trading y la IA no queden amarradas a un proveedor o exchange específico, ambos microservicios se estructuran internamente en 3 capas estrictas y aisladas:

* **Capa de Dominio (El Core Agnóstico):** Aloja las reglas de negocio puras que nunca cambian si se migra de infraestructura.
* *En Python:* Los pesos y lógicas de los modelos de IA, las fórmulas matemáticas de las estrategias financieras (tendencia/reversión) y las reglas del circuito de parada de emergencia (*Circuit Breaker*).
* *En Node.js:* Las entidades de las órdenes de trading, las validaciones de riesgo previas y las estructuras base (interfaces) de las velas de mercado.


* **Capa de Aplicación (Casos de Uso):** Orquesta el flujo de datos y define las acciones del sistema. Ejemplos: `EjecutarCompraCasoUso`, `CalcularMétricasVelaCasoUso`, `ProcesarSeñalIACasoUso`.
* **Capa de Infraestructura (Adaptadores Exteriores Volátiles):** Contiene las implementaciones técnicas propensas a cambiar. Si el exchange o la base de datos cambian, **solo** se modifica esta capa.
* *Adaptadores NestJS:* El cliente WebSocket que conecta a Binance/Bybit, el cliente inyector de Redis Streams y los controladores para futuras conexiones del Gateway.
* *Adaptadores FastAPI:* La librería `ta`, el entorno de ejecución de TensorFlow/PyTorch y el script consumidor que extrae los datos de Redis en memoria.



---

## 2. Motor de Indicadores Técnicos (Librería `ta` en Python)

A través de la capa de infraestructura (adaptador de Redis), FastAPI recibe los ticks, actualiza un DataFrame de Pandas y ejecuta los indicadores de forma vectorizada:

```python
import ta
import pandas as pd

def calcular_indicadores_tecnicos(df: pd.DataFrame) -> pd.DataFrame:
    # A. EMAs (Tendencia)
    df['ema_12'] = ta.trend.ema_indicator(close=df['close'], window=12)
    df['ema_26'] = ta.trend.ema_indicator(close=df['close'], window=26)
    
    # B. MACD (Impulso)
    df['macd'] = ta.trend.macd(close=df['close'], window_fast=12, window_slow=26)
    df['macd_signal'] = ta.trend.macd_signal(close=df['close'], window_fast=12, window_slow=26, window_sign=9)
    df['macd_diff'] = ta.trend.macd_diff(close=df['close'], window_fast=12, window_slow=26, window_sign=9)
    
    # C. RSI (Fuerza)
    df['rsi'] = ta.momentum.rsi(close=df['close'], window=14)
    
    # D. ATR (Volatilidad - Requerido por el Motor de Riesgo)
    df['atr'] = ta.volatility.average_true_range(high=df['high'], low=df['low'], close=df['close'], window=14)
    
    return df

```

---

## 3. Lógica de Decisiones y Estado del Mercado

El núcleo del Dominio evalúa el estado del DataFrame para conmutar la estrategia:

* **Mercado en Tendencia Fuerte (Estrategia de Tendencia):** Activado ante una separación expansiva entre `ema_12` y `ema_26` junto a un histograma de MACD (`macd_diff`) creciente. El ATS busca incorporarse al movimiento a favor de la tendencia macro.
* **Mercado en Rango / Lateralización (Estrategia de Reversión):** Activado si las EMAs se cruzan constantemente de forma plana. El control pasa al RSI: compra en sobreventa extrema ($<30$) y vende en sobrecompra extrema ($>70$).

---

## 4. Gestión de Seguridad y Protocolo ante Incidentes (OWASP)

* **Protección de Datos:** Las credenciales de API y firmas privadas de los exchanges se inyectan en runtime como variables de entorno seguras (`.env` o variables del sistema operativo en deployments bare metal). Está estrictamente prohibido su almacenamiento en duro (*hardcode*) en el código fuente.
* **Estrategia de Reacción (4 Fases de OWASP):**
1. **Identificación:** Monitoreo automatizado con logs estructurados (`winston` en NestJS / `logging` en FastAPI) para detectar anomalías de red o respuestas erróneas del exchange.
2. **Contención:** Ante comportamientos inusuales, el ATS cambia su estado de forma inmediata a pasivo, aislando los procesos afectados mediante reglas de red o revocando tokens de acceso.
3. **Erradicación:** Parcheo en caliente del adaptador de infraestructura afectado en el entorno de desarrollo y actualización automática de dependencias vulnerables.
4. **Recuperación:** Despliegue seguro reactivando las operaciones comerciales de manera escalonada. Cuando el deployment es Docker, se aprovechan los `healthchecks` nativos; cuando es bare metal, se usan health endpoints HTTP equivalentes.



---

## 5. Tarjetas de Usuario para Desarrollo (User Stories con Enfoque Hexagonal)

### Épica 1: Arquitectura de Datos y Mensajería en Tiempo Real

#### **Historia de Usuario 1: Canalización de Ticks Asíncrona (NestJS + Redis + FastAPI)**

> **Como** desarrollador del ATS,
> **Quiero** implementar un flujo desacoplado donde un Gateway de NestJS envíe datos a Redis Streams y FastAPI los consuma de manera asíncrona,
> **Para** asegurar que el cómputo analítico no degrade la captura de datos del WebSocket.

* **Criterios de Aceptación:**
* **Happy Path:** NestJS (`Infraestructura`) se conecta al WebSocket externo $\rightarrow$ recibe un tick $\rightarrow$ delega al Caso de Uso (`Aplicación`) la sanitización del objeto $\rightarrow$ el adaptador de Redis inyecta la trama en el stream en $<2\text{ ms}$. FastAPI consume el stream usando `asyncio`, actualiza el DataFrame y dispara la lógica de trading sin bloquear la recepción de NestJS.
* **Sad Path:** Si Redis se desconecta, NestJS intercepta la falla en su capa de infraestructura, almacena temporalmente los ticks en un búfer en memoria (máximo 100) y registra el error de forma local. Si la caída persiste por más de 10 segundos, NestJS cierra el WebSocket del exchange ordenadamente para prevenir incoherencias en el histórico de velas.



---

### Épica 2: Motor de Gestión de Riesgo Coercitivo

#### **Historia de Usuario 2: Cálculo Automatizado del Tamaño de la Posición (Dominio de Riesgo)**

> **Como** gestor de riesgo,
> **Quiero** que la capa de Dominio calcule el tamaño de la posición basándose en el balance libre y el ATR,
> **Para** que la pérdida máxima potencial nunca supere el $1\%$ por operación.

* **Criterios de Aceptación:**
* **Happy Path:** Una señal de trading llega al Caso de Uso de FastAPI. El Caso de Uso invoca la entidad del Dominio `CalculadorRiesgo`, pasándole el balance libre (recuperado por el adaptador CCXT) y el ATR actual. El Dominio devuelve la cantidad exacta de unidades a comprar situando el Stop Loss dinámico a una distancia proporcional al ATR.
* **Sad Path:** Si el adaptador de CCXT falla por Timeout de red o devuelve un balance inválido de $0, el Caso de Uso aborta la operación de forma inmediata antes de tocar el exchange y registra un error crítico. Si el tamaño de la posición calculado es inferior al lote mínimo permitido por las reglas de la API de mercado, la señal es descartada con un log de advertencia.



#### **Historia de Usuario 3: Circuito de Parada de Emergencia (Circuit Breaker)**

> **Como** propietario de la cuenta,
> **Quiero** un mecanismo coercitivo que apague el ATS si el Drawdown diario alcanza el límite establecido,
> **Para** evitar pérdidas catastróficas en días de alta manipulación de mercado.

* **Criterios de Aceptación:**
* **Happy Path:** El ATS opera con normalidad. Al cierre de la jornada UTC, el Caso de Uso verifica que el Drawdown acumulado está en rangos seguros y reinicia los contadores diarios a cero.
* **Sad Path:** Si una operación cierra con pérdidas y el acumulado diario cruza el umbral del $5\%$ (parámetro de Dominio), se dispara el `CircuitBreaker`. El ATS invoca de inmediato al adaptador de infraestructura de CCXT, cancela de forma masiva todas las órdenes abiertas, cierra cualquier posición spot/futuro activa a precio de mercado (`Market Order`), reescribe `ENVIRONMENT_MODE=PASIVO` y detiene la marcha comercial registrando el bloqueo.



---

### Épica 3: Ejecución de Órdenes y Resiliencia Financiera

#### **Historia de Usuario 4: Colocación Resiliente de Órdenes con CCXT**

> **Como** motor de ejecución,
> **Quiero** canalizar las órdenes a través de los adaptadores de CCXT utilizando lógica de reintento exponencial,
> **Para** mitigar fallas parciales de red durante ejecuciones críticas de Stop Loss.

* **Criterios de Aceptación:**
* **Happy Path:** El Caso de Uso de ejecución despacha una orden compuesta (Orden de Mercado + Stop Loss/Take Profit vinculados) al adaptador de CCXT. El exchange procesa la solicitud y retorna confirmación con su correspondiente ID de orden en milisegundos.
* **Sad Path:** Si la API del exchange retorna un error de timeout de red al intentar colocar el Stop Loss, el adaptador de infraestructura ejecuta de forma autónoma una política de reintento exponencial (máximo 3 reintentos en una ventana estricta de 500ms). Si todos los reintentos fallan, la capa de aplicación toma el control y envía una orden de emergencia a mercado para liquidar toda la posición inmediatamente y evitar pérdidas descontroladas.



---

### Épica 4: Modos de Operación del Sistema

#### **Historia de Usuario 5: Control de Entornos Seguro (`ENVIRONMENT_MODE`)**

> **Como** analista cuantitativo,
> **Quiero** asegurar que el ATS reconfigure sus adaptadores de infraestructura según el entorno de ejecución inyectado,
> **Para** prevenir operaciones reales accidentales.

* **Criterios de Aceptación:**
* **Happy Path:** * `BACKTESTING`: El adaptador de datos de FastAPI conmuta para leer data histórica desde archivos CSV/BD locales, desactiva la red externa y genera métricas de simulación (*Sharpe Ratio*).
* `PAPER_TRADING`: El ATS activa los WebSockets reales de NestJS y los streams de Redis, pero el adaptador de ejecución escribe los trades en una base de datos local simulada.
* `LIVE`: Se activan todas las conexiones productivas reales y flujo monetario real.


* **Sad Path:** Si `ENVIRONMENT_MODE=LIVE` pero el módulo de infraestructura detecta que faltan las credenciales cifradas en las variables de entorno o que son cadenas vacías, NestJS and FastAPI abortan el ciclo de inicialización lanzando una excepción fatal y deteniendo el contenedor Docker con un código de salida `1`.



---

### Épica 5 (Fase 1): Motor de Ejecución en Vivo (Live Execution Engine)

#### **Historia de Usuario 7: Orquestación de Ejecución en Vivo desde Señales**

> **Como** motor de trading automatizado,
> **Quiero** un orquestador que consuma señales de trading (NovaQuant o estrategia), valide riesgo, calcule tamaño de posición, ejecute órdenes compuestas y monitoree posiciones abiertas en tiempo real,
> **Para** cerrar el bucle señal→orden→posición→P&L replicando en producción lo que el Backtest Engine (H8) simula fuera de línea.

* **Criterios de Aceptación:**
* **Happy Path:** El orquestador recibe una señal `TradingSignal` (BUY/SELL con confianza) → consulta el Circuit Breaker (no está HALTED) → calcula tamaño de posición vía `PositionCalculator` (≤1% risk) → invoca `PlaceOrderUseCase` con orden compuesta (entry + SL a distancia ATR + TP) → persiste la posición resultante en `PositionRepository` → emite un `ExecutionReport`. El loop de monitoreo de posiciones abiertas verifica cada N segundos si el SL o TP deben ajustarse, y registra el P&L real al cierre.
* **Sad Path:** Si la señal tiene confianza inferior al mínimo configurable, se descarta con log. Si el Circuit Breaker está HALTED, la señal se rechaza con log de advertencia. Si `PlaceOrderUseCase` falla (3 reintentos agotados), se registra un incidente crítico y se notifica vía `IncidentReporter` (H4-B). Si el exchange devuelve un fill parcial, el orquestador registra la posición con la cantidad real ejecutada.
* **Cooldown:** El orquestador mantiene un cooldown por símbolo (configurable, default 60s) para evitar señales duplicadas. Cualquier señal para el mismo símbolo dentro del cooldown se descarta.
* **Reinicio:** Al arrancar, el orquestador carga posiciones abiertas desde el `PositionRepository` y reanuda el monitoreo. Si el archivo de persistencia está corrupto o ausente, arranca con posición plana.

---

### Épica 5 (Fase 2): Telemetría y Monitoreo del Sistema (Fase Final Postergada)

#### **Historia de Usuario 6: Integración del Módulo de Monitoreo Operativo**

> **Como** operador,
> **Quiero** que este componente sea el último en construirse dentro del ciclo del proyecto,
> **Para** adaptarlo correctamente dependiendo de si se aprueba el desarrollo de un Frontend o si se opta definitivamente por Webhooks asíncronos (Telegram/Discord).

* **Criterios de Aceptación:**
* **Happy Path:** Una vez consolidados los motores de datos, riesgo y ejecución, se define la vía de salida de telemetría. Si se aprueba el Frontend, NestJS habilitará Gateways internos estructurados para transmitir el estado del balance y operaciones en tiempo real a la UI. Si se descarta, se construirán adaptadores ligeros para despachar eventos estructurados directos hacia APIs de mensajería externa.
* **Sad Path:** Durante su construcción, fallas en la entrega o consumo de payloads de telemetría (sea por caída del Front o saturación de webhooks externos) jamás deberán interferir, bloquear o agregar latencia de hilos al bucle principal del Core de trading e IA de FastAPI.

---

## 6. Invariantes Arquitectónicas (Resumen)

Las siguientes invariantes son **no negociables** y se verifican automáticamente en pre-merge (ver `AGENTS.md` §2 para el detalle completo y las 14 invariantes):

* **#8 HEXAGONAL — Domain:** el Dominio no importa de Application ni Infrastructure.
* **#9 HEXAGONAL — Application:** Application solo importa ports (interfaces), nunca adapters concretos.
* **#10 HEXAGONAL — data-engine ↔ analytics-engine:** la comunicación entre los dos servicios ocurre **únicamente** vía el `MessageBus` (broker RESP-compatibile). Nunca imports directos.
* **#11 STACK LEAKAGE:** `ioredis`/`ws`/`ccxt` solo dentro de `apps/data-engine/src/infrastructure/`. `pandas`/`ta`/`tensorflow` solo dentro de `apps/analytics-engine/app/infrastructure/`.
* **#12 TICK PIPELINE:** inyección al broker en `<2ms` p99 (spec §5 Historia 1).
* **#14 DEPLOYMENT AGNOSTICISM:** el código de aplicación no asume Docker. Hostnames via env vars (`ARGOS_BROKER_URL`, `EXCHANGE_*`), paths via `path.join` / `os.path.join`, broker via `MessageBus` port (no hardcoded a Redis). `docker-compose.yml` es una opción de deploy, no la única. El sistema corre bare metal con el mismo binario.

---

## 7. EDL — Edge Decision Layer (Evidence System)

### 7.1 Trade Episode — Unidad Atómica de Evidencia

Un *Trade Episode* es la unidad mínima de observación del sistema EDL. Representa una realización del sistema de decisión bajo condiciones de mercado.

```
E = (episode_id, t_entry, t_exit, side, pnl, regime_at_entry,
     model_version, feature_hash, feature_schema_version)
```

**Propiedades:**
- **Inmutable**: creado como dataclass `frozen=True`. Solo se puede "enriquecer" vía `settle()` que produce un nuevo episodio.
- **feature_hash**: `SHA256(canonicalize(feature_vector))`. El vector crudo NO se almacena.
- **Canonicalización**: sorted keys, 6-decimal precision, NaN/Inf → null, schema versioning explícito (`feature_schema_version`).
- **Append-only store**: no se puede eliminar ni modificar un episodio. Solo `append()` para episodios abiertos y `append_settled()` para cerrados.

**Integración:**
- **Creación**: en el bucle de decisión, tras `guard_.execute()` exitoso. Feature hash desde `pipeline.last_raw_features`.
- **Settlement**: en `_phase_b_loop`, tras `tracker.tick()`. Episodios cerrados apareados con `PhaseBTradeEntry` vía `episode_id`.

## 8. EDL — Prior Specification (Escepticismo Institucional)

### 8.1 Objetivo

El prior representa el escepticismo institucional del sistema. Su propósito no es reflejar confianza en el modelo, sino imponer la carga de la prueba: cuanto mayor sea la capacidad del modelo para ajustarse al ruido, mayor deberá ser la evidencia requerida para declarar edge.

### 8.2 Principio Fundamental

El sistema asume que la mayoría de los modelos no poseen edge. Por tanto:

```
P(edge | model) << 0.5
```

### 8.3 Prior Base

Para un modelo nuevo:

```
P_base = 0.15
```

Interpretación: 15% de probabilidad de que exista edge; 85% de que el comportamiento observado sea atribuible al ruido.

### 8.4 Factorización

```
P(edge | model) = P_base × C(model) × R(model) × H(model) × T(train, eval)
```

Donde:
- **C(model)**: penalización por complejidad.
- **R(model)**: penalización por cobertura de regímenes de entrenamiento.
- **H(model)**: factor histórico (herencia de champions previos).
- **T(train, eval)**: factor de transferencia entre régimen de entrenamiento y régimen de evaluación.

### 8.5 Complejidad

Se usa `complexity_score ∈ [0, 1]` en lugar de `n_params` directo:

| Modelo | complexity_score |
|---|---|
| EMA Cross | 0.1 |
| XGBoost simple | 0.3 |
| LSTM | 0.5 |
| Ensemble LSTM+XGB+Meta | 0.7 |
| Sistema con RL y adaptación online | 1.0 |

```
C(model) = exp(-λ · complexity_score)
```

### 8.6 Cobertura de Regímenes

```
R(model) = 0.7  si entrenado en un solo régimen
R(model) = 1.0  si entrenado en múltiples regímenes
```

### 8.7 Historial

```
H(model) = 1.2  para modelo derivado de un champion estable
H(model) = 1.0  para modelos nuevos
H(model) = 0.8  para familias con historial pobre
```

### 8.8 Transferencia entre Regímenes

```
T(train, eval)
```

| train → eval | T |
|---|---|
| Mismo régimen | 1.0 |
| Cruzado (trending ↔ ranging) | 0.7 |
| Régimen desconocido | 0.5 |

Implementación diferida a v1 o v2.

### 8.9 Prior Log-Odds

Trabajo interno en log-odds:

```
L0 = log(P / (1 - P))
```

La actualización bayesiana se vuelve suma lineal:

```
Posterior LogOdds = Prior LogOdds + Log Bayes Factor
```

### 8.10 Conservadurismo

`CONFIRMED_EDGE` debe ser raro. La ausencia de evidencia suficiente mantiene el modelo en `UNKNOWN` por largos periodos. La transición a `CONFIRMED_EDGE` requiere que los datos obliguen al sistema a abandonar su escepticismo inicial.

### 8.11 λ — Calibración

El parámetro λ NO se calibra contra el forward test actual. Su calibración pertenece a la capa de Simulation Calibration (Section 11). Valor provisional para implementación:

```
DEFAULT_LAMBDA = 1.0
```

La calibración definitiva usará simulaciones Monte Carlo, backtests históricos de modelos fallidos y exitosos, y benchmarks institucionales.

## 9. EDL — Likelihood Specification

### 9.1 Propósito

El likelihood define cómo cada `TradeEpisode` aporta evidencia a favor o en contra de la hipótesis de edge. El sistema no actualiza creencias usando precios ni indicadores, sino únicamente mediante observaciones cerradas e inmutables.

### 9.2 Hipótesis Competidoras

- **H₀ (No Edge)**: los resultados observados son compatibles con ruido estadístico. La distribución de episodios del modelo no supera a la del baseline.

- **H₁ (Edge Exists)**: los resultados son incompatibles con H₀ y muestran superioridad estadísticamente significativa respecto al baseline.

```
H₁: U(model) > U(baseline)
```

donde `U` comienza siendo Sharpe ajustado por drawdown (calmar ratio o similar). EMA Cross es el baseline primario (adversario natural). Buy & Hold es referencia secundaria. Futuros challengers se integran automáticamente en v2.

### 9.3 Unidad de Evidencia

La unidad mínima es un `TradeEpisode` cerrado:

```
E_i = (pnl_i, duration_i, regime_i, feature_hash_i, model_i)
```

Los episodios abiertos NO aportan evidencia.

### 9.4 Independencia por Ventanas

No se asume independencia completa entre episodios. En su lugar:

```
P(D) = Π P(W_w)
```

donde `W_w` puede ser 1 día, 7 días, o un bloque de N trades. Esto reduce la sensibilidad a clusters de ganancias/pérdidas.

### 9.5 Construcción de H₀ (Distribución Nula)

H₀ se construye empíricamente, NO con distribuciones teóricas. Para cada régimen:

| Régimen | Parámetros |
|---|---|
| TRENDING | μ, σ, win_rate, density, duration_distribution |
| RANGING | μ, σ, win_rate, density, duration_distribution |

```
H₀ = { H₀_trending, H₀_ranging }
```

### 9.6 Fuente de Referencia para H₀

La distribución nula NO proviene del modelo evaluado. Proviene de baselines externos que producen `TradeEpisode`s comparables:

- EMA Cross
- Buy & Hold
- Futuros challengers

### 9.7 Likelihood Condicional al Régimen

No existe `P(E | H)` sino `P(E | H, r)` con `r ∈ {TRENDING, RANGING}`. Esto evita declarar edge global cuando solo existe edge local.

### 9.8 Componentes del Likelihood por Episodio

Cada episodio aporta evidencia en cuatro dimensiones:

```
P(E_i | H) = L_pnl · L_duration · L_density · L_regime
```

O en log-space:

```
log P(E_i | H) = Σ log L_j
```

### 9.9 Likelihood Ratio

```
LR_i = P(E_i | H₁) / P(E_i | H₀)
LLR(D) = Σ log LR_i
```

Interpretación:
- `LR > 1`: favorece edge
- `LR < 1`: favorece ruido
- `LR ≈ 1`: neutral

### 9.10 Actualización Secuencial Bayesiana

En log-odds:

```
logO_t = logO_{t-1} + logLR_t
```

Cada `TradeEpisode` modifica la creencia institucional.

### 9.11 Decay

La ausencia de evidencia también es información.

El decay NO actúa sobre LLR (la evidencia ocurrió y no desaparece). Actúa sobre el log-odds completo:

```
L_t = L_0 + LLR_t

L_{t+Δ} = L_0 + (L_t - L_0) · e^{-λ·Δt}
```

donde:
- `L_0` = log-odds del prior (escepticismo institucional).
- `LLR_t` = log-likelihood-ratio acumulado hasta t.
- `L_t` = log-odds posterior completo en t.

El sistema siempre regresa gradualmente al escepticismo institucional (`L_0`). Sin evidencia, la creencia decae al prior. Con evidencia constante, el sistema la mantiene mientras sea reciente.

### 9.12 Principio de Anti-Contaminación

H₀ NUNCA puede construirse usando episodios del mismo modelo evaluado. Prohibido: ARGOS episodios → build H₀ → evaluate ARGOS. La referencia debe provenir de baselines externos o modelos históricos congelados.

### 9.13 Principio de Falsificación

Si `LLR < θ_kill` para un régimen, entonces `P(edge | D, r) → 0` para ese régimen, que entra en `NO_EDGE` sin intervención humana. La falsificación es por régimen, no global.

### 9.14 Output

El likelihood no produce estados. Produce una cantidad continua:

```
P(edge | D, r, m)
```

donde `D` = episodios observados, `r` = régimen, `m` = modelo. Los estados del EDL son proyecciones discretas de esta posterior, y existen **por régimen** (ver Section 10.3).

### 9.15 Mapeo Probabilidad → Estado (por régimen)

| P(edge \| D, r, m) | Estado |
|---|---|
| < 0.2 | NO_EDGE |
| 0.2 – 0.5 | UNKNOWN |
| 0.5 – 0.8 | WEAK_EDGE |
| 0.8 – 0.95 | PROBABLE_EDGE |
| > 0.95 | CONFIRMED_EDGE |

Cada régimen produce su propio estado independientemente. No existe un estado global único.

## 10. EDL — Hierarchical Bayesian Evidence Model

### 10.1 Jerarquía del Modelo

```
TradeEpisode
    ↓
Window (día / 7 días / N trades)
    ↓
Regime (TRENDING, RANGING)       ← inferencia primaria
    ↓
Model (ARGOS, EMA Cross, Buy & Hold, challengers)
    ↓
Portfolio of Models
```

### 10.2 Posterior Completa

Posterior primaria — vector por régimen:

```
P(edge | D, m) = { P(edge | D, TRENDING, m),
                    P(edge | D, RANGING, m) }
```

Cada elemento:

```
P(edge | D, r, m) ∝ P(D | edge, r, m) · P(edge | r, m)
```

Donde:
- `P(D | edge, r, m)` = Likelihood (Section 9), condicional a régimen y modelo.
- `P(edge | r, m)` = Prior (Section 8), corregido por régimen de evaluación y complejidad del modelo.

### 10.3 Estados por Régimen

La posterior vive como vector. Los estados también:

| Régimen | P(edge\|D, r, m) | Estado |
|---|---|---|
| TRENDING | 0.93 | CONFIRMED_EDGE |
| RANGING | 0.11 | NO_EDGE |

No se promedia. No se toma mínimo. Cada régimen tiene su propia declaración de edge.

El agregado global es un derivado secundario (ej: `max(P(edge|r))` para reportes), no la verdad primaria.

### 10.4 Posterior Multi-Modelo (v2)

Con múltiples modelos:

```
P(edge | model, regime)
```

La utilidad relativa se define como:

```
Utility(m) = rank_vector_score(m)
```

Donde `rank_vector_score` usa agregación tipo Borda: ranking por Sharpe, drawdown, estabilidad, diversidad de régimen, outperformance vs baseline. Sin score compuesto raw.

### 10.5 Champion Selection

Un modelo solo es champion si:
- Ha sido challenger previamente.
- Ha mantenido superioridad ≥ 5 evaluaciones consecutivas.
- Supera baseline y otros modelos simultáneamente.
- `unique_regimes_seen ≥ 2` dentro del período.

### 10.6 Régimen de Decaimiento y Rotación

- **EDGE_DECAY**: Sharpe decreciente 5 días consecutivos, regime instability creciente, baseline convergence.
- **Champion rotation**: solo si `utility_gap > 0.1` mantenido por 5 evaluaciones consecutivas con diversidad de régimen.

### 10.7 Output del Sistema

El sistema no produce "decisiones". Produce:

```
P(edge | D, r, m)
```

que es una distribución de probabilidad sobre la existencia de edge, condicional a los datos observados, el régimen actual y el modelo activo. Las acciones (continuar, congelar, escalar) son proyecciones de esta distribución con umbrales configurables.

---

## 11. Evaluación de Políticas Basada en Trayectorias (Trajectory Model)

### 11.1 Problema Fundamental

El sistema EDL (Sections 7–10) define cómo cada `TradeEpisode` aporta evidencia. Sin embargo, la inferencia sobre edge requiere resolver un problema más profundo:

> Los `TradeEpisode`s NO son i.i.d. Los retornos NO son estacionarios. El régimen de mercado es latente y no directamente observable. La ejecución introduce autocorrelación.

Por tanto, `P(edge | D)` no puede estimarse como:
- Una media de Sharpe con distribución normal (asume estacionariedad).
- Un bootstrap de trades individuales (rompe dependencia temporal, destruye estructura de régimen, sobreestima estabilidad del edge).
- Un score compuesto (introduce dependencia entre evaluación y decisión, ver Section 12).

### 11.2 Definición de Trayectoria

Una **trayectoria** `τ` es una secuencia temporalmente ordenada de `TradeEpisode`s generada por un modelo bajo condiciones de mercado:

```
τ = (E₁, E₂, ..., E_n)
```

Cada episodio:

```
E_i = (pnl_i, duration_i, regime_i, feature_hash_i, model_id, timestamp_i)
```

Propiedades de una trayectoria válida:

| Propiedad | Descripción |
|---|---|
| **Orden temporal** | `timestamp_i < timestamp_{i+1}` ∀i. No se permite reordenamiento. |
| **Persistencia de régimen** | `regime_i` cambia según matriz de transición, no aleatoriamente. |
| **Restricción de capital** | `balance_i > 0` ∀i. Capital inicial fijo; el balance evoluciona con `pnl_i` y costos. |
| **Compounding** | El tamaño de posición en `E_i` depende del balance acumulado hasta `i-1`. |
| **Autocorrelación de ejecución** | Episodios consecutivos comparten condiciones de mercado y latencia de ejecución. |

### 11.3 Estructura de Bloques por Régimen

No se asume que los episodios son generados por un proceso homogéneo. En su lugar, se agrupan en **bloques de régimen**:

```
τ = [B₁, B₂, ..., B_k]
```

Cada `B_j` es una subsecuencia contigua de episodios donde el régimen es aproximadamente estacionario:

```
B_j = (E_a, E_{a+1}, ..., E_b)  con regime(B_j) = r_j ∈ {TRENDING, RANGING}
```

Propiedades del bloque:

| Propiedad | Estimación empírica desde D |
|---|---|
| `μ_j` | Media de `pnl` dentro del bloque |
| `σ_j` | Desviación estándar de `pnl` dentro del bloque |
| `win_rate_j` | Fracción de episodios con `pnl > 0` |
| `duration_j` | Duración total del bloque en tiempo de calendario |
| `density_j` | Episodios por unidad de tiempo dentro del bloque |

**Matriz de transición de régimen**:

```
T(r_a → r_b) = count(regime transition a→b) / count(regime = a)
```

Estimada empíricamente desde `D`. Si no hay transiciones observadas, se usa previa uniforme suavizada con pseudocuento `α = 1`.

### 11.4 Generación de Trayectorias (Monte Carlo sobre Espacio de Paths)

`P(edge | D)` se estima simulando **mundos alternativos plausibles** y comparando el desempeño del modelo contra el baseline en cada mundo.

#### 11.4.1 Algoritmo

```
1. Extraer bloques de régimen B₁...B_k de D (observado).
2. Estimar matriz de transición de régimen T.
3. Estimar parámetros de cada bloque (μ, σ, win_rate, duration, density).
4. Para cada simulación s ∈ {1...M}:
     a. Generar una secuencia de regímenes r₁, r₂, ..., r_m usando T
        (cadena de Markov de primer orden).
     b. Para cada régimen r_j:
          - Samplear un bloque B'_j del conjunto de bloques observados
            con régimen r_j (block bootstrap con reemplazo).
          - Alternativamente: samplear episodios individuales de bloques
            con régimen r_j, preservando el orden dentro del bloque.
     c. Concatenar B'_₁, ..., B'_m → trayectoria τ_s.
     d. Aplicar modelo M y baseline B a τ_s (mismas condiciones de mercado):
          - Calcular equity final, Sharpe, DD máximo, profit factor.
     e. Registrar U_model(τ_s) y U_baseline(τ_s).
```

#### 11.4.2 Preservación de Estructura

| Propiedad | Mecanismo en el generador |
|---|---|
| Orden temporal | Bloques concatenados en secuencia; dentro de cada bloque, episodios preservan orden |
| Persistencia de régimen | Cadena de Markov con matriz T; el mismo régimen tiende a persistir |
| Autocorrelación | Episodios dentro de un bloque bootstrap mantienen su covarianza empírica |
| Restricción de capital | Equity simulada parte de `INITIAL_CAPITAL` y aplica compounding |
| Regímenes no observados | Si un régimen no aparece en D, se excluye (no hay base empírica para simularlo) |

#### 11.4.3 Número de Simulaciones

```
M ≥ 10,000  (mínimo para estimación estable de colas)
M ≥ 50,000  (recomendado para inferencia por régimen)
```

### 11.5 Estimación de P(edge | D)

```
P(edge | D) = (1/M) · Σ_s 𝟙[U_model(τ_s) > U_baseline(τ_s)]
```

donde `U` es una función de utilidad que **debe** definir el decisor (ver Section 12.2). La función por defecto:

```
U(τ) = Sharpe(τ)  si DD_max(τ) < 0.05
U(τ) = -∞         si DD_max(τ) ≥ 0.05  (fail)
```

**Interpretación**: `P(edge | D) = 0.78` significa que en el 78% de los mundos plausibles (dado lo observado), el modelo supera al baseline. No es un "score". Es una **distribución sobre la relación entre políticas**.

#### 11.5.1 Descomposición por Régimen

```
P(edge | D, r) = (1/M_r) · Σ_{s: τ_s contiene r} 𝟙[U_model(τ_s, r) > U_baseline(τ_s, r)]
```

Cada régimen produce su propia estimación de edge. No se promedian.

### 11.6 Estimación de P(failure | D)

```
P(failure | D) = (1/M) · Σ_s 𝟙[failure(τ_s)]
```

donde `failure(τ)` se define como:

| Condición de Fracaso | Definición |
|---|---|
| **Drawdown** | `DD_max(τ) > δ_dd` con `δ_dd = 0.05` |
| **Ruina** | `equity(τ) < 0` en cualquier punto |
| **Underperformance persistente** | `Sharpe_rolling(τ, 14d) < δ_sharpe` por más de `N_días` consecutivos |
| **Dominancia de baseline** | `U_baseline(τ) > U_model(τ) + ε` con `ε = 0.20` (el baseline supera al modelo por >20%) |

`P(failure | D)` es la entrada principal al Control Layer (Section 12).

### 11.7 Relación con el EDL (Sections 7–10)

```
Section 7-8 (TradeEpisode + Prior)
    → produce episodios inmutables y escepticismo inicial
Section 9 (Likelihood)
    → produce LLR por episodio, condicional a régimen
Section 10 (Hierarchical Bayesian)
    → produce P(edge | D, r, m) vía actualización secuencial
Section 11 (Trajectory Model)  ← NUEVO
    → produce P(edge | D) y P(failure | D) vía Monte Carlo estructural
```

La diferencia fundamental:

| EDL (Sections 7–10) | Trajectory Model (Section 11) |
|---|---|
| Inferencia episodio por episodio | Evaluación de políticas completas |
| Actualización secuencial bayesiana | Simulación de mundos alternativos |
| Asume independencia por ventanas | Preserva dependencia temporal y de régimen |
| Produce `P(edge \| D, r, m)` | Produce `P(edge \| D)` y `P(failure \| D)` como distribuciones |
| Input: episodios individuales | Input: bloques de régimen + matriz de transición |

Ambas capas coexisten. La Sección 11 **complementa** a la Sección 10, no la reemplaza. La Sección 10 es la inferencia fina (episodio a episodio). La Sección 11 es la evaluación gruesa (el sistema como política completa frente a baselines).

### 11.8 Implementación

El Trajectory Model se implementa como:

```
scripts/trajectory_model.py
```

Interfaz:

```python
class TrajectoryModel:
    def __init__(self, episodes: list[TradeEpisode])
    def estimate_regime_transition(self) -> np.ndarray
    def build_blocks(self) -> list[RegimeBlock]
    def simulate(self, M: int = 10000) -> SimulationResult
    def edge_probability(self, result: SimulationResult) -> float
    def failure_probability(self, result: SimulationResult) -> float
```

Datos de entrada: `forward_test/trades_{symbol}.csv` (episodios cerrados), `forward_test/equity_{symbol}.csv` (evolución de equity para restricciones de capital).

---

## 12. Capa de Control Semántico (Control Layer)

### 12.1 Principio de Separación

La Capa de Control **no** produce evaluaciones. La Capa de Evaluación **no** produce decisiones. La separación es estricta:

| Capa | Produce | NO produce |
|---|---|---|
| Evaluation (Section 11) | `P(edge \| D)`, `P(failure \| D)` | Decisiones, stops, estados |
| Control (Section 12) | `HALT`, `CONTINUE`, `REDUCE` | Scores, distribuciones |

Esto elimina el riesgo de que el protocolo influya en el comportamiento del sistema evaluado.

### 12.2 Función de Utilidad del Decisor

La función `U(τ)` del decisor NO es negociada por el sistema. Es una entrada del usuario. El sistema solo computa estadísticas; el decisor define qué es "mejor".

Formato:

```python
@dataclass(frozen=True)
class UtilityFunction:
    name: str
    # Ponderadores para Sharpe, Drawdown, Profit Factor, etc.
    weights: dict[str, float]
    # Restricciones duras (el decisor las define)
    hard_constraints: dict[str, float]
```

Valor por defecto (razonable para un perfil conservador):

| Componente | Peso | Notas |
|---|---|---|
| Sharpe | 0.40 | Retorno ajustado por riesgo |
| Profit Factor | 0.20 | Gross Profit / Gross Loss |
| Win Rate | 0.15 | Estabilidad |
| Avg Win / Avg Loss | 0.15 | Calidad de trades ganadores vs perdedores |
| Calmar Ratio | 0.10 | Sharpe ajustado por DD |

### 12.3 Hazard-Based Control

No se usan umbrales arbitrarios sobre métricas puntuales. En su lugar, se usa una función de hazard:

```
h(t) = P(failure en [t, t+Δt] | sobrevive hasta t, D)
```

El hazard acumulado:

```
H(t) = Σ_{i=1}^{t} h(i)
```

**Regla de decisión**:

| Condición | Acción |
|---|---|
| `h(t) > θ_immediate` con `θ_immediate = 0.15` | `HALT` inmediato |
| `H(t) > θ_cumulative` con `θ_cumulative = 0.50` | `HALT` al final del día |
| `H(t) < θ_safe` con `θ_safe = 0.15` | `CONTINUE` |
| `0.15 ≤ H(t) ≤ 0.50` | `REDUCE` (reducir exposición al 50%) |

Donde:

| Parámetro | Valor | Base |
|---|---|---|
| `θ_immediate` | 0.15 | 15% de probabilidad de fracaso en la próxima barra → stop inmediato |
| `θ_cumulative` | 0.50 | 50% acumulado → stop al cierre del día |
| `θ_safe` | 0.15 | Por debajo de 15% acumulado → operar con normalidad |

Estos umbrales son **configurables por el operador**, no decididos por el sistema. El sistema solo reporta `H(t)` y ejecuta la acción correspondiente.

### 12.4 Kill Switch (Único Nivel)

**No existe soft stop**. Solo existe `HALT`. Las razones:

| Condición | Hazard Trigger | Acción |
|---|---|---|
| **Drawdown excedido** | `P(DD > 5% en 30 días \| D) > 0.15` | `HALT` |
| **Sharpe crítico** | `P(Sharpe_forward < 0.2 en 30 días \| D) > 0.30` | `HALT` |
| **Baseline dominance** | `P(U_baseline > U_model \| D) > 0.70` | `HALT` |
| **Múltiples alertas** | 2 o más condiciones con `P > 0.15` | `REDUCE` primero; si persiste 7 días → `HALT` |

#### 12.4.1 Acciones del Kill Switch

Al activarse `HALT`:

1. **Suspender señales**: el forward test ignora nuevas predicciones. `state.kill_switch = "halted"`.
2. **Cerrar posición**: si existe posición abierta, se cierra a mercado.
3. **Persistir estado**: `state.kill_switch_reason`, `state.kill_switch_timestamp`.
4. **Notificar**: el health endpoint retorna `status: HALTED` con razón.
5. **No reanudar automáticamente**: requiere intervención manual + `--reset` explícito.

El sistema en `HALTED` sigue ejecutándose (health endpoint responde, logs funcionan), pero no genera nuevas señales.

#### 12.4.2 REDUCE (Escalón Pre-HALT)

El estado `REDUCE` actúa como escalón antes de `HALT`:

1. **Reducir exposición**: el tamaño de posición se escala al 50%.
2. **Monitorear**: si después de 7 días calendario el hazard no baja de `θ_safe`, escala a `HALT`.
3. **Log**: cada barra en `REDUCE` registra el hazard actual y días restantes.

`REDUCE` no es soft stop — es un mecanismo de preservación de capital con timeline fijo. A los 7 días, o mejora o se detiene.

### 12.5 Relación con el Forward Test

```
forward_test.py (cada barra)
    → process_bars()
        → features → model → signal → trade
        → log episodio
    → al final del día:
        → trajectory_model.simulate(M=10000)  ← Section 11
        → P(edge | D), P(failure | D)
        → control_layer.evaluate()            ← Section 12
            → h(t), H(t)
            → CONTINUE / REDUCE / HALT
        → persistir protocol_BTC.json
```

### 12.6 Implementación

```
scripts/control_layer.py
```

Interfaz:

```python
class ControlLayer:
    def __init__(self, utility_fn: UtilityFunction)
    def evaluate(self, equity: pd.Series, episodes: list[TradeEpisode],
                 M: int = 10000) -> ControlDecision

@dataclass
class ControlDecision:
    action: Literal["CONTINUE", "REDUCE", "HALT"]
    hazard_immediate: float
    hazard_cumulative: float
    p_failure: float
    p_edge: float
    reason: str | None
    timestamp: str
```

### 12.7 Principios de Gobernanza

1. **El sistema no decide qué es "éxito"**. El decisor humano define `UtilityFunction` y umbrales de hazard. El sistema solo computa `P(edge | D)` y `h(t)`.

2. **No existe score compuesto**. La Evaluation Layer produce distribuciones. La Control Layer produce acciones discretas basadas en hazard. No hay `PASS / WEAK / FAIL`.

3. **El protocolo no influye en el sistema evaluado**. La Control Layer solo puede detener o reducir exposición. No puede modificar thresholds, features, modelo, ni lógica de entrada/salida.

4. **`HALT` es definitivo hasta intervención manual**. No hay reanudación automática. No hay "soft halt" que permita seguir operando.

5. **`REDUCE` tiene timeline fijo**. 7 días para que el hazard baje de `θ_safe`, o escala a `HALT`. No se extiende.

---

## 13. Capa de Referencia Externa (External Reference Layer — ERL)

### 13.1 Problema Fundamental

Las Sections 7–12 definen un sistema completo de inferencia y control. Sin embargo, todo el sistema comparte una vulnerabilidad estructural:

> Las estimaciones de `P(edge | D)` y `P(failure | D)` son relativas a baselines internos (EMA Cross, Buy & Hold) y a simulaciones del Trajectory Model. No existe un **ancla externa** que represente "ausencia absoluta de edge".

Esto crea un riesgo de **auto-validación**: el sistema puede producir `P(edge | D)` consistentemente alto simplemente porque supera a baselines que también pueden no tener edge en términos absolutos.

Adicionalmente, la Section 11 mezcla implícitamente dos definiciones incompatibles de "trayectoria válida" (ver 13.2), y la Section 12 define `h(t)` sin anclarlo a una variable externa al sistema (ver 13.4).

### 13.2 Definición del Espacio de Trayectorias (cierra Problema 1)

El Section 11.4 define el generador de trayectorias como bootstrap de bloques por régimen. Sin embargo, hay dos interpretaciones incompatibles de "trayectoria válida":

#### A — Reconstrucción Histórica Plausible (conservadora)

| Propiedad | Descripción |
|---|---|
| **Fuente** | Episodios reales observados en `D` |
| **Mecanismo** | Block bootstrap con reemplazo, preservando orden dentro del bloque |
| **Regímenes nuevos** | No genera transiciones no observadas |
| **Tail risk** | Subestima colas estructurales (no puede generar crisis no vistas) |
| **Varianza** | Baja (estimación estable) |
| **Uso** | Evaluación diaria, detección de edge en régimen actual |

#### B — Proceso Generativo (exploratoria)

| Propiedad | Descripción |
|---|---|
| **Fuente** | Parámetros estimados de `D` + supuestos distribucionales |
| **Mecanismo** | Cadena de Markov + sampling paramétrico de PnL por régimen |
| **Regímenes nuevos** | Permite transiciones no observadas vía ruido en la matriz `T` |
| **Tail risk** | Captura fragilidad estructural (stress tests) |
| **Varianza** | Más alta (refleja incertidumbre de modelo) |
| **Uso** | Evaluación semanal, tests de estrés, detección de fragilidad |

#### Decisión

Ambas coexisten, pero con roles separados y declarados explícitamente:

| Propósito | Modelo | Frecuencia |
|---|---|---|
| Evaluación de edge en régimen actual | **A** (Reconstrucción) | Diaria |
| Detección de fragilidad estructural | **B** (Generativo) | Semanal |
| Reporte de `P(edge \| D)` oficial | **A** (por ser conservadora) | Diaria |
| Alerta temprana de tail risk | **B** | Semanal |

La Section 11.4 se actualiza para reflejar ambos modos. El modo por defecto es **A**.

#### 13.2.1 Modo A — Algoritmo de Reconstrucción

```
1. Extraer bloques de régimen B₁...B_k de D.
2. Estimar matriz de transición empírica T (solo transiciones observadas).
3. Para cada simulación s ∈ {1...M}:
     a. Generar secuencia de regímenes vía T (Markov chain).
     b. Para cada régimen r_j: samplear bloque B'_j del conjunto {B_i: regime = r_j}.
     c. Concatenar → trayectoria τ_s.
4. Comparar U_model(τ_s) vs U_baseline(τ_s).
```

Restricciones:
- `T` solo contiene transiciones observadas. Transiciones no observadas tienen probabilidad 0.
- Bloques se samplean con reemplazo pero sin partir episodios individuales.
- No se generan PnL sintéticos: todo PnL proviene de episodios reales.

#### 13.2.2 Modo B — Algoritmo Generativo

```
1. Extraer bloques de régimen B₁...B_k de D.
2. Estimar matriz de transición empírica T_0.
3. Añadir ruido a T: T = (1 - ε)·T_0 + ε·U, donde U es matriz uniforme y ε = 0.05.
   (Esto permite transiciones no observadas con probabilidad pequeña.)
4. Para cada bloque, estimar distribución paramétrica de PnL:
     - Normal: N(μ_j, σ_j)  (por simplicidad inicial)
     - Alternativa: Student-t con ν = 4 (colas pesadas)
5. Para cada simulación s ∈ {1...M}:
     a. Generar secuencia de regímenes vía T ruidosa.
     b. Para cada bloque: samplear PnL_i ∼ P(PnL | r_j) hasta completar duración esperada.
     c. Aplicar compounding y restricción de capital → trayectoria τ_s.
6. Comparar U_model(τ_s) vs U_baseline(τ_s).
```

El Modo B **no reemplaza** al Modo A para decisiones de control. El Modo B solo produce alertas: si `P(failure_B | D) >> P(failure_A | D)`, hay evidencia de fragilidad no capturada por la reconstrucción.

### 13.3 Modelo de Referencia Externa H₀ (cierra Problema 2)

#### 13.3.1 Problema

Actualmente, `P(edge | D)` se define como la fracción de mundos donde `U(model) > U(baseline)`. Pero el baseline (EMA Cross, B&H) es otro sistema interno. Si el baseline tampoco tiene edge en términos absolutos, la comparación es **relativa**, no absoluta.

Se necesita un **H₀ externo** que represente "ausencia de edge" en términos absolutos.

#### 13.3.2 Definición de H₀

```
H₀: El sistema se comporta como un proceso sin habilidad predictiva
```

Operacionalmente, H₀ es el **modelo nulo** que generaría la misma distribución de trades que el sistema evaluado **si no existiera edge**. Es decir:

> Si el sistema no tuviera edge, ¿cómo se verían sus trades?

#### 13.3.3 Construcción de H₀

Se construyen **tres modelos nulos**, en orden creciente de realismo:

| Nulo | Definición | Propósito |
|---|---|---|
| **H₀₁ — Random Walk** | Las señales del modelo se reemplazan por decisiones aleatorias (50% BUY, 50% SELL) con la misma frecuencia de trades | Referencia mínima: ¿el edge es mejor que lanzar una moneda? |
| **H₀₂ — Permutation** | Las señales del modelo se permutan aleatoriamente en el tiempo (destruye correlación temporal pero preserva distribución marginal de señales) | Referencia temporal: ¿el edge depende de timing correcto? |
| **H₀₃ — Cost-adjusted null** | Mismas señales, pero se añade ruido al PnL de cada trade: PnL'_i = PnL_i · (1 + η_i) con η_i ∼ N(0, σ_shuffle) donde σ_shuffle es la desviación estándar del PnL de los baselines | Referencia de ruido: ¿el PnL del modelo es indistinguible del ruido de mercado? |

#### 13.3.4 Criterio de Edge Absoluto

```
P(edge_abs | D) = P( U(model) > U(H₀₁) ∧ U(model) > U(H₀₂) ∧ U(model) > U(H₀₃) | D )
```

Es decir, el modelo debe superar **simultáneamente** a los tres nulos para que exista evidencia de edge absoluto. Si solo supera a algunos, el edge es relativo (depende de qué nulo se use como referencia).

#### 13.3.5 Relación con Baselines Internos

| Referencia | Tipo | Propósito |
|---|---|---|
| EMA Cross, B&H | Baselines internos (Section 9) | Competidores reales: ¿supera a estrategias simples? |
| H₀₁, H₀₂, H₀₃ | Nulos externos (Section 13) | Ancla absoluta: ¿tiene edge más allá del ruido? |

Ambos son necesarios. Un modelo puede:
- Superar a H₀ (tiene edge absoluto) pero no a EMA Cross (edge relativo débil) → continuar observando.
- Superar a EMA Cross pero no a H₀ (edge relativo pero no absoluto) → sospecha de sobreajuste al ruido de mercado.

#### 13.3.6 Reporte de Edge

El sistema reporta **ambos** valores:

```json
{
  "p_edge_absolute": 0.72,
  "p_edge_relative": 0.65,
  "best_baseline": "EMA Cross",
  "worst_null": "H₀₃ — Cost-adjusted",
  "verdict": "absolute edge detected"
}
```

### 13.4 Anclaje de la Función de Hazard (cierra Problema 3)

#### 13.4.1 Problema

En Section 12.3, `h(t)` se define como:

```
h(t) = P(failure en [t, t+Δt] | sobrevive hasta t, D)
```

Pero `failure` se define internamente (DD > 5%, Sharpe < 0.2, baseline dominance). Esto crea un **loop autoreferencial**:

```
Trajectory Model → P(failure | D) → h(t) → HALT
                                     ↑
                              (depende del mismo sistema)
```

Si el sistema está sesgado (ej: sobreestima su propio edge), `P(failure | D)` será sistemáticamente baja y `h(t)` nunca alcanzará el umbral de HALT, incluso si el sistema está fallando en el mundo real.

#### 13.4.2 Solución: Hazard Anclado Externamente

La función de hazard se redefine como:

```
h*(t) = max( h_interno(t), h_externo(t) )
```

donde:

| Componente | Definición | Fuente |
|---|---|---|
| `h_interno(t)` | `P(failure \| D)` del Trajectory Model (Section 11) | Interna: simulación |
| `h_externo(t)` | `P( H₀ supera al modelo en ventana rodante 30d \| condiciones_de_mercado )` | **Externa**: H₀ es el Reference Model (Section 13.3) |

#### 13.4.3 Cálculo de h_externo(t)

```
h_externo(t) = P( U(H₀) > U(model) en [t, t+30d] | market_conditions(t) )
```

Estimación:
1. Para cada simulación en el Trajectory Model (Modo A), también simular H₀₁, H₀₂, H₀₃ bajo las mismas condiciones de mercado.
2. `h_externo(t) = (1/M) · Σ_s 𝟙[max(U(H₀_j, τ_s)) > U(model, τ_s)]`.
3. `h_interno(t) = P(failure | D)` de Section 11.6.

#### 13.4.4 Regla de Decisión (actualizada)

| Condición | Acción |
|---|---|
| `h*(t) > θ_immediate` (0.15) | `HALT` inmediato |
| `H*(t) > θ_cumulative` (0.50) | `HALT` al final del día |
| `h_externo(t) > h_interno(t)` por 7 días consecutivos | `REDUCE` (señal de que el modelo se está desconectando de la realidad) |
| `h*(t) < θ_safe` (0.15) | `CONTINUE` |

La condición `h_externo > h_interno` por 7 días es un **indicador temprano de divorcio del modelo respecto a la realidad**: el modelo cree que está bien (h_interno bajo), pero el mundo externo (H₀) lo contradice.

### 13.5 Arquitectura Completa del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│  Section 13 — External Reference Layer (ERL)                │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  H₀₁: Random Walk Null                              │   │
│  │  H₀₂: Permutation Null                              │   │
│  │  H₀₃: Cost-adjusted Null                            │   │
│  │  Trajectory Space: A (reconstruction, diaria)       │   │
│  │                   B (generative, semanal)            │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────┬────────────────────────────────────┘
                         │ ancla externa
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Section 11 — Evaluation Layer                              │
│  Trajectory Model: P(edge | D), P(failure | D)             │
│  Modo A (diario) + Modo B (semanal)                        │
└────────────────────────┬────────────────────────────────────┘
                         │ distribuciones
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Section 12 — Control Layer                                 │
│  h*(t) = max(h_interno, h_externo) → HALT/REDUCE/CONTINUE   │
└────────────────────────┬────────────────────────────────────┘
                         │ acciones
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  Section 7-10 — EDL (Truth + Inference)                     │
│  TradeEpisodes → Prior → Likelihood → Posterior             │
└─────────────────────────────────────────────────────────────┘
```

### 13.6 Implementación

La ERL se implementa como:

```
scripts/external_reference.py
```

Interfaz:

```python
class ExternalReferenceLayer:
    def __init__(self, episodes: list[TradeEpisode])
    def build_nulls(self) -> NullEnsemble
    def trajectory_modes(self) -> TrajectoryModes
    def hazard_external(self, market_conditions: dict) -> float
    def edge_absolute(self, U_model: float, M: int = 10000) -> float

@dataclass
class NullEnsemble:
    h01_random_walk: TrajectoryModel
    h02_permutation: TrajectoryModel
    h03_cost_adjusted: TrajectoryModel

@dataclass
class TrajectoryModes:
    mode_a: TrajectoryModel  # Reconstruction
    mode_b: TrajectoryModel  # Generative
```

### 13.7 Resumen de Definiciones Cerradas

| Problema | Definición Cerrada |
|---|---|
| **Espacio de trayectorias** (13.2) | Dos modos explícitos: A (reconstrucción, diario) y B (generativo, semanal). Modo A es el oficial para decisiones. |
| **H₀ externo** (13.3) | Triple nulo: Random Walk, Permutation, Cost-adjusted. `P(edge_abs \| D)` requiere superar los tres. |
| **Hazard anclado** (13.4) | `h*(t) = max(h_interno, h_externo)`. `h_externo` = probabilidad de que H₀ supere al modelo. El divorcio `h_externo > h_interno` por 7 días → REDUCE. |

Estas tres definiciones rompen el loop autoreferencial y anclan el sistema a una realidad externa medible, independiente del modelo evaluado.

---

## 14. Utility Measure Specification

### 14.1 Problema Fundamental

Las Sections 7–13 definen un sistema completo de inferencia, evaluación y control. Sin embargo, todas las comparaciones (modelo vs baselines, modelo vs H₀) ocurren sin un **espacio de utilidad único** donde todas las políticas se evalúan bajo la misma métrica.

Actualmente:
- `P(edge | D)` compara Sharpe del modelo vs Sharpe del baseline (Section 11.5).
- `P(failure | D)` usa drawdown, Sharpe crítico, y baseline dominance (Section 11.6).
- `h*(t)` mezcla hazard interno y externo (Section 13.4).
- Cada uno usa una métrica diferente → las comparaciones no son transitivas.

**Problema**: si modelo y baselines no se evalúan en el mismo espacio de utilidad, `P(edge | D)` puede ser "verdadero" pero económicamente irrelevante. Por ejemplo: un modelo puede tener mejor Sharpe que H₀ pero peor drawdown, o mejor retorno pero peor tail risk. La decisión de "quién gana" depende de cómo se ponderen estos componentes, y esa ponderación debe ser explícita y única.

### 14.2 Utility Functional Único

Se define **un solo** functional de utilidad `U(τ)` que mapea cualquier trayectoria `τ` a un escalar. **Todo** (modelo, baselines, H₀) se evalúa con este mismo functional.

```
U(τ) = α₁ · R(τ) - α₂ · D(τ) - α₃ · V(τ) - α₄ · T(τ)
```

#### 14.2.1 Componentes

| Componente | Símbolo | Definición | Rango típico |
|---|---|---|---|
| **Retorno** | `R(τ)` | CAGR: tasa de crecimiento anual compuesta de la equity final | [−1.0, 5.0] |
| **Drawdown** | `D(τ)` | MaxDD: máximo drawdown desde peak (positivo, penalizado) | [0.0, 1.0] |
| **Volatilidad** | `V(τ)` | Desviación estándar anualizada de retornos diarios | [0.0, 3.0] |
| **Tail Loss** | `T(τ)` | CVaR 95%: media del 5% peor de retornos diarios (positivo, penalizado) | [0.0, 0.5] |

#### 14.2.2 Pesos por Defecto

| Peso | Valor | Rationale |
|---|---|---|
| `α₁` | 1.0 | Retorno es el objetivo primario |
| `α₂` | 2.0 | Drawdown penalizado 2× (consistente con perfil conservador, DD < 5%) |
| `α₃` | 0.5 | Volatilidad penalizada, pero no tanto como DD |
| `α₄` | 1.0 | Tail loss penalizado igual que drawdown |

Estos pesos son **configurables por el operador** (ver Section 12.2). Los valores por defecto representan un perfil conservador estándar.

#### 14.2.3 Normalización

Cada componente se normaliza a un score en [0, 1] antes de la suma ponderada:

```
R_norm(τ) = (R(τ) - R_min) / (R_max - R_min)
```

Donde `R_min` y `R_max` se estiman empíricamente del conjunto de trayectorias simuladas (modelo + baselines + H₀) en cada evaluación. Esto asegura que los pesos `α` sean interpretables independientemente de la escala de los datos.

#### 14.2.4 Propiedades de U(τ)

| Propiedad | ¿Se cumple? | Implicación |
|---|---|---|
| **Monotonicidad en wealth** | Sí | Mayor equity final → mayor `R(τ)` → mayor `U(τ)` |
| **Penalización de drawdown** | Sí | `D(τ)` penaliza paths con drawdowns profundos |
| **Penalización de volatilidad** | Sí | `V(τ)` penaliza paths erráticos |
| **Penalización de colas** | Sí | `T(τ)` penaliza paths con pérdidas extremas |
| **Transitividad** | Sí | Si `U(τ₁) > U(τ₂)` y `U(τ₂) > U(τ₃)`, entonces `U(τ₁) > U(τ₃)` |
| **Invarianza a escala** | Sí (vía normalización) | Los pesos `α` son interpretables independientemente del mercado o activo |

### 14.3 H₀ como Proceso Generativo Único (cierra Problema 1 de Section 13)

#### 14.3.1 Problema con el diseño anterior

Section 13.3 definía tres H₀ (Random Walk, Permutation, Cost-adjusted). Esto introduce:

- **Redundancia**: los tres comparten estructura (mismos retornos marginales, misma volatilidad, misma autocorrelación de costos).
- **Inflación de significancia**: `P(edge_abs | D) = P(supera H₀₁ ∧ H₀₂ ∧ H₀₃)` cuenta evidencia compartida múltiples veces → la probabilidad de edge absoluto se infla artificialmente.
- **Dependencia**: los tres H₀ no son independientes, pero la fórmula de Section 13.3.4 los trata como si lo fueran.

#### 14.3.2 Definición Correcta

H₀ ya no es un conjunto de modelos. H₀ es un **único proceso generativo** (una medida sobre el espacio de trayectorias) que satisface:

> Bajo H₀, las señales del sistema no contienen información predictiva más allá de ruido de lag-1.

Formalmente:

```
H₀ ∼ P(τ | constraints)
```

donde los constraints definen el **espacio de caminos sin edge**:

| Constraint | Significado | Implementación |
|---|---|---|
| **No predictabilidad direccional** | `E[pnl_i | features_i, regime_i] = 0` ∀i | Las señales del modelo se reemplazan por un proceso de ruido con media cero condicional a régimen |
| **Misma frecuencia de trades** | `P(trade | regime) = P_model(trade | regime)` | La distribución temporal de trades se preserva |
| **Misma estructura de costos** | `cost_i ∼ P(cost | regime)` | Los costos se modelan desde la distribución empírica del modelo |
| **Misma estructura de régimen** | Matriz de transición T idéntica al modelo (Section 11.3) | La dinámica de régimen no cambia |
| **Misma volatilidad** | `σ(pnl | regime) = σ_model(pnl | regime)` | La volatilidad por régimen se preserva |

#### 14.3.3 Generación de Trayectorias Bajo H₀

```
1. Obtener trades reales del modelo: T_model = {(timestamp_i, side_i, pnl_i, regime_i)}.
2. Construir señal nula para cada trade:
     - Para cada trade en régimen r, samplear side'_i ∼ Bernoulli(p=0.5)
       (sin direccionalidad) con misma probabilidad de BUY/SELL que el modelo en ese régimen.
     - Calcular pnl'_i como el pnl que habría resultado de tomar side'_i en lugar de side_i,
       bajo las mismas condiciones de mercado (mismo entry_price, exit_price).
   O alternativamente:
     - Permutar los side_i dentro de cada bloque de régimen (destruye correlación señal-mercado
       pero preserva distribución marginal de señales).
3. Concatenar los pnl'_i preservando el orden temporal → trayectoria τ_H₀.
4. Repetir M veces → {τ_H₀_s : s = 1...M}.
```

Esto produce trayectorias que son **indistinguibles en estructura** de las del modelo, excepto que no contienen información predictiva. Cualquier diferencia en `U(τ_model) - U(τ_H₀)` es evidencia de edge.

#### 14.3.4 Criterio de Edge Absoluto (redefinido)

```
P(edge_abs | D) = (1/M) · Σ_s 𝟙[ U(τ_model) > U(τ_H₀_s) ]
```

Donde:
- `U` es el utility functional único de Section 14.2.
- `τ_model` es la trayectoria real observada del modelo.
- `τ_H₀_s` es la s-ésima trayectoria generada bajo H₀.

**Interpretación**: `P(edge_abs | D) = 0.78` significa que en el 78% de los mundos sin edge (consistentes con los datos observados), el modelo real produce mayor utilidad que el proceso nulo. Es una **medida única** de edge absoluto, sin redundancia ni inflación de significancia.

#### 14.3.5 Relación con los Tres Tests Anteriores

| Test anterior | Ahora es | Por qué cambia |
|---|---|---|
| H₀₁ Random Walk | Proyección de H₀ sobre la distribución marginal de señales | Ya no es un test independiente; es una propiedad del proceso generativo |
| H₀₂ Permutation | Método de generación principal de H₀ (permutación dentro de bloques) | Se incorpora como implementación, no como hipótesis separada |
| H₀₃ Cost-adjusted | Parámetro del proceso generativo (estructura de costos) | Se incorpora como constraint, no como test separado |

Los tres tests anteriores **no desaparecen**: se fusionan en un solo proceso generativo que los contiene a todos como propiedades. La redundancia se elimina porque ahora solo hay **una** comparación: `U(τ_model)` vs `U(τ_H₀)`.

### 14.4 Mapeo de Todas las Políticas al Mismo Espacio

#### 14.4.1 Principio

Toda política (modelo, baseline, H₀) se evalúa en el **mismo espacio** `U(τ)` definido en Section 14.2. No existe comparación fuera de este espacio.

```
         ┌──────────────┐
         │  U(τ) space  │
         └──────────────┘
         ↙    ↓    ↘
    Modelo   H₀   Baselines
    (τ_m)   (τ_H₀)  (τ_b)
```

#### 14.4.2 Comparaciones Válidas

| Comparación | Fórmula | Significado |
|---|---|---|
| **Edge absoluto** | `P(U(τ_m) > U(τ_H₀) \| D)` | ¿El modelo supera al ruido? |
| **Edge relativo** | `P(U(τ_m) > U(τ_b) \| D)` | ¿El modelo supera a la estrategia simple? |
| **Utilidad de H₀** | `U(τ_H₀)` | Línea base de "no habilidad". Un modelo con `U(τ_m) < U(τ_H₀)` tiene **edge negativo** (peor que ruido). |
| **Utilidad del baseline** | `U(τ_b)` | Referencia de estrategia simple. |

Todas se reportan en el mismo JSON:

```json
{
  "utility_model": 0.72,
  "utility_h0": 0.31,
  "utility_best_baseline": 0.45,
  "p_edge_absolute": 0.78,
  "p_edge_relative": 0.65,
  "dominant_policy": "model"
}
```

### 14.5 Hazard Externo (redefinido)

Section 13.4 definía `h_externo(t) = P(H₀ supera al modelo)`. Con Section 14, esto se vuelve directo:

```
h_externo(t) = P( U(τ_H₀) > U(τ_model) | market_conditions(t) )
```

Es decir: el hazard externo es la probabilidad de que **un proceso sin edge** genere más utilidad que el modelo, dado el régimen de mercado actual.

Esto se computa como parte del mismo Monte Carlo de Section 11:

```
1. Simular M trayectorias bajo H₀ (Section 14.3.3).
2. Simular M trayectorias del modelo (Section 11.4, Modo A).
3. h_externo = (1/M) · Σ_s 𝟙[ U(τ_H₀_s) > U(τ_model_s) ].
```

### 14.6 Relación con el EDL (Sections 7–10)

El EDL produce `P(edge | D, r, m)` vía actualización bayesiana episodio por episodio. La Utility Measure (Section 14) produce `P(edge_abs | D)` vía comparación de políticas completas en el espacio `U`.

| Propiedad | EDL (Secciones 7–10) | Utility Measure (Section 14) |
|---|---|---|
| Unidad | Episodio individual | Trayectoria completa |
| Método | Actualización secuencial bayesiana | Monte Carlo sobre espacio de paths |
| Output | `P(edge \| D, r, m)` por régimen | `P(edge_abs \| D)` global |
| Dependencia | Prior + Likelihood por episodio | U(τ) functional + H₀ generativo |
| Uso | Inferencia fina, detección temprana | Decisión gruesa, control de riesgo |

Ambas capas coexisten. La Section 14 no reemplaza al EDL: la inferencia episódica del EDL es más sensible para detección temprana de edge/decay, mientras que la Utility Measure es más robusta para decisiones de control.

### 14.7 Implementación

```
scripts/utility_measure.py
```

Interfaz:

```python
@dataclass(frozen=True)
class UtilityConfig:
    alpha_return: float = 1.0
    alpha_drawdown: float = 2.0
    alpha_volatility: float = 0.5
    alpha_tail_loss: float = 1.0

class UtilityMeasure:
    def __init__(self, config: UtilityConfig = UtilityConfig())
    def evaluate(self, equity_curve: pd.Series) -> float
    def compare(self, model: pd.Series, h0: pd.Series,
                baselines: dict[str, pd.Series]) -> UtilityReport
    def edge_probability(self, model: pd.Series, h0_samples: list[pd.Series]) -> float

class H0Generator:
    def __init__(self, episodes: list[TradeEpisode])
    def generate(self, n_paths: int = 10000) -> list[pd.Series]
```

### 14.8 Resumen de Decisiones Cerradas

| Decisión | Antes (Section 13) | Ahora (Section 14) |
|---|---|---|
| **H₀** | Ensemble de 3 tests (redundante) | Proceso generativo único (measure space) |
| **Edge absoluto** | `P(supera H₀₁ ∧ H₀₂ ∧ H₀₃)` → inflado | `P(U(τ_m) > U(τ_H₀))` → única, sin redundancia |
| **Espacio de comparación** | Sharpe, drawdown, dominance (múltiples espacios) | `U(τ)` funcional único (mismo espacio para todos) |
| **Hazard externo** | `P(H₀ > model)` sin definir espacio | `P(U(τ_H₀) > U(τ_model))` en el mismo `U` |
| **Tests anteriores** | H₀₁, H₀₂, H₀₃ como entidades separadas | Proyecciones del mismo proceso generativo H₀ |

Con Section 14, el sistema ahora tiene:
1. **Un solo** functional de utilidad `U(τ)` — define qué significa "mejor".
2. **Un solo** proceso generativo H₀ — define qué significa "no edge".
3. **Un solo** espacio de comparación — toda política se evalúa en `U(τ)`.

No hay más ambigüedad matemática. El sistema es implementable.

---

## 15. EIA — Edge Identifiability Axiom

Formal specification of when "edge" is a well-defined and measurable quantity in the system.

### 15.1 Motivation

In trading systems, most "edge detectors" fail not because of poor modeling, but because they violate a deeper constraint: they define edge as a property of a model, instead of a property of **separability between competing generative hypotheses under controlled counterfactuals**.

This leads to two failure modes:

- **False edge inflation**: apparent predictability caused by a weak or biased null model.
- **Null overfitting**: results that depend on the specific construction of H₀ rather than the data-generating process.

To prevent both, we define edge not as a scalar metric, but as a structurally identifiable latent quantity.

### 15.2 Core Objects

Let:

- `D`: observed trade episodes (frozen, canonicalized dataset).
- `τ`: trajectory functional over episodes (includes timing, sequencing, exposure path).
- `H₁`: alternative hypothesis (structured strategy-induced process).
- `H₀ ∈ ℍ₀`: class of null generative processes.
- `U(τ)`: utility functional (fixed α, invariant across models and nulls).

We define:

```
U₁ = U(τ | H₁, D)
U₀ = U(τ | H₀, D)
```

### 15.3 Edge as a Latent Vector

Edge is defined as a vector-valued latent variable:

```
E(D) = [E_d, E_t, E_e, E_s]

where:

E_d = Directional Edge    (sign-consistency of returns vs H₀)
E_t = Timing Edge         (entry distribution vs optimal stopping under H₀)
E_e = Execution Edge      (cost-adjusted slippage advantage vs H₀)
E_s = Structural Edge     (regime clustering + path dependency advantage vs H₀)
```

Each component is defined through contrastive expectation separation:

```
E_i(D) = E[f_i(τ) | H₁, D] - E[f_i(τ) | H₀, D]
```

where `f_i` are projections of the trajectory functional onto orthogonal behavioral axes.

### 15.4 Null Class Requirement

The null is not a single model but a **distribution over admissible generative processes**:

```
ℍ₀ = { H₀¹, H₀², ..., H₀ⁿ }
```

Each representing distinct failure modes:

- Permutation-based randomness (Section 14.3)
- Regime-conditioned stochastic processes
- Cost-adjusted random walks
- Block-correlated noise processes
- Execution noise models (slippage + latency + cost distortion)

### 15.5 Edge Identifiability Axiom (EIA)

Edge is well-defined and measurable if and only if:

**(1) Separability Condition**

There exists a non-zero margin Δᵢ > 0 such that:

```
E[f_i(τ) | H₁, D] - E[f_i(τ) | H₀, D] > Δᵢ
```

for all components i ∈ {d, t, e, s}.

**(2) Null Invariance Condition**

For all admissible null models:

```
∀ H₀ᵃ, H₀ᵇ ∈ ℍ₀ :
  |E[f_i(τ) | H₀ᵃ, D] - E[f_i(τ) | H₀ᵇ, D]| ≪ Δᵢ
```

This ensures that edge is not an artifact of null selection.

**(3) Representation Stability Condition**

Under perturbations of trajectory sampling, utility scaling, or block bootstrap resampling:

```
Var_ϵ(E_i(D)) ≪ Δᵢ
```

This ensures that edge is stable under admissible transformations of the inference pipeline.

### 15.6 Edge Existence Criterion

The system declares that a component Eᵢ is **identifiable** if:

```
EIA(Eᵢ) = (1) ∧ (2) ∧ (3)
```

Edge exists globally if:

```
∃ i ∈ {d, t, e, s} such that EIA(Eᵢ) = true
```

and is considered:

- **Partial Edge**: only a subset of components are identifiable.
- **Full Edge**: all components identifiable and consistent across regimes.

### 15.7 Implications

This axiom implies:

- **Edge is not a scalar** — it is a structured latent object.
- **No single H₀ is valid** — only classes of null processes.
- Any measurable edge must survive:
  - Null variation (Null Invariance)
  - Trajectory perturbation (Representation Stability)
  - Utility reparameterization within α-invariant class

### 15.8 Non-Identifiability Failure Modes

If EIA fails, the system is in one of three states:

| State | Symptom | Cause |
|---|---|---|
| **Underdetermined regime** | E(D) ≈ 0 for all H₀ | Insufficient data to separate H₁ from H₀ |
| **Null-sensitive regime** | E(D) changes with H₀ choice | Results depend on arbitrary null selection |
| **Degenerate utility regime** | Collapsed U variance | U(τ) loses signal structure |

In all cases:

```
E(D) is undefined (not noisy — unidentifiable)
```

### 15.9 Final Statement

> Edge is not defined by profitability, predictability, or Sharpe. Edge is defined by **invariance of separation between hypotheses under a class of admissible counterfactual worlds**.

---

## 16. Edge Tensor Formal Definition (Φ Operator)

Mapping trajectories into identifiable edge components under hypothesis-conditioned counterfactuals.

### 16.1 Purpose

The Edge Identifiability Axiom (Section 15) defines *when* edge exists. This section defines *what* edge is in computable form.

We formalize edge as a **tensor of separable projections** over trajectory space, conditioned on hypotheses and regimes.

### 16.2 Trajectory Space

Let `τ ∈ 𝕋` be a trajectory object defined as:

```
τ = (E, P, R, C)
```

where:

- `E`: ordered trade episodes (canonicalized, immutable).
- `P`: equity path induced by execution.
- `R`: regime sequence (latent or inferred).
- `C`: cost / friction path (fees, slippage, funding).

Thus `τ` is not a sequence of trades alone, but a **stateful execution manifold**.

### 16.3 Hypothesis-Conditioned Worlds

We define:

- `H₁`: strategy-induced generative process.
- `H₀ ∈ ℍ₀`: admissible null processes.

Each hypothesis induces a distribution over trajectories:

```
τ ∼ P(τ | H)
```

### 16.4 Edge as Tensor Object

We define the **Edge Tensor**:

```
E(D) = [E_d, E_t, E_e, E_s]ᵀ = Φ(τ | H₁, ℍ₀)
```

where Φ is a **projection operator** over trajectory space.

Each component corresponds to a distinct projection axis:

| Component | Meaning | Projection Domain |
|---|---|---|
| `E_d` | Directional edge | Sign-consistency of returns vs H₀ |
| `E_t` | Timing edge | Entry distribution vs optimal stopping under H₀ |
| `E_e` | Execution edge | Cost-adjusted slippage advantage |
| `E_s` | Structural edge | Regime clustering + path dependency advantage |

### 16.5 Projection Operator Φ

Φ is defined as the expectation difference across hypotheses:

```
Φ(τ | H₁, ℍ₀) = E_H₁[f(τ)] - E_ℍ₀[f(τ)]
```

but crucially:

```
f(τ) = { f_d(τ), f_t(τ), f_e(τ), f_s(τ) }
```

Each function extracts a sufficient statistic over trajectories (not individual trades).

### 16.6 Component Definitions

#### (1) Directional Edge — E_d

```
E_d = E[sign(r_i) | H₁] - E[sign(r_i) | H₀]
```

Measures deviation from random directional exposure.

#### (2) Timing Edge — E_t

```
E_t = E[t_entry - t*_optimal | H₁] - E[t_entry - t*_optimal | H₀]
```

where `t*_optimal` is defined as `argmin` expected regret under H₀ stopping distribution.

#### (3) Execution Edge — E_e

```
E_e = E[C_H₀(τ)] - E[C_H₁(τ)]
```

Measures cost efficiency vs null execution path.

#### (4) Structural Edge — E_s

```
E_s = I(τ; R | H₁) - I(τ; R | H₀)
```

where `I` is mutual information between trajectory and regime sequence. This captures:

- Clustering of trades
- Regime persistence exploitation
- Path dependency structure

### 16.7 Null-Class Dependency

All projections are evaluated against:

```
ℍ₀ = { H₀(perm), H₀(block), H₀(cost), H₀(rw) }
```

The Edge Tensor is valid iff it is stable under:

```
∀ H₀ᵢ ∈ ℍ₀ : sign(Φ(τ | H₁, H₀ᵢ)) = constant
```

This enforces EIA Null Invariance (Section 15.5) at tensor level.

### 16.8 Identifiability Constraint (link to EIA)

The Edge Tensor is identifiable if:

```
E(D) ≠ 0  ∧  Var_ℍ₀(E) ≪ ‖E‖
```

and satisfies all three EIA conditions (Section 15.5):

- Separability
- Null invariance
- Representation stability

### 16.9 Operational Interpretation

The system does NOT observe edge directly. It observes:

> **Divergences between trajectory projections under H₁ and a class of H₀ worlds.**

Thus:

- Edge is **not** computed.
- Edge is **inferred** as a stable asymmetry across counterfactual projections.

### 16.10 Key Consequences

1. **No single null model is sufficient** — Edge must survive a null *class*, not a null instance.
2. **Edge is not scalarizable** — Any scalar reduction loses identifiability information.
3. **Different components can disagree** — It is valid that `E_d > 0, E_t ≈ 0, E_s < 0`. This is not a contradiction; it is diagnostic structure.

### 16.11 Final Statement

> Edge is not a property of a strategy. Edge is a **vector of invariant asymmetries** between observed trajectories and a class of admissible counterfactual worlds.

---

## 17. EILS — Edge Inference Layer Specification

Mapping empirical data into Edge Tensor components under hypothesis-conditioned projections.

### 17.1 Purpose

The Inference Layer defines how the system transforms `D → E(D)` without introducing artificial structure beyond that already present in trajectory space.

It is explicitly a **distributional estimator**, not a point estimator.

### 17.2 Core Principle

Each edge component is defined as:

```
E_i = E_{τ∼P(τ|H₁)}[f_i(τ)] - E_{τ∼P(τ|H₀)}[f_i(τ)]
```

But in practice:
- `P(τ|H₁)` is not observed
- `P(τ|H₀)` is not observed

Therefore:

> All inference is performed via **paired counterfactual sampling**.

### 17.3 Paired Counterfactual Estimation (PCE)

For each observed trajectory `τ⁽ᵏ⁾`:

1. Generate K counterfactuals under H₀: `{τ¹_H₀⁽ᵏ⁾, ..., τᴷ_H₀⁽ᵏ⁾}`
2. Keep H₁ trajectory fixed (observed execution path)
3. Compute: `Δf_i⁽ᵏ⁾ = f_i(τ_H₁⁽ᵏ⁾) - (1/K) Σⱼ f_i(τ_H₀ⱼ⁽ᵏ⁾)`
4. Aggregate: `E_i = (1/N) Σₖ Δf_i⁽ᵏ⁾`

### 17.4 Null-Averaging Constraint (NAC)

To satisfy EIA Null Invariance (Section 15.5):

```
Var_ℍ₀(E_i) < ϵ · ‖E[E_i]‖
```

This enforces:
- No single H₀ dominates inference
- No null-specific artifacts propagate into tensor

### 17.5 Bootstrap Structure Requirement

Inference is performed on **block-bootstrap trajectories** (not trades), regime-conditioned segments, execution-consistent paths.

Formally:

```
τ⁽ᵏ⁾ ∼ BlockBootstrap(D, R)
```

where blocks preserve:
- Autocorrelation
- Regime persistence
- Exposure clustering

### 17.6 Component Estimators

**(1) Directional Edge — E_d**

```
E_d = E[sign(r)]_H₁ - E[sign(r)]_H₀
```

Estimator: binomial contrast over matched trajectories.

**(2) Timing Edge — E_t**

```
E_t = E[t_entry - t*]_H₁ - E[t_entry - t*]_H₀
```

Estimator: regret-distance to H₀ stopping distribution, where `t*` is the optimal entry under H₀.

**(3) Execution Edge — E_e**

```
E_e = E[C(τ)]_H₀ - E[C(τ)]_H₁
```

Estimator: cost differential under identical execution paths.

**(4) Structural Edge — E_s**

```
E_s = I(τ; R)_H₁ - I(τ; R)_H₀
```

Estimator: mutual information via kNN estimator or regime entropy reduction delta.

### 17.7 Stability Filter (Critical)

Before any component is accepted:

```
|E_i| > λ · σ_ℍ₀(E_i)
```

If not satisfied, the component is declared **UNIDENTIFIABLE**, not zero.

This is essential for EIA compliance (Section 15.5).

### 17.8 Horizon Consistency Constraint

Edge must be invariant across rolling windows, bootstrap samples, and regime slices:

```
Var_t(E_i) < δ
```

Otherwise edge is regime-bound, not system-level.

### 17.9 Output Semantics

Inference Layer outputs:

```json
{
  "E_d": {"mean": 0.0, "std": 0.0, "ci": [lower, upper]},
  "E_t": {"mean": 0.0, "std": 0.0, "ci": [lower, upper]},
  "E_e": {"mean": 0.0, "std": 0.0, "ci": [lower, upper]},
  "E_s": {"mean": 0.0, "std": 0.0, "ci": [lower, upper]},
  "identifiability": {
    "directional": true,
    "timing": false,
    "execution": true,
    "structural": true
  }
}
```

The system NEVER outputs a single edge value — only distributions.

### 17.10 Key Consequences

1. **Edge is a statistical object, not a computed metric** — You never "calculate edge", only estimate separation.
2. **Null models are baselines of variance** — Their role is to define the noise floor of identifiability.
3. **Failure is informative** — "UNIDENTIFIABLE" is a valid and expected output state.

### 17.11 Final Statement

> The Inference Layer does not decide whether edge exists. It decides whether edge is **identifiable under admissible counterfactual uncertainty**.

---

## 18. H₀ Engine — Unified Null Transformation System

Counterfactual trajectory generation under structure-preserving constraints.

### 18.1 Purpose

The H₀ Engine defines how counterfactual worlds are constructed for inference. It is not a simulator of randomness. It is a **trajectory transformation system** that preserves structural invariants while breaking signal alignment.

Formally:

```
H₀ : τ → τ̃
```

such that:
- Structural properties are preserved
- Predictive alignment is destroyed
- Utility functional remains comparable under U(τ)

### 18.2 Core Principle

All valid nulls must satisfy:

**Constraint Preservation Axiom (CPA)**:

```
C(τ) = C(τ̃)
```

where `C = {cost, regime path, execution timing distribution, exposure profile}`.

But:

```
Corr(τ, market signal) → 0
```

### 18.3 Unified Null Representation

Instead of multiple H₀ models, we define:

```
H₀⁽ᵏ⁾ = Tₖ(τ)
```

where `Tₖ ∈ {perm, block, regime-shuffle, cost-preserving noise}`.

Each is a **trajectory transformation operator**, not a generative model.

### 18.4 Required Null Operators

**(1) Permutation Null — H₀₁ (existing, Section 14.3)**

```
T_perm(τ): (r_i, t_i, p_i) → (r_πⁱ, t_i, p_i)
```

- Breaks signal alignment
- Preserves marginal distributions

**(2) Block Null — H₀₂**

```
T_block(τ): Bⱼ → B_π⁼
```

where `Bⱼ` are contiguous regime-consistent trade blocks.

Preserves:
- Autocorrelation
- Clustering
- Exposure structure

Breaks:
- Global sequencing signal

**(3) Regime Shuffle Null — H₀₃**

```
T_regime(τ): R → R̃
```

with regime labels permuted or Markov-resampled; within-regime structure preserved.

Tests regime dependence of edge and structural robustness.

**(4) Cost-Preserving Noise Null — H₀₄**

```
T_cost(τ): r_i → r_i + ϵ_i
```

subject to `Σ ϵ_i = 0`.

Preserves:
- Net PnL expectation
- Cost structure

Breaks:
- Fine-grained signal consistency

### 18.5 Unified Null Class

We define:

```
ℍ₀ = { Tₖ(τ) }⁴ₖ₌₁
```

All nulls operate on the **same observed trajectory**, not independent samples. This is what guarantees EIA compatibility.

### 18.6 Null Equivalence Condition (NEC)

For the system to be valid:

```
E[U(τ)] ≈ E[U(Tₖ(τ))]   ∀k
```

But:

```
Cov(U(τ), signal) ≫ Cov(U(Tₖ(τ)), signal)
```

Meaning: utility preserved, predictive structure destroyed.

### 18.7 Integration with Inference Layer

Each inference step uses `τ vs Tₖ(τ)` to compute directional separation, timing distortion, execution advantage, and structural dependence.

Thus:

```
E_i = E[f_i(τ)] - E[f_i(Tₖ(τ))]
```

averaged over all k.

### 18.8 Critical Design Property

> Nulls are not independent worlds. They are **counterfactual projections of the same world under structure-preserving distortion operators**.

This avoids:
- Dataset leakage
- Regime mismatch artifacts
- Synthetic-world bias

### 18.9 Failure Modes

If any operator violates CPA:

- Edge inflation occurs (false positives)
- Structural edge collapses into regime artifacts
- EIA Null Invariance is broken

Detection rule:

```
Varₖ(E_i) > ϵ ⇒ INVALID NULL CLASS
```

### 18.10 Final Statement

> The H₀ Engine does not simulate alternative markets. It constructs structurally consistent counterfactual distortions of the same market trajectory.

---

## 19. FEOT — Full Edge Observability Theorem

Conditions under which edge is observable without structural or inferential bias.

### 19.1 Purpose

This theorem defines the necessary and sufficient conditions under which the system can observe Edge Tensor components `E(D) = [E_d, E_t, E_e, E_s]` without contamination from:

- Null model selection bias
- Trajectory reconstruction artifacts
- Utility functional degeneracy
- Regime-specific overfitting

It formalizes when edge is not only identifiable, but **observable in a stable, invariant way**.

### 19.2 Definitions

Let:
- `D`: observed dataset of trade episodes
- `τ`: trajectory functional
- `ℍ₀`: unified null class (Section 18)
- `Φ`: edge projection operator (Section 16)
- `I`: inference layer estimator (Section 17)

We define:

```
Ê(D) = I(Φ(τ), ℍ₀)
```

### 19.3 Observability Definition

Edge is observable if:

```
∃ Ê(D) ≠ 0  ∧  Var_ℍ₀(Ê) < ϵ
```

and:

```
Ê(D) ≈ E(D)
```

within bounded inference error.

### 19.4 Full Observability Theorem

**Theorem (Edge Observability).** The Edge Tensor is observable if and only if the following three conditions hold:

**(1) Identifiability (EIA satisfied).** `E(D)` satisfies Section 15 (EIA), i.e.:
- Separability holds
- Null invariance holds
- Representation stability holds

**(2) Null Stability.** For all admissible null transformations:

```
∀ Tₖ ∈ ℍ₀ : Varₖ(Φ(τ | Tₖ)) < δ
```

Meaning the choice of null transformation does not materially affect the estimated Edge Tensor.

**(3) Inference Consistency.** For all admissible resampling schemes S:

```
E[Ê_S(D)] ≈ Ê(D)
```

and:

```
Var_S(Ê) ≪ ‖Ê‖
```

Meaning the inference pipeline is stable under sampling noise, bootstrap perturbation, and regime slicing.

### 19.5 Failure Modes (Non-Observability States)

If any condition fails:

| Case | Failure | Consequence |
|---|---|---|
| **A** | EIA fails | Edge does not exist as measurable object |
| **B** | Null stability fails | Edge depends on choice of H₀ |
| **C** | Inference consistency fails | Edge exists but cannot be reliably estimated |

In all cases, **Ê(D) is not a valid scientific object**.

### 19.6 Key Consequence

> Edge is observable only if it survives **both** world perturbation (H₀ class) **and** estimator perturbation (inference layer).

This creates a double robustness requirement:
- Structural robustness (EIA + Φ + H₀)
- Statistical robustness (inference layer)

### 19.7 Corollary (Practical Meaning)

A system may show:
- High Sharpe
- Consistent PnL
- Stable backtests

and still **not have observable edge** if it fails either null stability or inference consistency.

### 19.8 Final Statement

> Edge is not what maximizes performance. Edge is what remains **invariant under both counterfactual transformation and inferential perturbation**.
