# STABILITY GATES — Paper Trading Readiness

> **Propósito**: Definir los thresholds numéricos provisionales que determinan si el sistema
> está listo para operar en paper trading. Estos thresholds se calibran después de 24–72h de runtime.
>
> **Fase**: 1 — Thresholds provisionales + commit plan.
> **Base**: `docs/stability-framework.md` (Fase 0 — invariantes, kill-switch, ExecutionGate, jerarquía).
> **Orientación**: CONSERVADOR (trading infra).

---

## 0. Reglas fundacionales (no negociables)

### R1 — ExecutionGate es la autoridad final de decisión

```
ExecutionGate is the final decision authority for all trading execution.
```

No es un helper, no es un validador, no es un middleware. `TradingEngine.execute()`
delega en `ExecutionGate.evaluate()` y el veredicto es vinculante. No existe ruta
alternativa de ejecución.

### R2 — Unicidad de decisión

```
A signal_id can produce exactly one execution decision in the system lifetime.
```

Cada señal tiene exactamente una decisión final, nunca re-evaluada en paralelo
por múltiples loops. Esto elimina double evaluation, race conditions entre
monitor_positions y execute_signal, y replay inconsistency.

---

## 1. Gates de Pipeline Integrity

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-PL-01 | I1 — pipeline loaded | `is_loaded == true` al inicio de cada ciclo | `pipeline.is_loaded` flag | SOFT_HALT |
| G-PL-02 | I2 — inferencia sin errores | 0 errores/NaN/inf por ciclo | Conteo por ciclo de decisión | DEGRADED_PIPELINE |
| G-PL-03 | I3 — latencia candle→decision | p99 < 5000ms (provisional) | Percentiles por ventana de 10 ciclos | DEGRADED_PIPELINE si trending up 3 muestras |

## 2. Gates de Execution Safety

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-ES-01 | I4 — SL en exchange real | SL confirmado ≤ 5s desde fill | Timestamp diff fill → SL confirmation | CRITICAL_UNPROTECTED |
| G-ES-02 | I5 — SL fail → emergency | Emergency close ≤ 2s desde SL fail | Timestamp diff SL fail → emergency ack | CRITICAL_UNPROTECTED |
| G-ES-03 | I6 — sin órdenes duplicadas | 0 duplicados por posición | Verificación pre-orden en exchange adapter | SOFT_HALT |
| G-ES-04 | I7 — reconciliation check | `status == MATCHED` antes de ejecutar | Último estado de reconciliation | SOFT_HALT |

## 3. Gates de Data Integrity

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-DI-01 | I8 — 0 ticks inválidos | 0 parse errors en ventana 60s | Contador de parse errors | DEGRADED_PIPELINE (≥3 en 60s) |
| G-DI-02 | I9 — candles completas | 0 decisiones sobre candle en formación | `candle.completed` flag antes de consumir | DEGRADED_PIPELINE |
| G-DI-03 | I10 — stream gaps | gap > 30s → alerta inmediata | `last_tick_timestamp - now` | DEGRADED_OBSERVABILITY |
| G-DI-04 | I20 — time monotonicity | 0 ticks con timestamp ≤ anterior | `tick.ts > last_tick.ts` | DEGRADED_PIPELINE |

## 4. Gates de Position State

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-PS-01 | I11 — posición local ≈ exchange | `MATCHED` requerido | Reconciliation status | SOFT_HALT |
| G-PS-02 | I12 — CRITICAL_UNPROTECTED activo | `0` posiciones en este estado | Conteo de posiciones CRITICAL | SYSTEM_CIRCUIT_BREAKER |
| G-PS-03 | I13 — drawdown calculable | drawdown no null > 1 ciclo diagnóstico | Último drawdown snapshot | SOFT_HALT (>120s sin drawdown) |
| G-PS-04 | I18 — SL en exchange real | `orderId != null AND status == open` | Verificación directa exchange API | CRITICAL_UNPROTECTED |
| G-PS-05 | I19 — divergencia exchange | 0 mismatches (side, units, price) | Reconciliation diff | SOFT_HALT |

