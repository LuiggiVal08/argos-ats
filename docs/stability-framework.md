# System Stability Framework — Fase 0

> **Propósito**: Definir las invariantes absolutas del sistema, la máquina de estados del kill-switch,
> el ExecutionGate como punto único de decisión de trading, el TradingEngine como único entrypoint
> de ejecución, la jerarquía del control plane, y las transiciones entre estados.
>
> **Fase**: 0 — Diseño de seguridad (previa a STABILITY_GATES.md).
> **Orientación**: CONSERVADOR (trading infra).
> **Principio rector**: La seguridad no depende de reacción, sino de **imposibilidad de estados inválidos**.

---

## 1. Invariantes del sistema (absolutos)

Condiciones booleanas. No tienen umbrales numéricos — son leyes.
Si una invariante se viola, el sistema **transiciona de estado inmediatamente**.

### 1.1 Pipeline Integrity

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I1 | `pipeline.is_loaded == true` para cualquier operación de trading | `ExecutionGate` llama a `pipeline.assert_loaded()` que lanza si no está listo. No se evalúa la flag, se exige el estado |
| I2 | Señales de inferencia sin excepciones (NaN, inf, errores de modelo) | El predictor falla ruidosamente (log + halt) ante cualquier output inválido, no lo silencia |
| I3 | Latencia candle→decision medible y no creciente en el tiempo | El pipeline trackea latencia internamente y emite alerta si la media móvil (ventana 10) supera el p95 histórico |

### 1.2 Execution Safety

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I4 | Toda posición abierta tiene SL activo **en exchange real** (no solo en DB local) | `open_position()` no retorna éxito sin confirmación del exchange de que la SL order existe (orderId + status). La posición no se persiste como `OPEN` en DB hasta esa confirmación |
| I5 | SL fail → emergency close inmediato. Si emergency close también falla → CRITICAL_UNPROTECTED | `place_composite_order` lanza `SlPlacementError` con la entry ya ejecutada; el handler no puede seguir sin resolver |
| I6 | Una orden activa máxima por posición (sin duplicados) | El exchange adapter verifica que no hay órdenes activas para el mismo símbolo+lado antes de enviar |
| I7 | No ejecutar órdenes si hay reconciliation pendiente | `ExecutionGate` consulta `reconciliation.status` antes de toda operación. `reconciliation.status != MATCHED` → block |

### 1.3 Data Integrity

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I8 | 0 ticks inválidos aceptados por pipeline | `Tick.create()` rechaza (throw) cualquier input que no cumpla el schema; el adapter no hace catch silencioso |
| I9 | Decisiones solo sobre candles completas (timeframe cerrado) | `_decision_loop` no consume candles en formación. Espera a que `CandleBuilder` marque la candle como `completed` |
| I10 | Stream de ticks sin gaps > 30s sin alerta | El monitor de integridad emite evento de anomalía en el momento, no en el próximo ciclo de diagnóstico |
| I20 | Tick continuity / time monotonicity sin saltos de tiempo | El stream verifica que `tick.timestamp` > `last_tick.timestamp`. Cero tolerancia a ticks desordenados |

### 1.4 Position State

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I11 | Posición local ≈ posición exchange (reconciliation matching) | Si `reconciliation.reconcile()` retorna `MISSING_ON_EXCHANGE` o `MISSING_LOCAL`, `ExecutionGate` bloquea toda operación hasta resolver |
| I12 | No abrir posición si CRITICAL_UNPROTECTED activo en el sistema | `ExecutionGate` lee el estado global del kill-switch antes de proceder. `state == CRITICAL_UNPROTECTED` → block incondicional |
| I13 | Drawdown calculable (no null) en todo momento | Si drawdown es null persistentemente (más de 1 ciclo de diagnóstico), el sistema no puede evaluar riesgo → SOFT_HALT |
| I18 | SL/TP existen en exchange real, no solo en DB local | Posición no se persiste como `OPEN` hasta que la SL order está confirmada en exchange con `orderId` y `status=open` |
| I19 | Divergencia exchange vs local state | Si reconciliation encuentra mismatch (side, units, entry_price), `ExecutionGate` bloquea ejecución hasta resolver |

