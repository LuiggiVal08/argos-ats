# F5 — Runtime Enforcement Layer

> Contrato de ejecución: cómo las decisiones de política se convierten
> en comportamiento del motor en tiempo real.
>
> F3 produce hechos. F4 clasifica verdad. F5 ejecuta consecuencias.
>
> Esto NO es un scheduler de órdenes. Es el modelo de enforcement
> que garantiza que el motor nunca ejecute una orden que la política
> no haya autorizado.

---

## 0. Stack completo (relación con F1–F4)

```
F1 EventStore     → registro inmutable
F2 ReplayEngine   → reconstrucción determinista
F3 Verification   → integridad de proyección
F4 Policy Matrix  → acciónabilidad de hechos
F5 Enforcement    → ejecución de decisiones en runtime
```

F5 es la única capa que corre en el hot path de órdenes.
Todas las demás operan en background o en ciclos de verificación.

```
Hot path (latencia crítica):
  Signal → ExecutionGuard → OrderRouter → Exchange

Background (sin latencia crítica):
  Verification → Policy → RiskState transition
```

---

## 1. Execution Authority Model

### 1.1 Principio rector

```text
RiskPolicyEngine es la ÚNICA autoridad de decisión.
ExecutionGuard es enforcement puro — nunca decide, solo aplica.
OrderRouter es un adapter de ejecución — nunca filtra, solo envía.
```

Esto implica:

- **ExecutionGuard no puede DEGRADE ni HALT.** No muta `RiskState`.
- **ExecutionGuard no conoce F4.** No evalúa `VerificationReport`.
  Solo recibe el estado actual de riesgo y lo aplica como cache.
- **ExecutionGuard no toma decisiones de riesgo.** No evalúa
  "esto parece seguro" ni "este mismatch es menor".
- **OrderRouter no pregunta nada.** Las órdenes que recibe
  ya están autorizadas.

### 1.2 Cadena de autoridad

```
RiskPolicyEngine (política)
    │
    │ RiskStateChanged(ACTIVE | DEGRADED | HALT | RECOVERY_PENDING)
    ▼
RuntimeStateCache (capa de replicación)
    │
    │ current_risk_state (actualizado asincrónicamente)
    ▼
ExecutionGuard (enforcement)
    │
    │ OrderAuthorizationToken(risk_version) o OrderRejected
    ▼
OrderRouter (ejecución pura)
    │
    │ OrderSent / OrderFailed
    ▼
Exchange
```

### 1.3 Invariante de autoridad

```text
∀ order : order.executed
⇒ ∃ token : OrderAuthorizationToken
    ∧ token.risk_version == current_risk_version
    ∧ token.decision_hash == RiskPolicyEngine.decide(order.intent)
```

Cada orden ejecutada tiene un token de autorización vinculado
al estado de riesgo vigente al momento de la decisión.

---

## 2. Runtime State Cache

### 2.1 Qué se replica

```python
@dataclass(frozen=True)
class RuntimeRiskState:
    """Copia del estado de riesgo para consumo del hot path.

    Se actualiza cuando RiskPolicyEngine emite RiskStateChanged.
    La actualización es asíncrona pero versionada — ver §4.
    """
    status: str                         # ACTIVE | DEGRADED | HALT | RECOVERY_PENDING
    version: int                        # Monotónico, incrementa en cada transición
    updated_at: float                   # Timestamp Unix de la última transición
    reason: str                         # Razón de la transición actual
    last_valid_hash: str | None         # Último projection hash verificado
    verification_context: dict | None   # Contexto temporal (F4 §2.1)
```

### 2.2 Propagación

```
RiskPolicyEngine
    │  escribe RiskStateChanged en EventStore
    │  publica en bus interno (Redis Pub/Sub o canal en memoria)
    ▼
RuntimeStateCache
    │  recibe evento → actualiza cache local
    │  incrementa risk_version
    ▼
ExecutionGuard.read()
    │  siempre lee del cache local (nunca de EventStore en hot path)
    ▼
Orden autorizada o rechazada
```

**Regla**: en hot path, el estado de riesgo se lee del cache local,
nunca del EventStore. El EventStore es la source of truth para
replay y auditoría, pero el hot path necesita latencia < 1ms.

