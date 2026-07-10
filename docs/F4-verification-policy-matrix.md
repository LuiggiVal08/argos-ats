# F4 — Verification Policy Matrix

> Contrato de acciónabilidad entre hechos y decisiones.
>
> F3 responde "qué es verdad". F4 responde "qué hacemos con esa verdad".
>
> Esto NO es un schema de verificación. Es el modelo de autoridad política
> sobre los resultados de la verificación.

---

## 0. Relación con F3

F3 produce un `VerificationReport` con 5 flags booleanos:

| Flag | Significado | Fuente |
|---|---|---|
| `state_match` | El estado reconstruido es byte-idéntico | F3 (replay) |
| `trace_match` | La secuencia de reducciones es idéntica | F3 (replay) |
| `structural_match` | La lógica de ejecución es la misma | F3 (fingerprint) |
| `deployment_match` | El entorno de despliegue es el mismo | F3 (fingerprint) |
| `stream_integrity` | El event store está completo | Fuente externa (§5.2) |

F4 define **qué acción corresponde** a cada combinación de flags,
**quién puede ejecutarla**, y **bajo qué condiciones temporales**.

---

## 1. Verification Policy Matrix (5-dimensional)

### 1.1 Actionability table

Cada fila es una combinación de flags. El orden es por severidad
descendente — la primera fila que matchea determina la acción.

```
A = state_match
B = trace_match
C = structural_match
D = deployment_match
E = stream_integrity

Símbolos:
  ✅ = true
  ❌ = false
  * = cualquier valor (don't care)
```

| A | B | C | D | E | Acción | Consumidor | Justificación |
|---|---|---|---|---|---|---|---|
| ❌ | * | * | * | * | **HALT** inmediato | ExecutionGuard | Estado inconsistente. No hay operación segura posible. Dual invariante §3.5. |
| ✅ | * | ❌ | * | * | **HALT** inmediato | ExecutionGuard | Sistema de clasificación perdió poder explicativo. Severidad CRITICAL. |
| ✅ | ❌ | ❌ | * | * | **HALT** inmediato | ExecutionGuard | Structural fingerprint no captura toda la semántica. Bug de clasificación. |
| ✅ | ✅ | ❌ | * | * | **HALT** inmediato | ExecutionGuard | Invariante §3 violado. Sistema de verificación no confiable. |
| ✅ | ❌ | ✅ | * | * | **DEGRADED** (acumulativo) | RiskPolicyEngine | Pérdida de determinismo histórico. Stream y deploy irrelevantes para acción. |
| ✅ | ✅ | ✅ | * | ❌ | **DEGRADED** (no-HALT) | RiskPolicyEngine | Stream incompleto. Estado y lógica correctos, pero event log con gaps. Operativamente seguro, no auditable. |
| ✅ | ✅ | ✅ | ❌ | ✅ | **LOG** (benigno) | Auditoría | Despliegue rutinario. Stream íntegro. |
| ✅ | ✅ | ✅ | ✅ | ❌ | **LOG** (benigno) | Auditoría | Stream incompleto. Sistema funciona; auditoría comprometida. Notificar compliance. |
| ✅ | ✅ | ✅ | ✅ | ✅ | **NO-OP** | — | Todo coincide. Proyección íntegra. Actualizar `last_valid_hash`. |

### 1.2 Principios de la matriz

1. **State_match=false siempre es HALT** — no hay excusa para operar sobre estado inconsistente.
2. **Structural_match=false siempre es HALT** — o bien hay un bug de clasificación (ver §3) o el sistema ejecutó lógica diferente sin que el estado lo refleje. Ambos son críticos.
3. **Trace_match=false NUNCA es HALT directo** — solo DEGRADED acumulativo. Un solo trace mismatch puede ser ruido; N en una ventana es sistémico.
4. **Deployment_match=false NUNCA cambia la acción por sí mismo** — solo modifica el contexto de auditoría.
5. **Stream_integrity=false sin state/trace/structural mismatches produce DEGRADED, no HALT** — el sistema opera correctamente pero no puede auditarse. Notificar a compliance.

