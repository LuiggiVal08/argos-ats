# Protocolo de Respuesta ante Incidentes (OWASP)

> Basado en `spec.md` §4. Define las 4 fases OWASP adaptadas al stack de
> argos-ats: **Identificación → Contención → Erradicación → Recuperación**.
>
> Cada fase mapea a componentes concretos del código y tiene SLAs
> asociados. El protocolo se activa cuando un detector automático o
> un operador humano declara un incidente.

## Índice

1. [Fase 1: Identificación](#fase-1-identificación)
2. [Fase 2: Contención](#fase-2-contención)
3. [Fase 3: Erradicación](#fase-3-erradicación)
4. [Fase 4: Recuperación](#fase-4-recuperación)
5. [Clasificación de Incidentes](#clasificación-de-incidentes)
6. [SLAs](#slas)
7. [Responsables](#responsables)

---

## Fase 1: Identificación

**Objetivo**: Detectar automáticamente condiciones anómalas y registrar
eventos estructurados para diagnóstico.

### Detectores automáticos

| Detector | Señal | Fuente | Componente |
|---|---|---|---|
| Latencia de ticks | p99 > 2ms | `HealthMonitorUseCase` (H1) | data-engine |
| Error rate de exchange | > 5% de llamadas CCXT fallidas en 1min | `ExchangeOrderClient` invocations | analytics-engine |
| Drawdown diario | ≥ 5% de pérdida | `CheckDrawdownUseCase` (H3) | analytics-engine |
| Orden huérfana | Emergency market ejecutado | `PlaceOrderUseCase` (H4-A) | analytics-engine |
| Caída de broker | Redis no reachable > 10s | `HealthMonitorUseCase` (H1) | data-engine |

### Acciones de identificación

1. El detector emite un log estructurado con nivel `CRITICAL` o `ERROR`
   y un `incident_id` único (UUID v4).
2. Si el detector corre en analytics-engine, pública un evento
   `incident:detected` al stream `incidents` del broker.
3. Si el detector corre en data-engine, escribe el evento en el stream
   `incidents` via `RedisProtocolBus`.
4. Un operador humano puede declarar un incidente manualmente vía
   `/incident-drill` o tocando el endpoint `POST /incident/declare`.

---

## Fase 2: Contención

**Objetivo**: Aislar el sistema afectado y detener la exposición
al riesgo en < 30 segundos desde la identificación.

### Acciones automáticas

| Condición | Acción | Componente responsable |
|---|---|---|
| Drawdown ≥ 5% | Circuit breaker: cancelar órdenes, cerrar posiciones, `ENVIRONMENT_MODE=PASIVO` | `TripCircuitBreakerUseCase` (H3) |
| Error rate > 5% | Pausar nuevas órdenes, mantener WS activos | `PlaceOrderUseCase` desactivado vía feature flag |
| Orden huérfana detectada | Log crítico + notificación | `PlaceOrderUseCase` (H4-A) |
| Broker caído > 10s | Cerrar WS exchange, buffer ticks | `HealthMonitorUseCase` + `InMemoryTickBuffer` (H1) |

### Acciones manuales

1. Verificar el `incident_id` en los logs (`structlog` / `winston`).
2. Confirmar que el circuit breaker se disparó:
   ```bash
   curl -X POST http://localhost:8001/risk/drawdown/check
   ```
3. Si el breaker no se disparó automáticamente, ejecutar:
   ```bash
   curl -X POST http://localhost:8001/risk/drawdown/trip
   ```
4. Revocar API keys del exchange si hay sospecha de compromiso.

---

## Fase 3: Erradicación

**Objetivo**: Eliminar la causa raíz del incidente en < 4 horas.

### Pasos

1. **Diagnóstico**: Revisar los logs estructurados del incidente
   (`incident_id` correlaciona todos los eventos).
2. **Aislamiento del adaptador**: Si el fallo está en un adapter
   concreto (ej. `CcxtOrderClient`, `BinanceWebSocketAdapter`),
   deshabilitarlo vía feature flag o config.
3. **Parche en caliente** (si aplica):
   - Si es bug de infraestructura: corregir en `infrastructure/`
   - Si es bug de dominio: corregir en `domain/`
   - Si es dependencia vulnerable: `pip-audit` / `npm audit` + update
4. **Test unitario que reproduce el bug**: Agregar test antes del fix
   (TDD inverso).
5. **Validación**: `pytest` + `tsc --noEmit` + `eslint` + `arch_lint`.
6. **Commit** con mensaje `fix(scope): incident-<id> <desc>`.

### Criterio de erradicación

- El test que reproducía el bug ahora pasa.
- El resto del test suite sigue verde.
- No hay dependencias vulnerables conocidas en el árbol.

---

## Fase 4: Recuperación

**Objetivo**: Reactivar la operativa comercial de forma segura y
gradual en < 1 hora desde la erradicación.

### Pasos

1. **Health check post-fix**:
   ```bash
   health_health_check
   ```
2. **Modo PAPER_TRADING primero**: Reactivar el ATS en paper trading
   durante 30 minutos:
   ```bash
   config_toggle_mode PAPER_TRADING
   ```
3. **Verificar drawdown**: Confirmar que el drawdown está reseteado:
   ```bash
   curl -X POST http://localhost:8001/risk/day/open
   ```
4. **Escalar a LIVE** (solo si PAPER_TRADING fue exitoso):
   ```bash
   config_toggle_mode LIVE
   ```
5. **Monitorear** los primeros 10 minutos en LIVE: latencia de ticks,
   error rate, drawdown.

### Rollback

Si la reactivación falla en PAPER_TRADING o LIVE:

1. `config_toggle_mode PASIVO`
2. Revisar logs del período de prueba
3. Volver a Fase 3 (Erradicación)

---

## Clasificación de Incidentes

| Clase | Descripción | SLA | Ejemplo |
|---|---|---|---|
| **P1** | Pérdida de fondos o fuga de secrets | 15min contención | API key comprometida |
| **P2** | Operativa detenida o drawdown > 5% | 30min contención | Circuit breaker trip |
| **P3** | Degradación del servicio | 2h diagnóstico | Latencia > 2ms p99 |
| **P4** | Cosméticos / no urgentes | 24h | Log ruidoso |

---

## SLAs

| Fase | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| Identificación | < 1min | < 1min | < 5min | — |
| Contención | < 15min | < 30min | < 2h | — |
| Erradicación | < 2h | < 4h | < 24h | < 72h |
| Recuperación | < 1h | < 2h | < 4h | < 24h |

---

## Responsables

| Rol | Responsabilidad |
|---|---|
| Operador (humano) | Declarar incidentes manuales, ejecutar runbook, decidir escalado a LIVE |
| Sistema (automático) | Detectar anomalías, ejecutar contenciones automáticas (circuit breaker, emergency orders) |
| Desarrollador | Implementar fixes, escribir tests, hacer PR |

---

*Documento mantenido en `docs/incident-response.md`. Actualizar cuando
se añadan nuevos detectores o se modifiquen SLAs.*