### 2.3 Cache staleness

```text
Maximum cache lag = 50ms (configurable)

Si el cache no se actualiza en > 50ms:
  1. ExecutionGuard DEGRADE automáticamente (local, temporal)
  2. RiskPolicyEngine recibe stale_cache alert
  3. Cuando el cache se actualiza, ExecutionGuard vuelve al estado real

Este es el ÚNICO caso donde ExecutionGuard "degrada" — y es
por seguridad, no por riesgo. Nunca DEGRADE por sospecha de
riesgo, solo por pérdida de conectividad con la autoridad.
```

---

## 3. OrderAuthorizationToken (stateless, Modelo 1)

### 3.1 Especificación

```python
@dataclass(frozen=True)
class OrderAuthorizationToken:
    """Token stateless que autoriza una orden.

    No requiere registry mutable. Es autovalidante:
      ExecutionGuard.verify(token) → bool

    Propiedades:
      - Determinista: mismo intent + mismo risk_state → mismo token.
      - No transferible: vinculado al intent específico.
      - Expirable: el token caduca si risk_version cambia.
    """
    # Identidad de la orden
    order_id: str
    intent_hash: str                    # SHA-256 del intent (símbolo, lado, cantidad)

    # Versión de riesgo bajo la que se autorizó
    risk_version: int

    # Sello de autoridad
    decision_hash: str                  # SHA-256(risk_version + intent_hash + secret)
    issued_at: float                    # Timestamp Unix

    # Expiración
    expires_at: float                   # risk_version + MAX_LATENCY (50ms por defecto)
```

### 3.2 Validación en hot path

```python
class ExecutionGuard:
    def authorize(self, intent: OrderIntent) -> OrderAuthorizationToken | OrderRejected:
        risk = self._cache.current()

        if risk.status == "HALT":
            return OrderRejected("HALT", risk.version)

        if risk.status == "DEGRADED" and intent.side in ("BUY", "SELL"):
            if intent.is_increase_position:
                return OrderRejected("DEGRADED: no new risk", risk.version)
            # reduce/close sí permitido

        token = OrderAuthorizationToken(
            order_id=uuid4().hex,
            intent_hash=intent.sha256(),
            risk_version=risk.version,
            decision_hash=self._compute_decision_hash(risk.version, intent),
            issued_at=time.time(),
            expires_at=time.time() + MAX_AUTHORIZATION_LATENCY,
        )
        return token

    def verify(self, token: OrderAuthorizationToken) -> bool:
        """Anti-TOCTOU: verifica que el token sigue siendo válido.

        Se llama inmediatamente antes de enviar la orden al OrderRouter.
        """
        risk = self._cache.current()
        return (
            # Misma versión de riesgo
            token.risk_version == risk.version
            # Token no expiró
            and time.time() < token.expires_at
            # Decisión hash coincide
            and token.decision_hash == self._compute_decision_hash(
                risk.version, OrderIntent.from_token(token),
            )
        )
```

### 3.3 Flujo de autorización

```
1. SignalEngine genera OrderIntent
2. ExecutionGuard.authorize(intent)
     ├── verifica risk cache
     ├── rechaza si HALT o DEGRADED + increase
     └── emite OrderAuthorizationToken
3. (opcional) Gateways, validadores, latencia de red...
4. Antes de OrderRouter.send():
     ExecutionGuard.verify(token)
     ├── risk_version aún vigente?
     ├── token.expires_at > now?
     ├── decision_hash íntegro?
     └── PASS → OrderRouter.send(order)
           └── FAIL → re-autorizar con nuevo token
```

### 3.4 Invariante del token

```text
El token es STATELESS. No se almacena en ningún registry mutable.
Su validez depende exclusivamente de:
  (a) risk_version actual del cache
  (b) timestamp actual
  (c) integridad del decision_hash

Esto permite:
  - Replicación horizontal sin shared state
  - Replay determinista (el token se puede reconstruir)
  - Sin estado mutable en runtime (cero side effects)
```

---

## 4. Risk Versioning Protocol (anti-TOCTOU)

### 4.1 El problema