---

## 2. Temporalidad de las violaciones

### 2.1 VerificationContext

No todas las violaciones actúan instantáneamente. Las decisiones
acumulativas necesitan un contexto compartido con el RiskPolicyEngine.

```python
@dataclass
class VerificationContext:
    """Estado temporal del VerificationEngine para decisiones acumulativas.

    Se almacena en el RiskPolicyEngine junto con el risk state.
    No es persistente entre reinicios del sistema (se reconstruye
    desde el último checkpoint válido).
    """

    # Ventanas deslizantes (timestamps de violaciones)
    trace_mismatch_timestamps: list[float] = field(default_factory=list)
    structural_mismatch_timestamps: list[float] = field(default_factory=list)

    # Estado del último ciclo de verificación
    last_verification_time: float = 0.0
    last_valid_hash: str = ""
    last_full_report: VerificationReport | None = None

    # Control de cooldown (hereda de §11.4)
    cooldown_until: float = 0.0
    consecutive_degraded_cycles: int = 0
```

### 2.2 Ventanas y umbrales

| Violación | Ventana | Umbral | Acción al exceder |
|---|---|---|---|
| `trace_match=false` | 300s (deslizante) | 3 ocurrencias | DEGRADED → si ya está DEGRADED → HALT |
| `structural_match=false` | Sin ventana (acción inmediata) | 1 ocurrencia | HALT (ver §1.1, esto es CRITICAL) |
| `state_match=false` | Sin ventana (acción inmediata) | 1 ocurrencia | HALT inmediato |
| `deployment_match=false` | Sin ventana | N/A | LOG only, sin acumulación |

### 2.3 Reglas de ventana

```text
trace_mismatch_window: list[timestamp]
  → se podan timestamps > 300s del momento actual
  → si len(window) >= 3:
      * si sistema está ACTIVE → DEGRADED
      * si sistema ya está DEGRADED → HALT
  → el contador se resetea al pasar a ACTIVE

structural_mismatch NO tiene ventana porque:
  structural_match=false es siempre un bug de clasificación (§3)
  o una violación del invariante de poder explicativo
  → HALT inmediato, sin acumulación
```

### 2.4 Herencia del cooldown de §11.4

Las reglas de anti-oscilación de EMC §11.4 se aplican directamente:

- `cooldown_window = 300s` entre transiciones ACTIVE ↔ DEGRADED
- `min_degraded_time = 60s` antes de permitir recuperación
- `max_breaches_in_window = 3` → HALT
- El contador de trace mismatches comparte la misma ventana que las
  breaches de proyección (§11.4 Rule C)

---

## 3. Invariante de poder explicativo

### 3.1 Formulación

```text
state_match = true
∧ trace_match = true
⇒
structural_match = true
```

O equivalentemente (contrapositiva):

```text
structural_match = false
⇒
(state_match = false ∨ trace_match = false)
```

### 3.2 Justificación

El fingerprint estructural captura:

- routing (LaneScheduler)
- reducer dispatch
- sort key spec
- schema semantics

Si estos no cambian, la misma secuencia de eventos debe producir
exactamente la misma traza y el mismo estado. Por construcción.

Si structural_match=false pero state_match=true y trace_match=true,
entonces el fingerprint estructural **no cubre toda la lógica**
que afecta la ejecución. Hay un componente ejecutándose que no está
versionado.

### 3.3 Consecuencias de la violación

```text
Evento: VerificationInvariantBroken
  category = EXPLANATORY_POWER_LOSS
  severity = CRITICAL
  detail = "structural fingerprint does not cover all execution components"

Acción:
  1. HALT inmediato (la matriz F4 no es aplicable)
  2. El sistema de verificación perdió capacidad explicativa
  3. Se requiere intervención manual para:
     a. Identificar el componente no versionado
     b. Agregarlo al StructuralFingerprint
     c. Validar que el invariante se restablece
```

### 3.4 Dual invariante: state_match=false ⇒ no operación segura

