# Raw Trade Samples: Binance WS → Redis

## Muestra de Ticks Raw desde Redis (`ticks:btcusdt`)

### 5 Ticks recientes

```
tradeId=6428331794
  quantity (raw) = "1500000"  (type=str, nombre largo)
  qty / 1e8 = 0.015 BTC
  price = 64216.00
  notional = 963.24 USDT

tradeId=6428331793
  quantity (raw) = "700000"
  qty / 1e8 = 0.007 BTC
  notional = 449.51 USDT

tradeId=6428331792
  quantity (raw) = "906000"
  qty / 1e8 = 0.00906 BTC
  notional = 581.80 USDT

tradeId=6428331791
  quantity (raw) = "8000"
  qty / 1e8 = 0.00008 BTC
  notional = 5.14 USDT

tradeId=6428331790
  quantity (raw) = "8000"
  qty / 1e8 = 0.00008 BTC
  notional = 5.14 USDT
```

### Formato de Campos del Tick en Redis

| Campo | Valor | Significado |
|-------|-------|-------------|
| `quantity` | `"1500000"` | Cantidad en unidades minor (satoshi-like). Dividir por 1e8 para obtener BTC |
| `price.minor` | `"6421600000000"` | Precio en minor units. Dividir por 10^8 para obtener USD |
| `price.decimals` | `8` | Decimales del precio |
| `ts` | `1782067049640` | Timestamp en ms |
| `tradeId` | `"6428331794"` | ID único del trade de Binance |
| `side` | `"sell"` | `sell` = taker sell (maker buy), `buy` = taker buy (maker sell) |

### Determinación de Campos

| Campo Binance | Se usa? | Cómo se usa |
|---------------|---------|-------------|
| `q` | ✅ Sí | `BigInt(Math.trunc(Number(q) * 1e8))` → `quantity` en Redis |
| `Q` (quoteQty) | ❌ No | No se almacena ni procesa |
| `p` | ✅ Sí | `Price.parse(p, 8)` → `price.minor` / `price.decimals` |
| `m` | ✅ Sí | Determina `side`: `true` → sell, `false` → buy |

### Conclusión

- **`quantity` en Redis SIEMPRE está en satoshi units** (multiplicado por 1e8)
- **Representa BASE ASSET volume (BTC)**, no USDT notional
- **El formato es consistente** desde el primer tick (Jun 14 tradeId=6407040816) hasta el último
- **No hay cambio de formato** en toda la historia del stream
