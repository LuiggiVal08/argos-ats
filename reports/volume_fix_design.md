# Volume Bug Fix Design

> ⚠️ NO IMPLEMENTAR — solo diseño

## Causa Raíz

**El bug de volumen inflado fue causado por el path gRPC (`USE_GRPC_TICKS=true`)**.

El data-engine publica ticks a través de `CompositeMessageBus([grpcBus, redisBus], redisBus)`, enviando cada tick a AMBOS transportes. El analytics-engine, al usar gRPC, recibía `quantity_minor` donde el valor protobuf `int64` no se deserializaba correctamente, resultando en que el CandleBuilder acumulara un volumen ~100-200× mayor.

**El fix aplicado**: Cambiar a `USE_GRPC_TICKS=false` para usar el path Redis (que funciona correctamente).

**Evidencia**: El volumen actual coincide 1:1 con CCXT Spot (205.04 BTC/hr ambos).

---

## Archivos Afectados (solo config)

| Archivo | Cambio | Riesgo |
|---------|--------|--------|
| `apps/analytics-engine/.env.example` | Documentar `USE_GRPC_TICKS=false` | LOW |
| `docker-compose.yml` (service analytics-engine) | `USE_GRPC_TICKS=false` | LOW |
| `apps/analytics-engine/app/main.py` (default) | Considerar cambiar default a `false` | MEDIUM |

---

## Estrategia de Corrección

### Opción 1 (Recomendada): Mantener Redis Path

✅ Ya implementado. `USE_GRPC_TICKS=false`.

**Respaldo**: Funciona correctamente. Volumen idéntico a CCXT Spot.

### Opción 2: Depurar Path gRPC (futuro)

Si en el futuro se desea usar gRPC:

1. Agregar logging en `tickToProto()` para verificar `quantity_minor`
2. En analytics-engine, agregar validación: `assert volume > 0 and volume < 100` para detectar valores anómalos
3. Comparar volumen gRPC vs Redis en el `StreamComparator` existente
4. Corregir el tipo de dato protobuf: forzar int64 con valor numérico, no string

### Opción 3: Dual-transport con validación

1. Correr ambos paths simultáneamente en el CandleBuilder con un `StreamComparator`
2. Usar el volumen de Redis como fuente de verdad
3. Generar alerta si la diferencia > 5%

---

## Riesgo

| Opción | Riesgo | Justificación |
|--------|--------|---------------|
| **1** (mantener Redis) | **LOW** | ✅ Ya probado, funciona correctamente |
| **2** (depurar gRPC) | MEDIUM | Requiere cambios en data-engine y analytics-engine |
| **3** (dual transport) | MEDIUM | Complejidad adicional, overhead |

---

## Recomendación Final

**Mantener `USE_GRPC_TICKS=false`** como solución definitiva. El path Redis es más simple, igual de rápido (I/O bound de todas formas), y produce volumen correcto sin ambigüedades de tipos protobuf.

No se requiere ningún cambio de código. Solo asegurar que el deploy siempre incluya `USE_GRPC_TICKS=false` en las env vars.