## 5. Gates de System State

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-SS-01 | I14 — recovery completado | `RecoveryGate == SAFE` | Status del gate | SYSTEM_CIRCUIT_BREAKER (boot) |
| G-SS-02 | I15 — secrets en LIVE | `EXCHANGE_API_KEY`, `SECRET`, `BROKER_URL` no vacíos | Preflight check | SYSTEM_CIRCUIT_BREAKER (boot) |
| G-SS-03 | I16 — env mode consistente | Mismo valor en todos los componentes | Lectura al boot | SYSTEM_CIRCUIT_BREAKER |
| G-SS-04 | I21 — model output bounds | confidence ∈ [0,1], sin NaN/inf | Validación post-inferencia | DEGRADED_PIPELINE |
| G-SS-05 | I22 — risk exposure cap | **10%** del free balance (provisional) | `total_notional / free_balance` | SOFT_HALT |
| G-SS-06 | I23 — partial failure tolerance | Cada loop independiente | 0 loops caídos que afecten a otros | DEGRADED_OBSERVABILITY |

## 6. Gates de Order Integrity

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-OI-01 | I17 — idempotencia | signal_id único en store | `SELECT signal_id FROM executions` | SOFT_HALT |
| G-OI-02 | I24 — time monotonicity órdenes | `order.ts > last_order.ts` por símbolo+lado | Comparación timestamp | SOFT_HALT |
| G-OI-03 | I25 — no orphan SL | 0 órdenes SL activas sin posición | Post-close verification | DEGRADED_OBSERVABILITY |

## 7. Gates de Global Ordering

| Gate | Invariante | Threshold | Medición | Violación → |
|------|-----------|-----------|----------|-------------|
| G-GO-01 | I26 — causal consistency | `causal_context.monotonic == true` AND `decision.candle_id == last_reconciled_candle_id` AND `decision.candle_hash == last_reconciled_candle_hash` | CausalContext validation por ciclo | SOFT_HALT |

---

## 8. Mapeo de gates a kill-switch

| Kill-switch state | Gates violados | Acción del sistema |
|------------------|---------------|-------------------|
| NORMAL | 0 | Operación completa |
| DEGRADED_PIPELINE | G-PL-02, G-PL-03, G-DI-01, G-DI-02, G-DI-04, G-SS-04 | No nuevas posiciones. Pipeline degradation. Acumular señales en cola |
| DEGRADED_OBSERVABILITY | G-DI-03, G-SS-06, G-OI-03 | Operación permitida con confidence ≥ 0.8. Observability parcial |
| SOFT_HALT | G-ES-03, G-ES-04, G-PS-01, G-PS-03, G-PS-05, G-SS-05, G-OI-01, G-OI-02, G-GO-01 | Solo cierre de posiciones. No abrir nuevas. Descartar señales |
| CRITICAL_UNPROTECTED | G-ES-01, G-ES-02, G-PS-04 | Freeze total. Solo recovery. Timeout 30s |
| SYSTEM_CIRCUIT_BREAKER | G-PS-02, G-SS-01, G-SS-02, G-SS-03 | Apagado completo. Solo restart manual |

---

## 9. Fase 1 commit plan — `fix/paper-trading-stability-lock`

8 commits atómicos en orden de prioridad (execution safety → data integrity → system stability).

### Commit 1 — ExecutionGate + TradingEngine

**Propósito**: Crear el control plane. ExecutionGate como interfaz en puertos, TradingEngine como
único entrypoint. Sin este commit, el resto de las invariantes no tienen enforcement centralizado.

**Archivos**:
- `apps/analytics-engine/app/application/ports/execution_gate.py` (nuevo)
- `apps/analytics-engine/app/application/ports/trading_engine.py` (nuevo)
- `apps/analytics-engine/app/infrastructure/trading/execution_gate.py` (nuevo)
- `apps/analytics-engine/app/infrastructure/trading/trading_engine.py` (nuevo)
- `apps/analytics-engine/app/composition.py` (modificar — wiring del gate + engine)

**Commits**: `feat(core): ExecutionGate + TradingEngine control plane`

---

### Commit 2 — CRITICAL_UNPROTECTED enforcement

**Propósito**: Hacer CRITICAL_UNPROTECTED un estado transitorio con timeout + reconciliation AND.
Implementar el freeze de ejecución y el recovery process obligatorio.

**Archivos**:
- `apps/analytics-engine/app/infrastructure/trading/ccxt_order_client.py` (I4, I5, I18)
- `apps/analytics-engine/app/application/use_cases/execute_signal.py` (SlPlacementError → emergency → CRITICAL_UNPROTECTED)
- `apps/analytics-engine/app/composition.py` (kill-switch wiring)

**Commit**: `fix(execution): CRITICAL_UNPROTECTED enforcement with timeout AND reconciliation`

---

### Commit 3 — SL placement safety + emergency close

**Propósito**: Garantizar que I4 (SL en exchange real) se cumple antes de persistir posición.
Y que I5 (SL fail → emergency) tiene cobertura completa.