```text
state_match = false
⇒
is_operationally_safe = false
```

Sin excepciones. No existe "drift tolerable" ni "epsilon equivalence"
en un sistema financiero donde el estado es la verdad fundamental.

Consecuencias:
- Si `state_match=false`, HALT es la única acción posible.
- No hay DEGRADED para state mismatches.
- No hay ventana de acumulación para state mismatches.
- Cualquier intento de introducir "aceptar pequeñas diferencias"
  en el RiskPolicyEngine debe ser rechazado. Rompe la verificabilidad.

Razón: en trading algorítmico, el estado incluye posiciones, PnL,
y balances. Una diferencia de 1 unidad en quantity o 0.01 en precio
puede significar que el sistema está operando sobre una realidad
que no existe. No hay "casi correcto" en posiciones.

### 3.5 Property-based testing del invariante

```python
@given(events=event_lists(min_size=1, max_size=20))
@settings(max_examples=200)
async def test_explanatory_power_invariant(
    self, events: list[DomainEvent],
) -> None:
    """state_match ∧ trace_match ⇒ structural_match."""
    db = _fresh_db_path("epi")
    sorted_events, trace, state, sfp, dfp = await _replay_get_trace(events, db)

    # Replay idéntico → el invariante debe cumplirse
    _, trace2, state2, sfp2, dfp2 = await _replay_get_trace(events, _fresh_db_path("epi2"))

    state_match = state == state2
    trace_match = trace == trace2
    structural_match = sfp == sfp2

    # Si el fingerprint NO cambió entre replays, esto SIEMPRE se cumple
    # Si CAMBIÓ intencionalmente (ej. deploy), el test se salta
    if structural_match:
        assert not (state_match and not trace_match), (
            "Same structural fingerprint but different state/trace "
            "→ reducer no determinístico o I/O en reducer"
        )
```

---

## 4. Direccionalidad de la autoridad

### 4.1 Matriz de autoridad

| Fuente | LOG | DEGRADE | HALT | Mutar estado de riesgo | Mutar estado materializado |
|---|---|---|---|---|---|---|
| VerificationEngine | ✅ | ❌ | ❌ | ❌ | ❌ |
| RiskPolicyEngine | ✅ | ✅ | ✅ | ✅ (solo RiskState) | ❌ |
| ExecutionGuard | ✅ | ✅ | ❌ | ❌ | ❌ |
| HumanOperator | ✅ | ✅ | ✅ | ❌ (solo vía eventos admin) | ❌ |
| DeploymentValidator | ✅ | ❌ | ❌ | ❌ | ❌ |

### 4.2 Reglas de autoridad

1. **VerificationEngine emite hechos, nunca decisiones.**
   - Puede LOG cualquier hallazgo.
   - No puede DEGRADE ni HALT.
   - Emite `ProjectionIntegrityBreached` como signal event.
   - RiskPolicyEngine decide qué hacer con ese signal.

2. **RiskPolicyEngine es la única entidad que puede mutar RiskState.**
   - Puede transicionar ACTIVE → DEGRADED → HALT → ACTIVE.
   - Solo responde a signals verificados (no a rumores).
   - No puede ser overrideado por VerificationEngine ni ExecutionGuard.

3. **ExecutionGuard puede DEGRADE pero no HALT.**
   - Puede rechazar órdenes individuales si está DEGRADED.
   - No puede detener el motor completo.
   - El HALT es responsabilidad exclusiva del RiskPolicyEngine.