### 1.5 System State

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I14 | Recovery completado antes de operar | `main.py` no arranca los loops de decisión hasta que `RecoveryGate` retorne `SAFE` |
| I15 | Secrets presentes en LIVE | `preflight.abort_if_missing(LIVE)` se ejecuta **antes** de cualquier construcción de adapters — si falta, `sys.exit(1)` |
| I16 | `ENVIRONMENT_MODE` consistente en todos los componentes | El valor se lee al boot y se propaga a todos los subsistemas; cambio de modo requiere restart |
| I21 | Model output sanity bounds | `confidence ∈ [0,1]`, sin NaN/inf. El predictor valida output del modelo antes de construir `TradingSignal` |
| I22 | Risk exposure cap — max notional exposure definido | Incluso si todo funciona, el sistema no expone más de X notional. `ExecutionGate` verifica antes de aprobar |
| I23 | Partial failure tolerance — un componente fallando no debe degradar todo el sistema silenciosamente | Cada loop del streaming engine tiene su propio `try/except` con logging explícito. Caída de un loop no detiene los demás |

### 1.6 Order Integrity

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I17 | Idempotencia de órdenes — mismo signal_id no puede generar 2 entradas | El store de execution_signals tiene `signal_id` como PK única con `ON CONFLICT DO NOTHING`. `ExecutionGate` verifica antes de aprobar |
| I24 | Time monotonicity en órdenes | Toda orden tiene timestamp > última orden para el mismo símbolo+lado. Previene race conditions |
| I25 | No orphan SL orders | Si una posición se cierra, todas las órdenes SL/TP asociadas se cancelan. Verificación post-close |

### 1.7 Global Ordering (Causal Consistency)

| ID | Invariante | Principio de imposibilidad |
|----|-----------|---------------------------|
| I26 | Causal consistency: la base de información de toda ejecución está causalmente alineada con la última candle cerrada y reconciliada | `TradingEngine` recibe el `causal_context` del sistema. Toda señal debe referenciar una `candle_id` cuya versión (`candle_hash`) coincida con la última reconciliada. Si hay gap o desorden causal → `TradingEngine` no entrega la señal al `ExecutionGate` |

**Jerarquía causal** (completa):
```
tick.timestamp < candle.close_time
candle.close_time ≤ decision.timestamp
decision.timestamp ≤ execution.timestamp
execution.timestamp ≥ reconciliation_confirmed_timestamp
```

**Event versioning**:
- Cada candle tiene un `candle_id` (secuencial, ej: símbolo + índice de vela) y un `candle_hash` (SHA256 del contenido OHLCV)
- El reconciliation engine mantiene `last_reconciled_candle_id` y `last_reconciled_candle_hash`
- El `causal_context` incluye: `last_tick_timestamp`, `last_candle_close_time`, `last_reconciled_candle_id`, `last_reconciled_candle_hash`, `last_execution_timestamp`, `reconciliation_confirmed_timestamp`

**Imposibilidad**:
- Si `decision.timestamp < candle.close_time` → reject (data incompleta)
- Si `execution.timestamp < reconciliation_confirmed_timestamp` → reject (estado no reconciliado)
- Si `decision.candle_id ≠ causal_context.last_reconciled_candle_id` → reject (candle incorrecta)
- Si `decision.candle_hash ≠ causal_context.last_reconciled_candle_hash` → reject (contenido de candle cambiado post-reconciliation)
- Si `execution.timestamp < decision.timestamp` → reject (violación de orden causal)
- El `causal_context` es inmutable durante un ciclo de decisión y solo se actualiza cuando el reconciliation engine completa un ciclo

---

## 2. Kill-switch: máquina de estados

### 2.1 Estados

```
NORMAL ◄──► DEGRADED ◄──► SOFT_HALT ──► CRITICAL_UNPROTECTED ──► SYSTEM_CIRCUIT_BREAKER
                ▲                                               │
                └──────────────────── restart manual ────────────┘
```