**Archivos**:
- `apps/analytics-engine/app/infrastructure/exchange/ccxt_order_client.py` (round_amount, get_price, cancel_order, place_stop_loss_order, close_partial)
- `apps/analytics-engine/app/infrastructure/trading/ccxt_binance_adapter.py` (Futures Demo, cancel, stop_loss)
- `apps/analytics-engine/app/application/use_cases/monitor_positions.py` (new-before-cancel SL ordering)

**Commit**: `fix(execution): SL placement safety — exchange-confirmed before persist, new-before-cancel`

---

### Commit 4 — Reconciliation enforcement

**Propósito**: Hacer que `ExecutionGate` bloquee trades si `reconciliation.status != MATCHED`.
Implementar I11, I19 en el flujo de decisión.

**Archivos**:
- `apps/analytics-engine/app/domain/recovery/reconciliation_engine.py` (estado MATCHED/MISMATCH)
- `apps/analytics-engine/app/application/ports/execution_gate.py` (reconciliation check)
- `apps/analytics-engine/app/infrastructure/trading/execution_gate.py` (implementación check)
- `apps/analytics-engine/app/composition.py` (wiring)

**Commit**: `fix(execution): reconciliation enforcement — block trades on mismatch`

---

### Commit 5 — Idempotencia de señales

**Propósito**: Implementar I17. Un signal_id no puede generar dos ejecuciones.
Usar execution_idempotency port existente pero no implementado.

**Archivos**:
- `apps/analytics-engine/app/application/ports/execution_idempotency.py` (implementar el port)
- `apps/analytics-engine/app/infrastructure/trading/execution_idempotency.py` (nuevo — SQLite + Redis impl)
- `apps/analytics-engine/app/application/use_cases/execute_signal.py` (check pre-ejecución)
- `apps/analytics-engine/app/composition.py` (wiring)

**Commit**: `feat(core): signal idempotency — I17 enforcement`

---

### Commit 6 — Tick integrity + pipeline cold start

**Propósito**: Eliminar cualquier ruta silenciosa de ticks inválidos (I8, I20).
Garantizar pipeline.is_loaded determinístico desde cold start (I1).

**Archivos**:
- `apps/data-engine/src/infrastructure/messaging/binance-websocket.adapter.ts` (parseQuantity, validación)
- `apps/analytics-engine/app/infrastructure/trading/streaming_inference.py` (is_loaded init)
- `apps/analytics-engine/app/infrastructure/monitoring/stream_integrity.py` (stream anomaly detection)
- `apps/analytics-engine/app/main.py` (cold start pipeline init)

**Commit**: `fix(data): tick integrity — zero tolerance for invalid/out-of-order ticks`

---

### Commit 7 — Observability + pipeline metrics

**Propósito**: Arreglar drawdown null persistente (I13), latencia tick→candle metrics (I3),
pipeline.is_loaded flag en observability.

**Archivos**:
- `apps/analytics-engine/app/api/observability.py` (equity/drawdown no null, pipeline.is_loaded)
- `apps/analytics-engine/app/infrastructure/monitoring/phase_b_tracker.py` (baseline fix, AT_RISK fix)
- `apps/analytics-engine/app/infrastructure/monitoring/experiment_control_plane.py` (fixes)
- `apps/analytics-engine/app/main.py` (latency instrumentation, drift_watchdog, xread fix)

**Commit**: `fix(observability): pipeline metrics, drawdown, latency instrumentation`

---

### Commit 8 — Backpressure + partial failure tolerance

**Propósito**: Implementar backpressure semantics por estado y partial failure tolerance (I23).
Cada loop independiente.

**Archivos**:
- `apps/analytics-engine/app/main.py` (cada loop con try/except propio, no propagar fallos)
- `apps/analytics-engine/app/infrastructure/monitoring/system_health.py` (health snapshot)
- Kill-switch state machine wiring

**Commit**: `feat(core): backpressure semantics per state + partial failure isolation`

---

## 10. Post-Fase 1

1. Merge `fix/paper-trading-stability-lock` a `dev` (PR)
2. Forward test mínimo 24–72h
3. Recolectar métricas reales (latencia, drawdown, trades, errores)
4. Calibrar thresholds de STABILITY_GATES.md
5. Solo entonces → Fase 2 (behavior stabilization) y Fase 3 (H60–H65 architecture)

---

*Documento Fase 1 — 2026-06-22*
*Thresholds: PROVISIONALES — calibrar después de 24–72h de runtime*
*Base: docs/stability-framework.md v3*
