# Volume Ratio Statistics: CandleBuilder vs CCXT OHLCV

## Ratio: CandleBuilder / CCXT Spot

### Metodología
- Se comparó la última vela completada del CandleBuilder con CCXT `fetch_ohlcv('BTC/USDT', '1h')`
- Misma hora exacta (2026-06-21 17:00-18:00 UTC)

### Resultado

| Fuente | Open | High | Low | Close | Volume |
|--------|------|------|-----|-------|--------|
| CCXT Spot | 64145.3 | 64221.4 | 64080.0 | 64151.5 | **205.04** |
| CandleBuilder | 64145.3 | 64221.4 | 64080.0 | 64151.5 | **205.04** |
| Ratio | 1.0x | 1.0x | 1.0x | 1.0x | **1.00x** |

**El CandleBuilder produce volumen IDÉNTICO a CCXT Spot.**

### Ratio: Candle / Tick Stream

Para la misma vela:
- 44,313 ticks procesados por el CandleBuilder
- Volumen total de ticks: 205.039 BTC
- Volumen de la vela: 205.04 BTC
- **Ratio: 1.00x** — cada tick contribuye exactamente `quantity / 1e8`

### Ratio: Spot vs Futures

| Fuente | Volumen |
|--------|---------|
| CCXT Spot | 205.04 |
| CCXT Futures | 1,339.79 |
| Ratio Futures/Spot | **6.54x** |

### Ratio Histórico (Velas Infladas)

Para velas históricas con volumen inflado (88.5% de 1000 velas), no es posible calcular el ratio exacto porque el stream de ticks no tiene datos para el período inflado completo. Las velas infladas muestran:

| Estadística | Volumen (BTC/hr) |
|-------------|-------------------|
| Media | ~44,866 |
| Mediana | ~45,165 |
| P5 | ~353 |
| P95 | ~95,959 |

Esto representa aproximadamente **175-220x** el volumen esperado de CCXT Spot (~250 BTC/hr).

### Conclusión

- **Ratio actual CandleBuilder/CCXT = 1.00x** → Pipeline correcto
- **Ratio actual CandleBuilder/TickStream = 1.00x** → Acumulación de ticks correcta
- **Ratio Futures/Spot = 6.54x** → No explica el factor 175-220x
- **El bug de volumen inflado NO está activo actualmente**