| Estado | Naturaleza | Trading | Consume data | Señales entrantes | Observability | Recovery posible |
|--------|-----------|---------|-------------|-------------------|---------------|------------------|
| NORMAL | Estable | Full (abre + gestiona + cierra) | ✅ | Procesar normalmente | ✅ | — |
| DEGRADED | Estable | No abre nuevas. Gestiona existentes. Cierra si toca SL/TP | ✅ | **Acumular en cola de revisión** (log + store, no descartar) | ✅ | ✅ automático → NORMAL |
| SOFT_HALT | Estable | Solo cierra existentes (SL/TP/emergency). No abre, no gestiona | ✅ | **Descartar con log** (no acumular — la razón del halt puede ser la señal misma) | ✅ | ✅ automático → NORMAL |
| CRITICAL_UNPROTECTED | **Transitorio** | Freeze total. Solo recovery process. Timer 30s | ❌ (solo recovery) | **Descartar con log** | ✅ | ⚠️ timeout **AND** reconciliation |
| SYSTEM_CIRCUIT_BREAKER | **Terminal** | Apagado completo | ❌ | **Descartar** | ✅ (mínima, health endpoint) | ❌ Solo restart manual |

### 2.2 Reglas de transición

| Evento | Estado origen | Estado destino | Trigger | Tiempo |
|--------|--------------|---------------|---------|--------|
| Pipeline not loaded | NORMAL | SOFT_HALT | `pipeline.is_loaded == false` | Inmediato |
| SL fail (y emergency close ok) | NORMAL / SOFT_HALT | CRITICAL_UNPROTECTED | `SlPlacementError` + emergency ejecutado | Inmediato |
| SL fail + emergency close fail | NORMAL / SOFT_HALT | CRITICAL_UNPROTECTED | Posición sin SL en exchange | Inmediato |
| Reconciliation desync | NORMAL | SOFT_HALT | `reconciliation.status != MATCHED` | Inmediato |
| Tick parse error persistente (≥3 en 60s) | NORMAL | DEGRADED | Contador de parse errors | 60s ventana |
| Latencia candle→decision trending up | NORMAL | DEGRADED | Media móvil > p95 histórico | 3 muestras consecutivas |
| Drawdown null persistente | NORMAL / DEGRADED | SOFT_HALT | 2 ciclos de diagnóstico sin drawdown | ~120s |
| CRITICAL_UNPROTECTED sin resolver | CRITICAL_UNPROTECTED | SYSTEM_CIRCUIT_BREAKER | Timeout 30s **AND** recovery no completado | 30s |
| Secrets faltantes en LIVE | — | SYSTEM_CIRCUIT_BREAKER | `preflight.abort_if_missing` | Boot |
| Recovery no completado | — | SYSTEM_CIRCUIT_BREAKER | `RecoveryGate` retorna `BLOCKED` | Boot |
| Idempotencia violada (signal duplicado) | NORMAL | SOFT_HALT | Signal ID ya ejecutado | Inmediato |
| Divergencia exchange vs local position | NORMAL | SOFT_HALT | Reconciliation mismatch | Inmediato |

### 2.3 Transiciones de recuperación

| Estado origen | → | Estado destino | Condición |
|--------------|---|---------------|-----------|
| DEGRADED | → | NORMAL | 10 minutos sin nuevas violaciones de invariantes |
| SOFT_HALT | → | NORMAL | Reconciliation OK + pipeline OK + todas las invariantes OK por 5 minutos |
| CRITICAL_UNPROTECTED | → | SOFT_HALT | Recovery completado **AND** reconcilación exchange exitosa **AND** confirmación externa (SL orderId verificado en exchange) |
| CRITICAL_UNPROTECTED | → | SYSTEM_CIRCUIT_BREAKER | Timeout 30s alcanzado **AND** recovery no completado |

### 2.4 Diseño de CRITICAL_UNPROTECTED

CRITICAL_UNPROTECTED es un **estado transitorio peligroso**, no operativo.
No es un estado estable — su existencia es un bug del sistema, no una condición operativa normal.

- Duración máxima: **30 segundos**
- Durante esos 30s, solo el **recovery process** está activo
- Recovery debe completar **todas** estas condiciones (AND, no OR):
  1. Restaurar SL en exchange (vía `place_stop_loss_order`)
  2. Verificar que la SL order está activa en exchange (orderId + status consultado directamente, no desde DB local)
  3. Reconciliar estado local vs exchange (side, units, entry_price deben coincidir)
  4. Confirmar posición protegida post-recovery
- Si **cualquier** condición falla o el timeout expira → SYSTEM_CIRCUIT_BREAKER (restart manual)
- No existe "CRITICAL_UNPROTECTED permanente" — si se queda trabado, es un bug del recovery
- El timeout no es la única condición de escape: recovery exitoso antes del timeout → SOFT_HALT. Timeout no es sinónimo de fallo si recovery completó antes