4. **HumanOperator solo emite eventos administrativos.**
   - Puede LOG, DEGRADE y HALT exclusivamente emitiendo eventos:
     * `ManualSystemHaltRequested`
     * `ManualRecoveryRequested`
     * `ManualTradingPauseRequested`
   - **NO puede mutar estado materializado directamente.**
     Prohibiciones explícitas:
     * ❌ Mutar `PositionState` (positions, PnL, balances)
     * ❌ Mutar `EventStore` (insertar, borrar, modificar eventos)
     * ❌ Reescribir snapshots
     * ❌ Modificar `RiskState` directamente (solo vía RiskPolicyEngine)
   - RiskPolicyEngine sigue siendo quien decide si los eventos
     administrativos se convierten en cambios efectivos de estado.
     Ejemplo: si HumanOperator emite `ManualRecoveryRequested` pero
     el drawdown sigue activo, RiskPolicyEngine lo rechaza y
     re-emite HALT.
   - Cada intervención humana queda en el event log como evento firmado.
   - El sistema puede revertir una intervención humana si las
     condiciones de riesgo lo requieren.

5. **DeploymentValidator solo observa.**
   - Puede verificar que structural_fingerprint == expected.
   - Puede alertar si diffiere (LOG).
   - No puede DEGRADE ni HALT.

### 4.3 Anti-patrón de autoridad

```text
❌ VerificationEngine llama directamente a stop_execution()
   → Rompe la separación epistemología/política.
   → RiskPolicyEngine no tiene oportunidad de evaluar contexto.

❌ ExecutionGuard ignora un DEGRADED de RiskPolicyEngine
   → Rompe la cadena de autoridad.
   → El sistema ejecuta órdenes en estado no verificado.

❌ RiskPolicyEngine DEGRADE basado en un signal no verificado
   → Permite ataques de spoofing al bus de eventos.
   → Todo signal debe pasar por VerificationEngine primero.
```

---

## 5. Dimensión de auditabilidad

### 5.1 `is_auditable`

Cuarta dimensión del modelo de verdad, ortogonal a las tres existentes.

Requiere tanto consistencia interna (F3) como completitud del event log (fuente externa).

```python
@property
def is_auditable(self) -> bool:
    """True si el replay es reproducible exactamente para fines forenses.

    Un sistema puede ser seguro para operar (is_operationally_safe)
    pero no auditable (trace corruption, structural drift, stream gaps).

    Regla:
      is_auditable = state_match
                     AND trace_match
                     AND structural_match
                     AND stream_integrity
      (deployment_match es irrelevante para auditabilidad)
    """
    return (
        self.state_match
        and self.trace_match
        and self.structural_match
        and self.stream_integrity
    )
```

### 5.2 `stream_integrity`

El flag `stream_integrity` NO se computa desde el replay. Es una
señal externa que responde a:

> "¿El event store contiene todos los eventos que debía contener?"

Se deriva de múltiples fuentes de evidencia:

| Fuente | Qué detecta | Frecuencia |
|---|---|---|
| Heartbeat ticks del exchange | Gap > 5s sin tick = posible pérdida de stream | Cada tick |
| `event_index` discontinuo (SQLite) | Salto en autoincrement = INSERT fallido o DELETE | Cada write |
| Exchange sequence IDs (order book) | Gap en seqnum del exchange = missing book event | Cada book update |
| Contador esperado vs recibido por ventana | 1000 ticks/min esperados, 800 recibidos = stream loss | Cada minuto |
| Kafka offset lag (si aplica) | Consumer atrasado = events no procesados aún | Cada poll |
| Redis stream contador | XADD count vs XREAD count mismatch = stream truncado | Cada verificación |

```python
@dataclass(frozen=True)
class StreamIntegrityReport:
    """Reporte de integridad del event stream para auditabilidad."""
    all_ticks_received: bool          # Sin gaps de heartbeat
    event_sequence_contiguous: bool    # Sin saltos en event_index
    exchange_seq_gap_free: bool       # Sin gaps en seqnum del exchange
    expected_vs_actual_ratio: float   # 1.0 = perfecto, <0.95 = warning

    @property
    def is_integrity_ok(self) -> bool:
        """El stream está completo para fines de auditoría.

        Un ratio >0.95 con todos los checks estructurales OK es
        aceptable para auditoría. Por debajo de 0.95 se requiere
        notificación a compliance.
        """
        return (
            self.all_ticks_received
            and self.event_sequence_contiguous
            and self.exchange_seq_gap_free
            and self.expected_vs_actual_ratio >= 0.95
        )
```