```
Signal BUY generado          → t=0ms
Order autorizada             → t=5ms (risk_version=184)
RiskStateChanged(HALT)       → t=10ms (risk_version=185)
Order enviada a Binance      → t=12ms (risk_version=184 stale)
```

La orden escapó porque entre authorize() y send() el estado cambió.

### 4.2 La solución

```text
Toda orden lleva el risk_version bajo el que fue autorizada.

Antes de enviar:
  ExecutionGuard.verify(token)
    compara token.risk_version vs current_risk_version
    si difieren → REJECT, re-autorizar

El window de vulnerabilidad se reduce a:
  MAX_AUTHORIZATION_LATENCY (50ms por defecto)
  + tiempo de verificación (< 1ms)

Si el risk_version cambia durante la ventana, la orden se rechaza
y se re-autoriza contra el nuevo estado.
```

### 4.3 Protocolo completo

```python
def safe_send(intent: OrderIntent) -> OrderResult:
    """Protocolo anti-TOCTOU completo."""
    MAX_RETRIES = 3

    for attempt in range(MAX_RETRIES):
        # 1. Autorizar contra estado actual
        token = guard.authorize(intent)
        if isinstance(token, OrderRejected):
            return token

        # 2. Verificar antes de enviar (TOCTOU check)
        if not guard.verify(token):
            # Estado cambió entre authorize() y verify()
            # Reintentar con nuevo estado
            continue

        # 3. Enviar (ventana TOCTOU cerrada)
        result = router.send(intent.to_order(token))
        return result

    return OrderRejected("max_retries_exceeded", guard.current_version())
```

### 4.4 Property del protocolo

```text
∀ order : order.executed
⇒ order.risk_version == current_risk_version AT_SEND_TIME

Demostración:
  Sea t_a = authorize(), t_v = verify(), t_s = send()
  verify() checkea token.risk_version == cache.version AT t_v
  Si cambia entre t_v y t_s, la siguiente orden detecta el cambio
  Por construcción, el risk_version es monotónico y el cache es atómico
```

---

## 5. ExecutionGuard Policy Matrix

### 5.1 Tabla de enforcement

ExecutionGuard solo conoce `current_risk_state.status`. No conoce
F3, F4, fingerprints, trazas, ni hashes.

| RiskState | Abrir posición | Aumentar posición | Reducir posición | Cerrar posición | Modificar SL/TP |
|---|---|---|---|---|---|
| ACTIVE | ✅ | ✅ | ✅ | ✅ | ✅ |
| DEGRADED | ❌ | ❌ | ✅ | ✅ | ✅ |
| HALT | ❌ | ❌ | ❌ | ✅ | ✅ |
| RECOVERY_PENDING | ❌ | ❌ | ❌ | ✅ | ✅ |

### 5.2 Propiedad de la tabla

```text
La severidad nunca aumenta exposición.

Formalmente:
  ∀ state ≠ ACTIVE:
      allowed_exposure_change(state) ≤ 0

Es decir, en cualquier estado distinto de ACTIVE, solo se permiten
operaciones que reducen o mantienen la exposición actual.
```

### 5.3 Casos frontera

| Escenario | Decisión | Justificación |
|---|---|---|
| DEGRADED, orden de reduce que cierra parcialmente | ✅ Permitido | Reduce exposición |
| DEGRADED, roll de posición (close + reopen) | ❌ Rechazado | Close permitido, reopen es increase |
| HALT, take-profit se ejecuta automáticamente | ✅ Permitido | SL/TP son del exchange, no del motor |
| HALT, posición liquida por margen (exchange) | ✅ Permitido | Es el exchange, no el motor |
| RECOVERY_PENDING, ajuste de SL | ✅ Permitido | No cambia exposición, mejora riesgo |

### 5.4 Anti-patrón de enforcement

```text
❌ ExecutionGuard rechaza un close en HALT
   → El trader queda atrapado en una posición que no puede cerrar.
   → Cerrar posiciones SIEMPRE está permitido, incluso en HALT.

❌ ExecutionGuard permite un increase en RECOVERY_PENDING
   → Recovery no debe aumentar exposición bajo ninguna circunstancia.
   → El propósito de RECOVERY_PENDING es restaurar consistencia,
     no aprovechar oportunidades de mercado.
```

---