### 2.5 Backpressure semantics

Para cada estado, las señales entrantes tienen un tratamiento específico:

| Estado | Señales de inferencia | SL/TP events | Órdenes manuales | Reconciliation |
|--------|----------------------|--------------|-------------------|----------------|
| NORMAL | Procesar → ExecutionGate | Ejecutar | Permitir | Activa (cada N ciclos) |
| DEGRADED | **Acumular** en cola de revisión (log + store, sin descartar). ExecutionGate bloquea nuevas posiciones. Gestión de existentes permitida | Ejecutar | Bloquear | Activa |
| SOFT_HALT | **Descartar** con log structurado (incluir reason=state). No acumular | Ejecutar solo cierres | Bloquear | Activa |
| CRITICAL_UNPROTECTED | **Descartar** con log | Freeze: no procesar nada | Bloquear | Solo recovery |
| SYSTEM_CIRCUIT_BREAKER | Descartar | Descartar | Bloquear | Inactiva |

**Principio de DEGRADED signal queue**: las señales acumuladas en DEGRADED no se "reprocesan" al volver a NORMAL. Son audit trail, no backlog. Si el sistema no pudo ejecutar durante DEGRADED, no debe intentar ponerse al día — podría apilar señales obsoletas.

### 2.6 DEGRADED execution policy

**Pregunta**: ¿ExecutionGate permite ejecutar en DEGRADED?

**Respuesta**: Depende del **tipo de degradación**. Se definen dos categorías:

| Tipo de degradación | Ejemplos | ExecutionGate policy |
|--------------------|----------|---------------------|
| **Pipeline degradation** | I3 (latencia trending up), I2 (errores de inferencia), I21 (model output bounds) | **Bloquear nuevas posiciones**. Gestionar existentes. No se puede abrir sin pipeline sano |
| **Observability degradation** | I10 (stream gaps), I20 (ticks desordenados), I23 (partial failure en monitoring) | **Permitir nuevas posiciones con confianza elevada** (threshold 0.8 en vez de 0.7). Gestionar existentes normalmente |

**Regla general para ambigüedad**: si no se puede determinar el tipo de degradación, se trata como pipeline degradation (conservador).

**Implementación**: `ExecutionGate` recibe un `degradation_type` opcional del kill-switch state machine:
```python
class SystemState(Enum):
    NORMAL = "NORMAL"
    DEGRADED_PIPELINE = "DEGRADED_PIPELINE"   # bloquea nuevas posiciones
    DEGRADED_OBSERVABILITY = "DEGRADED_OBSERVABILITY"  # permite con threshold elevado
    SOFT_HALT = "SOFT_HALT"
    CRITICAL_UNPROTECTED = "CRITICAL_UNPROTECTED"
    SYSTEM_CIRCUIT_BREAKER = "SYSTEM_CIRCUIT_BREAKER"
```


---

## 3. Mapeo de invariantes → estados

### 3.1 Matriz de violaciones

| Invariante | Violación → Estado | Tiempo |
|-----------|-------------------|--------|
| I1 (pipeline loaded) | SOFT_HALT | Inmediato |
| I2 (señales sin error) | DEGRADED | Al primer error |
| I3 (latencia estable) | DEGRADED | 3 muestras trending up |
| I4 (SL en exchange) | CRITICAL_UNPROTECTED | Inmediato |
| I5 (SL fail + emergency) | CRITICAL_UNPROTECTED | Inmediato |
| I6 (orden duplicada) | SOFT_HALT | Inmediato |
| I7 (reconciliation pending) | SOFT_HALT | Inmediato |
| I8 (0 ticks inválidos) | DEGRADED | ≥3 en 60s |
| I9 (candles completas) | DEGRADED | Al detectar |
| I10 (gaps > 30s) | DEGRADED | Inmediato |
| I11 (posición local ≈ exchange) | SOFT_HALT | Inmediato |
| I12 (CRITICAL activo) | SYSTEM_CIRCUIT_BREAKER | Inmediato |
| I13 (drawdown null) | SOFT_HALT | 2 ciclos (~120s) |
| I14 (recovery completado) | SYSTEM_CIRCUIT_BREAKER | Boot |
| I15 (secrets en LIVE) | SYSTEM_CIRCUIT_BREAKER | Boot |
| I16 (env mode consistente) | SYSTEM_CIRCUIT_BREAKER | Al detectar |
| I17 (idempotencia) | SOFT_HALT | Inmediato |
| I18 (SL en exchange real) | CRITICAL_UNPROTECTED | Inmediato |
| I19 (divergencia exchange) | SOFT_HALT | Inmediato |
| I20 (time monotonicity ticks) | DEGRADED | Al detectar |
| I21 (model output bounds) | DEGRADED | Al detectar |
| I22 (risk exposure cap) | SOFT_HALT | Al exceder |
| I23 (partial failure) | DEGRADED | Al detectar |
| I24 (time monotonicity órdenes) | SOFT_HALT | Al detectar |
| I25 (no orphan SL) | DEGRADED | Al detectar |
| I26 (causal consistency) | SOFT_HALT | Al detectar |