### 5.3 Implicaciones de `is_auditable=false`

| Escenario | Seguro operar? | Auditable? | Consecuencia |
|---|---|---|---|
| `trace_match=false` | ✅ (DEGRADED) | ❌ | Post-mortems no confiables. Cumplimiento comprometido. |
| `structural_match=false` | ❌ (HALT) | ❌ | Sistema detenido. Forense imposible hasta restaurar invariante §3. |
| `state_match=false` | ❌ (HALT) | ❌ | Verdad fundamental perdida. Requiere reconstrucción desde origen. |
| `deployment_match=false` | ✅ (LOG) | ✅ | Auditoría intacta. Diferente build no afecta reproducibilidad. |

### 5.3 Uso en cumplimiento

```text
En trading institucional, el regulador pregunta:

  "¿Puede demostrar que su sistema procesó exactamente
   estos eventos y produjo exactamente este resultado?"

is_auditable responde SÍ (state_match ∧ trace_match ∧ structural_match ∧ stream_integrity)
  → Se puede re-ejecutar el event stream y obtener estado idéntico.
  → El event store está completo (sin gaps de heartbeat, seqnum, event_index).

is_auditable responde NO
  → No se puede garantizar reproducibilidad forense.
  → Causas posibles:
      * trace_match=false → pérdida de determinismo histórico
      * structural_match=false → bug de clasificación (§3)
      * stream_integrity=false → event log incompleto
  → Se debe notificar a compliance dentro de 24h, indicando la causa.
```

---

## 6. Modelo de verdad de cinco dimensiones

### 6.1 Las cinco propiedades

| Propiedad | Fórmula | Pregunta | Consumidor |
|---|---|---|---|
| `is_correct` | `state_match ∧ structural_match` | ¿El estado es válido bajo la lógica esperada? | RiskPolicyEngine |
| `is_operationally_safe` | `is_correct` | ¿Es seguro ejecutar órdenes? | ExecutionGuard |
| `is_equivalent` | `state_match ∧ trace_match ∧ structural_match ∧ deployment_match` | ¿Es exactamente el mismo replay? | Auditoría, CI/CD |
| `is_auditable` | `state_match ∧ trace_match ∧ structural_match ∧ stream_integrity` | ¿Es reproducible forensemente? | Compliance, Post-mortem |

### 6.2 Relaciones entre dimensiones

```
is_equivalent (más fuerte)
    ↓
is_auditable (sin deployment, requiere stream_integrity)
    ↓
is_correct (sin trace, sin deployment, sin stream)
    ↓
is_operationally_safe (alias de is_correct)

is_auditable ⇒ is_correct  (trace + stream son necesarios para auditabilidad)
is_equivalent ⇒ is_auditable  (deployment es extra, stream ya está en auditable)
```

### 6.3 Tabla de decisión unificada

| A | B | C | D | E | correct | safe | auditable | equivalent | Acción |
|---|---|---|---|---|---|---|---|---|---|
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | NO-OP |
| ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | DEGRADED (stream) |
| ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | LOG (deploy) |
| ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | LOG (deploy + stream) |
| ✅ | ❌ | ✅ | * | * | ✅ | ✅ | ❌ | ❌ | DEGRADED (trace) |
| * | * | ❌ | * | * | ❌ | ❌ | ❌ | ❌ | HALT (§3) |
| ❌ | * | * | * | * | ❌ | ❌ | ❌ | ❌ | HALT (§3.5) |
| ✅ | ✅ | ❌ | * | * | ❌ | ❌ | ❌ | ❌ | HALT (§3 violado) |

---

## 7. Integración con RiskPolicyEngine

### 7.1 Ciclo de verificación completo