## 6. OrderRouter Contract

### 6.1 Contrato

```python
class OrderRouter(ABC):
    """Adaptador de ejecución puro.

    Contrato:
      - Recibe órdenes YA autorizadas.
      - Nunca pregunta "is_halted?" ni "is_degraded?".
      - Solo traduce Order → Exchange API.
      - Reporta resultado (filled, rejected, timeout).
    """

    @abstractmethod
    async def send(self, order: AuthorizedOrder) -> OrderResult:
        """Enviar orden al exchange.

        Precondición: order está autorizada por ExecutionGuard.
        Postcondición: OrderResult contiene el estado real del exchange.
        """
        ...
```

### 6.2 Invariante

```text
OrderRouter.send(order)
precondition:
    ∃ token : OrderAuthorizationToken
    ∧ ExecutionGuard.verify(token) == True

postcondition:
    result.status ∈ { FILLED, REJECTED, TIMEOUT, PARTIAL }
    ∧ result.exchange_order_id ≠ None (si FILLED o PARTIAL)
    ∧ result.error ≠ None (si REJECTED o TIMEOUT)
```

### 6.3 Anti-patrón

```text
❌ OrderRouter implementa su propia lógica de riesgo.
   → "rechazar orden si el spread es muy alto"
   → Eso es política de ejecución, no de riesgo.
   → Debería vivir en ExecutionGuard o en un filtro aparte.

❌ OrderRouter reintenta órdenes sin re-autorizar.
   → El reintento puede ejecutarse bajo un risk_state distinto.
   → Siempre verify(token) antes de cada intento de envío.
```

---

## 7. Recovery Workflow (F5.5)

### 7.1 Mapa directo desde EMC §5 / F4 §2

```
HALT
  │
  ├── recovery_timer (30 min por defecto)
  │     └── RiskPolicyEngine emite RiskStateChanged(RECOVERY_PENDING)
  │
  ├── HumanOperator emite ManualRecoveryRequested
  │     └── RiskPolicyEngine evalúa drawdown + contexto
  │           ├── drawdown seguro → RiskStateChanged(RECOVERY_PENDING)
  │           └── drawdown activo → RiskStateChanged(HALT) mantenido
  │
  ▼
RECOVERY_PENDING
  │
  ├── VerificationEngine.full_equivalence()
  │     ├── PASS → RiskStateChanged(ACTIVE)
  │     └── FAIL → RiskStateChanged(HALT) permanente
  │
  └── HumanOperator emite ManualRecoveryRequested
        └── RiskPolicyEngine valida igual que arriba
```

### 7.2 Acciones durante RECOVERY_PENDING

| Acción | Permitido | Justificación |
|---|---|---|
| Ejecutar replay | ✅ | Reconstruir estado desde event log |
| Ejecutar verificación | ✅ | Validar integridad de la proyección |
| Reconstruir snapshot | ✅ | Crear nuevo checkpoint |
| Cerrar posiciones (manual) | ✅ | Reducir riesgo durante recovery |
| Abrir posiciones | ❌ | Recovery no aumenta exposición |
| Enviar órdenes automáticas | ❌ | Solo el operador humano puede cerrar |
| Modificar SL/TP | ✅ | Mejorar gestión de riesgo existente |
| Ignorar heartbeat | ❌ | Recovery requiere conectividad activa |

### 7.3 Garantía de recovery

```text
RECOVERY_PENDING → ACTIVE
  requiere: VerificationEngine.full_equivalence() PASS

Sin verify() PASS, el sistema NO puede volver a ACTIVE.
Ni siquiera por intervención humana.
```

---

## 8. Concurrency Model

### 8.1 Event ordering en hot path

El hot path tiene 3 etapas secuenciales por orden:

```
1. authorize() → token o reject
2. verify()   → PASS o FAIL (anti-TOCTOU)
3. send()     → result
```

No hay concurrencia dentro de una orden individual.
Pero múltiples órdenes pueden estar en diferentes etapas simultáneamente:

```
Orden A: authorize → verify → send
Orden B:              authorize → verify → send
Orden C:                           authorize → verify → send
```

### 8.2 Race conditions cubiertas