### 3.2 Principio de diseño

Para cada invariante, el diseño debe preguntarse no *"¿cómo detectamos la violación?"*
sino *"¿cómo hacemos la violación imposible?"*

Ejemplos:
- **I4 (SL en exchange)**: No es "detectar que falta SL", es que `open_position()` no puede completarse
  sin SL confirmado. La posición no existe en DB hasta que el exchange confirma la SL order.
- **I1 (pipeline loaded)**: No es "detectar is_loaded=false", es que `ExecutionGate` llama a
  `pipeline.assert_loaded()` que lanza si no está listo.
- **I17 (idempotencia)**: No es "detectar signal duplicado", es que el store de execution_signals
  tiene `signal_id` como PK única con `ON CONFLICT DO NOTHING`.
- **I26 (causal consistency)**: No es "detectar desorden causal", es que `ExecutionGate` recibe
  un `causal_context` inmutable y no puede aprobar una señal cuya candle de referencia no esté
  cerrada y reconciliada.

---

## 4. Implementación inmediata (mapeo a Fase 1)

### Invariantes ya implementadas

| Invariante | Estado | Dónde |
|-----------|--------|-------|
| I1 (pipeline loaded) | ✅ Existe | `streaming_inference.py` — `is_loaded` flag + check en decisión loop |
| I4 (SL en exchange) | ✅ Parcial | `ccxt_order_client.py` — retry SL placement con 3 intentos |
| I5 (SL fail + emergency) | ✅ Parcial | `execute_signal.py` — catch `SlPlacementError` con emergency close |
| I6 (orden duplicada) | ✅ Parcial | `monitor_positions.py` — new-before-cancel en SL update |
| I8 (0 ticks inválidos) | ✅ Parcial | `BinanceWebSocketAdapter` — validación de ticks |
| I15 (secrets en LIVE) | ✅ Existe | `preflight.abort_if_missing()` |
| I17 (idempotencia) | ❌ No existe | execution_idempotency port existe pero no implementado en pipeline |

### Invariantes que requieren implementación

| Invariante | Prioridad | Archivos afectados |
|-----------|-----------|-------------------|
| I17 (idempotencia) | **Alta** | `execute_signal.py`, `execution_idempotency.py` |
| I18 (SL en exchange real) | **Alta** | `ccxt_order_client.py`, `composition.py` |
| I19 (divergencia exchange vs local) | **Alta** | `monitor_positions.py`, `reconciliation_engine.py` |
| I23 (partial failure tolerance) | **Media** | `main.py` — cada loop su propio try/except |
| I21 (model output sanity) | **Media** | `predict_ensemble.py` — validar confidence |
| I26 (causal consistency) | **Media** | `ExecutionGate` + `causal_context` provider |

---

## 5. ExecutionGate Layer

### 5.1 Propósito

El ExecutionGate es el **único punto del sistema donde se permite abrir/cerrar posiciones**.
Centraliza todas las invariantes en una sola decisión atómica. Sin él, las invariantes están
distribuidas y no hay garantía de que se evalúen consistentemente.

No es un "check más" — es el **control plane** que reemplaza la evaluación dispersa de invariantes.

### 5.2 Execution Ownership (TradingEngine como único entrypoint)

Para que el ExecutionGate sea realmente el gate, no basta con que exista como clase.
Debe ser **imposible** ejecutar un trade sin pasar por él. Esto se logra con una
arquitectura de un solo entrypoint:

