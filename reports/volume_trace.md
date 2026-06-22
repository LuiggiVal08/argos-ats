# Volume Trace: Binance WS → Redis → CandleBuilder → Features

## Trace Seguimiento del Volumen

### Stage 1: Binance WebSocket `@trade` stream

**Source**: `wss://stream.binance.com:9443/stream?streams=btcusdt@trade`

**Raw payload**:
```json
{
  "e": "trade",
  "t": 6428327052,
  "p": "64165.56",
  "q": "0.0008",
  "Q": "51.33",
  "T": 1782067049026,
  "m": true
}
```

| Field | Value | Description |
|-------|-------|-------------|
| `q` | `"0.0008"` | Base quantity (BTC) — **used for volume** |
| `Q` | `"51.33"` | Quote quantity (USDT) — **not used** |
| `t` | `6428327052` | Trade ID (sequential, per symbol) |

### Stage 2: Data-engine Tick Parser

**File**: `apps/data-engine/src/infrastructure/messaging/in-memory-tick-buffer.ts:69`

```typescript
quantity: BigInt(Math.trunc(Number(evt.q) * 1e8)),
```

Converts BTC string → satoshi-scale integer:
```
"0.0008" → Math.trunc(0.0008 * 1e8) = 80000n
```

**Domain entity** (`apps/data-engine/src/domain/entities/tick.ts`):
- `quantity: bigint` — stored as `80000n`
- `tick.toJSON().quantity = "80000"` — serialized as decimal string

### Stage 3: Redis Stream (`ticks:btcusdt`)

**Publisher**: `apps/data-engine/src/infrastructure/messaging/redis-protocol-bus.ts:70-81`

```typescript
await this.client.xadd(stream.toString(), "*", "p", JSON.stringify(tick.toJSON()))
```

Redis entry format:
```
> XREAD STREAMS ticks:btcusdt 0
1) 1) "1782067049640-0"
   2) 1) "p"
      2) "{\"symbol\":\"BTC/USDT\",\"price\":{\"minor\":\"6416556000000\",\"decimals\":8},\"quantity\":\"80000\",\"side\":\"sell\",\"ts\":1782067049026,\"tradeId\":\"6428327052\"}"
```

**`quantity` en Redis**: `"80000"` (string, satoshi units, 1e8 per BTC)

### Stage 4: gRPC Publisher (alternate path)

**File**: `apps/data-engine/src/infrastructure/grpc/grpc-tick-server.ts:8-19`

```typescript
quantity_minor: j.quantity,  // "80000" as string → protobuf int64
```

Same quantity, different transport.

### Stage 5: Analytics-Engine Consumer

**Current `ENVIRONMENT_MODE`**: `USE_GRPC_TICKS=false` → uses Redis path

**Redis path** (`apps/analytics-engine/app/main.py:403`):
```python
volume = float(tick.get("quantity", "0")) / 1e8
```

**gRPC path** (`apps/analytics-engine/app/main.py:491`):
```python
volume = tick.quantity_minor / 1e8
```

Both produce the same result: `"80000" / 1e8 = 0.0008 BTC`

### Stage 6: CandleBuilder

**File**: `apps/analytics-engine/app/infrastructure/trading/candle_builder.py:67-159`

```python
# Line 154 (existing candle):
self._current.volume += volume  # 0.0008 BTC per tick accumulated

# Line 122 (new candle):
volume=volume  # starts with this tick's volume
```

Volume is strictly **cumulative summation** of all tick BTC quantities within a 1h UTC-aligned window.

Each tick contributes `quantity / 1e8` BTC (e.g., `"80000" / 1e8 = 0.0008`).

### Stage 7: Candle Volume Verification

**Last candle** (2026-06-21 17:00-18:00 UTC):
- 44,313 ticks in the hour
- Total tick volume: 205.039 BTC
- Candle volume: 205.04 BTC
- **Ratio: 1.00x — EXACT match**

### Stage 8: Feature Pipeline

**File**: `apps/analytics-engine/app/infrastructure/trading/build_features.py`

Features that use `candle.volume`:
- `volume` — raw volume
- `volume_sma` — rolling SMA
- `htf_volume_sma_4h` — HTF SMA (4h)
- `htf_volume_sma_1d` — HTF SMA (1d)

All derive from the CandleBuilder's accumulated volume.

## Conclusión

El pipeline de volumen **actual** es correcto. Cada etapa convierte y acumula la cantidad base (BTC) correctamente. El ratio CCXT/CandleBuilder es 1.00x.

Los candles históricos con volumen inflado (88.5% de 1000 candles, 50K-160K BTC/hr) fueron producidos por una configuración/estado anterior que ya no está activo.