| Escenario | Protección |
|---|---|
| RiskStateChanged entre authorize y verify | verify() detecta cambio de risk_version → FAIL → re-autorizar |
| RiskStateChanged entre verify y send | Próxima orden detecta cambio. La orden actual se ejecuta bajo riesgo previo (aceptable para ≤50ms window) |
| Cache stale > 50ms | ExecutionGuard DEGRADE local temporal |
| Dos RiskStateChanged consecutivos | Versión monotónica. verify() contra la más reciente |
| Orden llega después de HALT pero antes de cache update | verify() detecta risk_version desactualizado |

### 8.3 Garantía de orden global

```text
No se garantiza orden global FIFO de las órdenes.

Se garantiza:
  ∀ order : order.executed
  ⇒ token.risk_version == current_risk_version AT_VERIFY_TIME

El sistema prioriza seguridad sobre justicia de ordenamiento.
```

---

## 9. Failure Modes Taxonomy

### 9.1 Desync (cache desincronizado)

| Síntoma | Causa | Detección | Acción |
|---|---|---|---|
| ExecutionGuard cree que está ACTIVE pero RiskPolicyEngine emitió HALT | Cache lag > heartbeat | Stale cache heartbeat perdido | ExecutionGuard DEGRADE local |
| ExecutionGuard cree que está HALT pero hace horas que Recovery terminó | RiskStateChanged no llegó al cache | timeout en cache refresh | Reconnect al bus, re-sync |
| risk_version se resetea | Reinicio del RiskPolicyEngine | Version gap detectado | Replay del event log desde último checkpoint |

### 9.2 Stale risk (riesgo desactualizado)

| Síntoma | Causa | Detección | Acción |
|---|---|---|---|
| Órdenes autorizadas con risk_version anterior | TOCTOU natural (ventana < 50ms) | verify() FAIL | Re-autorizar |
| Órdenes ejecutadas con risk_version anterior pero state cambió | Ventana verify→send | Post-hoc audit | Marcar en log, notificar compliance |
| Múltiples órdenes stale simultáneas | Cache caído por > 50ms, no DEGRADE local | heartbeat perdido + N stale tokens | HALT remoto desde RiskPolicyEngine |

### 9.3 Orphan orders (órdenes sin token)

| Síntoma | Causa | Detección | Acción |
|---|---|---|---|
| Orden llega a OrderRouter sin token | Bug en ExecutionGuard | Missing token header | Rechazar en OrderRouter |
| Orden con token inválido | Token corrupto o tamper | decision_hash mismatch | Rechazar, log como security incident |
| Orden con token expirado | Risk cambió hace mucho, token sobrevive | expires_at check | Rechazar, re-autorizar |

### 9.4 Failure mode invariante

```text
∀ failure mode:
    respuesta NO puede aumentar exposición
    respuesta NO puede mutar RiskState
    respuesta DEBE loguearse con severity según tipo

Excepción → cache stale > 50ms:
    ExecutionGuard puede DEGRADE local temporal
    (única excepción, documentada en §2.3)
```

---

## 10. Property Tests de Enforcement

### 10.1 Invariante: toda orden ejecutada tiene token válido

```python
@given(order_intents=order_intent_lists(min_size=1, max_size=50))
@settings(max_examples=200)
async def test_all_executed_orders_have_valid_tokens(
    self, order_intents: list[OrderIntent],
) -> None:
    """∀ order : order.executed ⇒ ∃ token : token.risk_version == current_version."""
    cache = RuntimeRiskCache()
    guard = ExecutionGuard(cache)
    router = MockOrderRouter()

    for intent in order_intents:
        token = guard.authorize(intent)
        if isinstance(token, OrderRejected):
            continue
        assert guard.verify(token), (
            f"Token inválido para orden {intent.order_id}: "
            f"risk_version {token.risk_version} != current {cache.current().version}"
        )
        result = await router.send(intent.to_order(token))
        assert result.status != "FILLED" or token.risk_version == cache.current().version
```

### 10.2 Invariante: risk version es monotónico