```
Cualquier origen (señal, manual, recovery)
    │
    ▼
TradingEngine.execute(signal, causal_context)    ← ÚNICO entrypoint
    │
    ▼
ExecutionGate.evaluate(signal, causal_context)   ← Gate puro, sin side effects
    │
    ├── APPROVE ──→ ExecuteSignalUseCase(signal) ← Única ruta de ejecución
    │
    ├── REJECT ────→ log + store (audit trail)
    │
    └── HALT ──────→ kill-switch transition + log CRITICAL
```

**Reglas**:
- `TradingEngine.execute()` es el **único** lugar del sistema que puede llamar a un exchange adapter
- No existe `execute_signal()` directo, ni `monitor_positions()` que llame al exchange sin pasar por TradingEngine
- `TradingEngine` no tiene lógica de negocio — delega en `ExecutionGate` para la decisión y en `ExecuteSignalUseCase` para la acción
- `TradingEngine` es el **propietario** de `causal_context`: lo construye, lo valida y lo actualiza post-ejecución

**Implementación**: `TradingEngine` es un servicio de aplicación (application layer) que:
1. Construye el `CausalContext` actual
2. Llama a `ExecutionGate.evaluate(signal, causal_context)`
3. Si APPROVE → delega en el use case concreto
4. Post-ejecución → actualiza `causal_context` (nuevo `last_execution_timestamp`)

### 5.2 API

```python
@dataclass(frozen=True)
class GateVerdict:
    action: Literal["APPROVE", "REJECT", "HALT"]
    reason: str = ""
    blocking_invariant: Optional[str] = None  # I1, I4, I7, etc.

class ExecutionGate(Protocol):
    """Single point of execution truth. All trading decisions go through this."""
    
    def evaluate(
        self,
        signal: ExecutionSignal,
        causal_context: CausalContext,
    ) -> GateVerdict:
        ...
```

### 5.4 Evaluación (orden secuencial, short-circuit)

El ExecutionGate evalúa en este orden. Si cualquiera falla → short-circuit:

1. **Kill-switch state**: ¿el sistema está en un estado que permite trading?
   - `state in [CRITICAL_UNPROTECTED, SYSTEM_CIRCUIT_BREAKER]` → HALT
   - `state == SOFT_HALT` → REJECT (halt activo)
   - `state == DEGRADED_PIPELINE` → REJECT (nuevas posiciones bloqueadas)
   - `state == DEGRADED_OBSERVABILITY` → permitir con threshold elevado (pasa al paso 8)
2. **Pipeline loaded** (I1): `pipeline.assert_loaded()` → HALT si no
3. **Reconciliation status** (I7, I11, I19): `reconciliation.status == MATCHED` → REJECT si no
4. **CRITICAL_UNPROTECTED check** (I12): ¿hay alguna posición en este estado? → HALT
5. **Idempotency** (I17): `signal_id` ya ejecutado? → REJECT
6. **Causal consistency** (I26): ¿causal_context.monotonic == True y candle_id/candle_hash coinciden? → REJECT si no
7. **Risk exposure** (I22): ¿la nueva posición excede el cap? → REJECT
8. **Model sanity** (I21): ¿confidence en [0,1]?
   - `state == NORMAL` → confidence ≥ 0.7
   - `state == DEGRADED_OBSERVABILITY` → confidence ≥ 0.8
   - Menor → REJECT
9. **Drawdown evaluable** (I13): ¿drawdown calculable? → REJECT si no
10. ✅ **APPROVE**: todas las invariantes pasaron

### 5.5 Arquitectura

```
Signal ──→ TradingEngine.execute(signal, causal_context)    ← único entrypoint
                │
                ▼
          ExecutionGate.evaluate(signal, causal_context)     ← decisión pura
                │
                ├── APPROVE ──→ ExecuteSignalUseCase(signal) ← única ruta de ejecución
                │
                ├── REJECT ────→ log + store (audit trail)
                │
                └── HALT ──────→ kill-switch transition + log CRITICAL
```

- `evaluate()` es puro (sin side effects). No modifica estado del sistema.
- La acción de HALT (transicionar kill-switch) la ejecuta `TradingEngine`, no el gate.
- El gate informa qué invariante bloqueó (`blocking_invariant`).
- El gate no conoce adapters concretos — solo consume puertos (`KillSwitchStateReader`, `PipelineStatusReader`, `ReconciliationStatusReader`, `IdempotencyChecker`, etc.).
- `TradingEngine` es el único propietario de la ruta de ejecución. No hay `execute_signal()` directo, no hay `monitor_positions()` con acceso al exchange — toda ejecución pasa por `TradingEngine.execute()`. El `ExecuteSignalUseCase` concreto se inyecta como dependencia de `TradingEngine`, no se llama desde fuera.

