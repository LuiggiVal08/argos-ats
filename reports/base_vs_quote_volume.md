# Base Volume vs Quote Volume

## Determinación de la Unidad de Volumen

### En el Data-Engine (TypeScript)

**File**: `apps/data-engine/src/infrastructure/messaging/in-memory-tick-buffer.ts:69`

```typescript
quantity: BigInt(Math.trunc(Number(evt.q) * 1e8)),
```

Donde `evt.q` es el campo `q` del Binance `@trade` stream:

```json
{
  "q": "0.0008",       // ← BASE quantity (BTC)
  "Q": "51.33",         // ← QUOTE quantity (USDT), NO USADO
}
```

**El data-engine usa el campo `q` (base asset quantity), NO `Q` (quote asset quantity).**

### Conversión

```
q = "0.0008" (BTC)
→ quantity = BigInt(Math.trunc(0.0008 × 1e8)) = 80000n
→ En Redis: "quantity": "80000"
→ Analytics: 80000 / 1e8 = 0.0008 BTC
```

### En el Analytics-Engine (Python)

**Redis path** (`apps/analytics-engine/app/main.py:403`):
```python
volume = float(tick.get("quantity", "0")) / 1e8
```

**gRPC path** (`apps/analytics-engine/app/main.py:491`):
```python
volume = tick.quantity_minor / 1e8
```

Ambos recuperan el valor original en BTC: `80000 / 1e8 = 0.0008`.

### Unidad Final en el CandleBuilder

El CandleBuilder acumula **base asset volume (BTC)**:
```
Por tick: 0.0008 BTC
Por hora: ~205 BTC (para una hora típica)
```

### Comparación con CCXT `fetch_ohlcv`

CCXT `fetch_ohlcv` para Spot BTC/USDT retorna `volume` en **base asset units (BTC)**. Esto es consistente:

| Fuente | Volumen en | Valor |
|--------|-----------|-------|
| CandleBuilder | BTC | 205.04 |
| CCXT Spot | BTC | 205.04 |
| Match | ✅ | 1.00x |

### Conclusión

**El volumen siempre ha sido BASE ASSET volume (BTC)** en todos los componentes. No hay confusión entre base y quote volume. El código del data-engine nunca ha usado `Q` (quoteQty) para la cantidad.