```python
@given(state_transitions=risk_state_lists(min_size=1, max_size=100))
@settings(max_examples=100)
async def test_risk_version_monotonic(self, state_transitions):
    """risk_version nunca decrece."""
    versions = []
    for transition in state_transitions:
        new_version = apply_transition(transition)
        versions.append(new_version)
    for i in range(1, len(versions)):
        assert versions[i] > versions[i - 1], (
            f"Risk version decreció: {versions[i-1]} → {versions[i]}"
        )
```

### 10.3 Invariante: HALT no ejecuta increases

```python
@given(order_intents=order_intent_lists(min_size=1, max_size=30))
@settings(max_examples=100)
async def test_halt_rejects_increases(self, order_intents):
    """En HALT, ninguna orden de increase es autorizada."""
    cache = RuntimeRiskCache(status="HALT")
    guard = ExecutionGuard(cache)

    for intent in order_intents:
        token = guard.authorize(intent)
        if intent.is_increase_position:
            assert isinstance(token, OrderRejected), (
                f"Orden increase {intent.order_id} fue autorizada en HALT"
            )
```

### 10.4 Invariante: DEGRADED solo reduce exposición

```python
@given(order_intents=order_intent_lists(min_size=1, max_size=30))
@settings(max_examples=100)
async def test_degraded_only_reduces(self, order_intents):
    """En DEGRADED, solo closes y reduces son autorizados."""
    cache = RuntimeRiskCache(status="DEGRADED")
    guard = ExecutionGuard(cache)

    for intent in order_intents:
        token = guard.authorize(intent)
        if intent.side in ("BUY", "SELL") and not intent.is_close:
            if intent.is_increase_position:
                assert isinstance(token, OrderRejected), (
                    f"Increase {intent.order_id} autorizada en DEGRADED"
                )
```

### 10.5 Invariante: RECOVERY_PENDING no abre posiciones

```python
@given(order_intents=order_intent_lists(min_size=1, max_size=30))
@settings(max_examples=100)
async def test_recovery_pending_no_new_positions(self, order_intents):
    """En RECOVERY_PENDING, solo closes y SL/TP adjustments."""
    cache = RuntimeRiskCache(status="RECOVERY_PENDING")
    guard = ExecutionGuard(cache)

    for intent in order_intents:
        token = guard.authorize(intent)
        if intent.is_increase_position or intent.is_new_position:
            assert isinstance(token, OrderRejected), (
                f"Nueva posición en RECOVERY_PENDING: {intent.order_id}"
            )
```

---

## Apéndice A: Anti-patrones de F5

| Anti-patrón | Por qué no |
|---|---|
| ExecutionGuard decide DEGRADE por sospecha de riesgo | La única autoridad de riesgo es RiskPolicyEngine. ExecutionGuard solo aplica. |
| OrderRouter rechaza órdenes por spread alto | Es política de ejecución, no de enforcement. Debe estar en un filtro aparte o en la estrategia. |
| Token stateful con registry mutable | Rompe replay determinista. El token debe ser autovalidante. |
| HALT liquida posiciones automáticamente | HALT = freeze risk, no flatten portfolio. Liquidación es otro evento. |
| Recovery sin verify() PASS | RECOVERY_PENDING → ACTIVE requiere verificación de proyección. Sin verify, no hay recovery. |
| Reintentar orden sin re-autorizar | El risk_version puede haber cambiado. Siempre re-autorizar en cada intento. |
| Cache de riesgo en EventStore para hot path | Latencia inaceptable. Cache local versionado, EventStore para replay/auditoría. |

---

## Apéndice B: Resumen de cambios respecto al diseño previo

| Aspecto | Antes | Con F5 |
|---|---|---|
| ExecutionGuard conoce F4 | Conocía VerificationReport | Solo conoce risk_status |
| Autorización de órdenes | Implícita | OrderAuthorizationToken stateless |
| TOCTOU | No cubierto | Risk versioning + verify() pre-send |
| Cache de riesgo | No existía | RuntimeRiskCache versionado |
| OrderRouter | Podía filtrar | Solo envía órdenes autorizadas |
| Recovery | Solo timer | Pipeline: RECOVERY_PENDING → verify() → ACTIVE |
| HALT y liquidación | Implícita | HALT ≠ liquidación. Evento separado. |
| Property tests | No existían | 5 invariantes con hypothesis |

---

*Última actualización: 2026-06-24 — versión inicial.*
