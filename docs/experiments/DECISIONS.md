# Forward Test Experiment 001 — Decision Changelog

> Registro cronológico de todas las decisiones importantes tomadas durante el experimento FT-001.
> Permite reconstruir la evolución del sistema y entender el contexto de cada cambio.

---

## Formato

| Campo | Descripción |
|---|---|
| **Fecha** | Fecha de la decisión |
| **Problema observado** | Qué se observó o qué problema se identificó |
| **Hipótesis** | Teoría sobre la causa raíz |
| **Acción tomada** | Qué se hizo para resolverlo |
| **Resultado esperado** | Qué se esperaba que pasara |
| **Resultado observado** | Qué pasó realmente |
| **Impacto** | Alto / Medio / Bajo |
| **Archivos afectados** | Archivos modificados (si aplica) |

---

## Registro de Decisiones

### 2026-07-10 — Inicio del Experimento

| Campo | Valor |
|---|---|
| **Problema observado** | Sistema alcanzó estado estable, listo para forward testing |
| **Hipótesis** | El sistema puede operar en PAPER_TRADING de forma continua |
| **Acción tomada** | Crear experimento FT-001 con baseline v0.9.1-forward-test |
| **Resultado esperado** | Sistema corriendo 24/7 generando señales y métricas |
| **Resultado observado** | Pendiente |
| **Impacto** | Alto |
| **Archivos afectados** | `docs/experiments/FORWARD_TEST_EXPERIMENT_001.md`, `docs/experiments/METRICS.md`, `docs/experiments/DECISIONS.md`, `scripts/generate_daily_snapshot.py` |

---

### 2026-07-10 — Fix: Decimal Scoping Error

| Campo | Valor |
|---|---|
| **Problema observado** | Error `cannot access local variable 'Decimal'` cada ~5 segundos en position monitor loop |
| **Hipótesis** | Importación local redundante `from decimal import Decimal` dentro de `try`/`for` creaba un binding que shadowaba el import del módulo |
| **Acción tomada** | Eliminar la importación local en `main.py:960` (el import del módulo en línea 10 ya está disponible) |
| **Resultado esperado** | Error desaparece, logs limpios |
| **Resultado observado** | Error eliminado, servicios corriendo sin errores |
| **Impacto** | Medio |
| **Archivos afectados** | `apps/analytics-engine/app/main.py` |

---

### 2026-07-10 — Regime-Aware Execution Guard

| Campo | Valor |
|---|---|
| **Problema observado** | Threshold de confianza fijo (0.75) no se adaptaba a diferentes regímenes de mercado |
| **Hipótesis** | Un threshold más bajo en TRENDING (0.55) y más alto en RANGING (0.62) mejoraría la selección de señales |
| **Acción tomada** | Implementar ExecutionGuard con thresholds regime-aware, configurables via env vars |
| **Resultado esperado** | Mejor filtrado de señales según régimen |
| **Resultado observado** | Implementado, pendiente de validación en forward test |
| **Impacto** | Alto |
| **Archivos afectados** | `apps/analytics-engine/app/infrastructure/trading/execution_guard.py`, `apps/analytics-engine/app/composition.py` |

---

## Decisiones Futuras (No Implementadas)

> Las siguientes decisiones están documentadas pero NO se implementarán
> durante el experimento FT-001. Se proponen como trabajo futuro.

###_FT-002: Unified Label Encoding Module

| Campo | Valor |
|---|---|
| **Problema observado** | Existe un módulo unificado de encoding (`CLASS_ENCODING`) propuesto pero no implementado |
| **Hipótesis** | Un módulo centralizado evitaría inconsistencias de encoding |
| **Acción propuesta** | Crear `apps/analytics-engine/app/domain/value_objects/class_encoding.py` |
| **Resultado esperado** | Encoding consistente en todo el sistema |
| **Prioridad** | Media |
| **Requiere nuevo experimento** | Sí |

###_FT-003: Ensemble Mapping Fix

| Campo | Valor |
|---|---|
| **Problema observado** | `PredictEnsembleSignalUseCase._ensemble_decision_raw()` tiene mapping invertido |
| **Hipótesis** | El mapping de probabilidades del meta-modelo está al revés |
| **Acción propuesta** | Corregir el mapping en `_ensemble_decision_raw` |
| **Resultado esperado** | `POST /model/predict` retorna señales correctas |
| **Prioridad** | Media |
| **Requiere nuevo experimento** | No (afecta solo API, no streaming loop) |

---

## Plantilla para Nuevas Decisiones

```markdown
### YYYY-MM-DD — Título de la Decisión

| Campo | Valor |
|---|---|
| **Problema observado** | |
| **Hipótesis** | |
| **Acción tomada** | |
| **Resultado esperado** | |
| **Resultado observado** | |
| **Impacto** | Alto / Medio / Bajo |
| **Archivos afectados** | |
```

---

**Última actualización**: 2026-07-10