### 5.6 CausalContext

```python
@dataclass(frozen=True)
class CausalContext:
    last_tick_timestamp: int
    last_candle_close_time: int
    last_reconciled_candle_id: str         # ej: "BTCUSDT:4713"
    last_reconciled_candle_hash: str       # SHA256 del OHLCV de la última candle reconciliada
    reconciliation_confirmed_timestamp: int
    last_execution_timestamp: int
    monotonic: bool  # True si tick.ts < candle.close ≤ decision.ts ≤ execution.ts
```

El `CausalContext` se construye al inicio de cada ciclo de decisión por el `TradingEngine`.
Es inmutable durante ese ciclo. Si `monotonic == False`, el `ExecutionGate` rechaza
automáticamente (I26). Post-ejecución, `TradingEngine` produce un nuevo `CausalContext`
con `last_execution_timestamp` actualizado.

### 5.7 System Authority Hierarchy

El sistema tiene una jerarquía de autoridad explícita. Cada nivel puede override al inferior:

```
Recovery Engine                  ← AUTORIDAD MÁXIMA (seguridad del sistema)
    ↑ controla boot, puede overridear ExecutionGate
    |
Kill-Switch State Machine        ← AUTORIDAD DE ESTADO (define modo operativo)
    ↑ define NORMAL/DEGRADED/SOFT_HALT/CRITICAL/CIRCUIT_BREAKER
    |
TradingEngine                    ← AUTORIDAD DE EJECUCIÓN (único entrypoint)
    ↑ construye causal_context, delega en ExecutionGate
    |
ExecutionGate                    ← AUTORIDAD DE DECISIÓN (aprueba/rechaza/bloquea)
    ↑ consume invariantes, decide APPROVE/REJECT/HALT
    |
Reconciliation Engine            ← AUTORIDAD DE VERDAD (estado ground truth)
    ↑ provee estado reconciliado contra exchange
    |
Pipeline + Monitoring            ← AUTORIDAD DE SENSORES (datos crudos)
    ↑ provee ticks, candles, señales, métricas
```

**Reglas de jerarquía**:
- **Recovery Engine** puede overridear al `ExecutionGate` solo durante startup o CRITICAL_UNPROTECTED recovery. No durante operación normal.
- **Kill-Switch State Machine** es la única fuente de `SystemState`. Nadie más escribe el estado.
- **TradingEngine** es el único que puede llamar al exchange. `ExecuteSignalUseCase`, `MonitorPositionsUseCase`, etc. se inyectan como dependencias de `TradingEngine`, no se exponen como API pública.
- **ExecutionGate** no conoce la existencia de `TradingEngine`. Es un servicio puro.
- **Reconciliation Engine** es la única fuente de verdad sobre posiciones. Nadie más escribe `PositionRepository` sin pasar por reconciliation.

---

## 6. Próximos pasos (Fase 1)

1. Revisar y aprobar `docs/stability-framework.md` (este documento)
2. Escribir `STABILITY_GATES.md` con thresholds provisionales (después de 24–72h de runtime)
3. **Fase 1 commits — rama `fix/paper-trading-stability-lock`**:
   - Commit 1: Implementar `ExecutionGate` como interfaz en `application/ports/` e implementación en `infrastructure/`
   - Commit 2: Implementar `TradingEngine` como único entrypoint de ejecución
   - Commit 3: Implementar `CausalContext` + I26 check
   - Commit 4: Extender kill-switch con `DEGRADED_PIPELINE` / `DEGRADED_OBSERVABILITY`
   - Commit 5: Implementar invariantes faltantes de la tabla en §4
   - Commit 6: Backpressure semantics en cada loop
4. Forward test mínimo 24–72h
5. Solo después → Fase 2 (behavior stabilization) y Fase 3 (H60–H65 architecture)

---

*Documento Fase 0 — 2026-06-22 (v2 — añadidos ExecutionGate, I26, backpressure semantics, CRITICAL_UNPROTECTED AND)*
*Orientación: CONSERVADOR (trading infra)*
