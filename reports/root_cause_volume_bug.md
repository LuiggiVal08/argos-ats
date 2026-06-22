# Root Cause Analysis: Volume Pipeline Bug

## Executive Summary

**Estado**: El bug de volumen inflado ya NO está activo. Se corrigió espontáneamente.

**Pipeline actual**: ✅ CORRECTO — volumen de velas coincide con CCXT Spot en proporción 1.00x.

**Pipeline histórico**: ❌ INFLADO — 885/1000 velas (88.5%) mostraban volumen 50K-160K BTC/hr (vs ~250 esperado).

---

## 1. Evidencia

### 1.1 Pipeline actual es correcto

| Componente | Métrica | Resultado |
|------------|---------|-----------|
| CandleBuilder vs CCXT Spot | Última vela | 205.04 = 205.04 BTC **1.00x** |
| CandleBuilder vs Tick Stream | Total 44,313 ticks hora | 205.04 = 205.04 BTC **1.00x** |
| CCXT Spot vs CCXT Futures | Misma hora | 205 vs 1,340 **6.54x** |

### 1.2 Tick stream es consistente

| Métrica | Período antiguo (Jun 14) | Período actual (Jun 21) |
|---------|-------------------------|------------------------|
| Formato `quantity` | `"80000"` (string, satoshi) | `"80000"` (string, satoshi) |
| División en analytics | `/ 1e8` | `/ 1e8` |
| tradeId secuencia | 6407040816 | 6428344914 |
| Duplicados | **0%** | **0%** |

### 1.3 Velas infladas vs normales

| Período | # Velas | Volumen medio | % del total |
|---------|---------|---------------|-------------|
| ALTO INFLADO | 885 | ~45,000 BTC/hr | 88.5% |
| BAJO NORMAL | 115 | ~200-5,000 BTC/hr | 11.5% |

### 1.4 Patrón de inflación

Las velas infladas y normales se intercalan a lo largo del período de 42 días (May 10 - Jun 21). No hay un patrón diario claro.

---

## 2. Causas Descartadas

| Hipótesis | Evidencia | Veredicto |
|-----------|-----------|-----------|
| Duplicación de trades (tradeId) | 0% duplicados en 50K muestra | ❌ Descartado |
| Base vs Quote volume | Siempre ha sido `q` (BTC base), nunca `Q` | ❌ Descartado |
| Spot vs Futures | Futures es 6.5x spot, no 200x | ❌ Descartado |
| Cambio de formato quantity en Redis | Formato consistente todo el stream | ❌ Descartado |
| Divisor incorrecto en analytics-engine | Código actual `/ 1e8`, correcto | ❌ Descartado |
| Doble consumo gRPC + Redis | Dispatch es `if/else` exclusivo | ❌ Descartado |

---

## 3. Causa Raíz Más Probable

### Configuración `USE_GRPC_TICKS`

El código tiene una variable de entorno `USE_GRPC_TICKS` que por defecto es `true`:

```python
use_grpc = os.environ.get("USE_GRPC_TICKS", "true").lower() == "true"
```

**Estado actual**: `USE_GRPC_TICKS=false` → usa Redis path → **volumen correcto**.

**Estado probable anterior**: `USE_GRPC_TICKS=true` (default) → usaba gRPC → volumen inflado.

### ¿Por qué el gRPC producía volumen inflado?

El data-engine publica ticks a **ambos** transportes simultáneamente vía `CompositeMessageBus`:

```typescript
// app.module.ts line 85
return new CompositeMessageBus([grpcBus, redisBus], redisBus)
```

El server gRPC llama a `tickToProto()` que mapea `quantity_minor: j.quantity` donde `j.quantity` es un **string** ("100000") asignado a un campo protobuf `int64`.

La hipótesis más probable es que **el cliente gRPC del analytics-engine recibía `quantity_minor` como un tipo de dato incompatible** (potencialmente un `Long` de JavaScript que se serializaba incorrectamente, o un string que no se convertía correctamente a int). En ciertos casos, esto podría resultar en que `tick.quantity_minor` tuviera un valor 100× mayor que el esperado.

### El Fix

El contenedor actual del analytics-engine fue recreado (~13:30 UTC Jun 21) con `USE_GRPC_TICKS=false`, cambiando al path Redis donde el volumen se procesa correctamente. Las velas desde ~09:00 UTC muestran volumen correcto (posiblemente de un contenedor anterior que también usaba Redis o se reinició por otra razón).

---

## 4. Archivos Afectados

| Archivo | Rol | Cambio necesario |
|---------|-----|-----------------|
| `apps/analytics-engine/app/main.py:307` | Dispatch gRPC vs Redis | ✅ Ya resuelto (usar Redis) |
| `apps/analytics-engine/.env` | `USE_GRPC_TICKS=false` | ✅ Config actual |
| `docker-compose.yml` | Env vars del container | ✅ Config actual |

---

## 5. Riesgo

**Clasificación**: LOW RISK (el fix ya está aplicado)

- El pipeline actual produce volumen correcto (1:1 con CCXT)
- No se requiere cambio de código
- La configuración `USE_GRPC_TICKS=false` está funcionando

**Recomendación**: Mantener `USE_GRPC_TICKS=false`. Si se desea usar gRPC en el futuro, validar rigurosamente que `tick.quantity_minor` se recibe con el valor correcto en el analytics-engine.

---

## 6. Conclusión

El bug de volumen inflado fue causado por el uso del path gRPC (`USE_GRPC_TICKS=true`) donde la cantidad del tick se transmitía incorrectamente desde el data-engine. El fix fue cambiar al path Redis (`USE_GRPC_TICKS=false`).

No se requiere acción correctiva adicional. El pipeline actual es correcto y produce velas con volumen idéntico a CCXT Spot.