```
Timer (cada N eventos o 15 min)
  │
  ▼
VerificationEngine.verify(store, last_valid_hash)
  │
  ├── hash PASS
  │     └→ actualizar last_valid_hash
  │        reset trace_mismatch_window si estaba acumulando
  │
  └── hash FAIL
        └→ emitir ProjectionIntegrityBreached(report)
               │
               ▼
          RiskPolicyEngine.on_integrity_breach(signal)
               │
               ├── 1. Clasificar por matriz F4 (§1)
               │     ├── HALT → transition(HALT, reason)
               │     ├── DEGRADED → check ventana temporal (§2)
               │     │     ├── under threshold → LOG + acumular
               │     │     └── over threshold → transition(DEGRADED | HALT)
               │     └── LOG → solo log, no state transition
               │
               ├── 2. Verificar invariante §3
               │     └── violado → emitir VerificationInvariantBroken
               │                   transition(HALT, "explanatory_power_loss")
               │
               └── 3. Actualizar VerificationContext
                     ├── timestamps de mismatches
                     ├── last_verification_time
                     └── cooldown state
```

### 7.2 Signals emitidos por el ciclo

| Signal | Emisor | Condición |
|---|---|---|
| `ProjectionIntegrityBreached` | VerificationEngine | verify() FAIL |
| `RiskStateChanged` | RiskPolicyEngine | Transición de estado de riesgo |
| `VerificationInvariantBroken` | RiskPolicyEngine | Violación del invariante §3 |

---

## 8. Anti-patterns

| Anti-patrón | Por qué no |
|---|---|
| VerificationEngine decide DEGRADED sin pasar por RiskPolicyEngine | Rompe §4. VerificationEngine emite hechos, no decisiones. |
| trace_match=false tratado como HALT directo | Un solo trace mismatch puede ser ruido. Siempre ventana acumulativa. |
| deployment_match=false tratado como DEGRADED | Causa oscilaciones en cada deploy. Same structural fingerprint = same logic. |
| Ignorar el invariante §3 porque "nunca pasa" | Si ocurre, el sistema de verificación mintió. Toda decisión posterior es inválida. |
| HumanOperator revive el sistema sin verificar drawdown | La intervención humana queda event-sourced, pero RiskPolicyEngine re-evalúa y puede revertir. Además, HumanOperator no puede mutar RiskState directamente — solo emite eventos de solicitud. |
| Usar `is_equivalent` para decisiones operacionales | `is_equivalent` incluye deployment_match. Falla en deploys rutinarios. Usar `is_operationally_safe`. |
| Acumular trace mismatches sin límite de ventana | Memory leak + decisión basada en historia irrelevante. Siempre ventana deslizante de 300s. |
| RiskPolicyEngine acepta signals no verificados | Ataque de spoofing. Todo signal debe pasar por VerificationEngine. |

---

## 9. Resumen de cambios respecto al estado actual

| Aspecto | Antes de F4 | Con F4 |
|---|---|---|
| Decisión sobre trace mismatch | Implícita en RiskPolicyEngine | Explícita: DEGRADED acumulativo con ventana de 300s |
| Decisión sobre deployment mismatch | "Fingerprint-only exclusion" (EMC §11.4) | Formalizada: LOG only, nunca cambia acción |
| Invariante de clasificación | No existía | `state_match ∧ trace_match ⇒ structural_match` |
| Auditorabilidad | No existía como dimensión | `is_auditable` = state ∧ trace ∧ structural |
| Matriz de autoridad | Implícita en §11 | Explícita: 5 fuentes, 4 acciones |
| Ventanas temporales | Solo cooldown (§11.4) | Ventana deslizante para trace mismatches |
| VerificationContext | No existía | Contexto compartido con RiskPolicyEngine |

---

## 10. Lo que NO cubre F4 (y está bien)

- **No define la política de drawdown.** Eso es del RiskPolicyEngine existente (§5 de EMC).
- **No redefine el cooldown.** Hereda las reglas de §11.4.
- **No reemplaza F3.** F3 produce los hechos; F4 consume esos hechos.
- **No define la implementación del timer de verificación.** Eso es infraestructura operacional.
- **No especifica el schema de `VerificationInvariantBroken`.** Se modela como un DomainEvent más en el catálogo de §2 de EMC.

---

*Última actualización: 2026-06-24 — versión inicial.*
