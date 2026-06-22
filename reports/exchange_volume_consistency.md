# Exchange Volume Consistency: Spot vs Futures

## Verificación: Spot vs USDT-M Futures

### CCXT Spot: `fetch_ohlcv('BTC/USDT', '1h')`

```python
binance.fetch_ohlcv('BTC/USDT', '1h', limit=1)
# Última vela (2026-06-21 17:00):
# O=64145.3 H=64221.4 L=64080.0 C=64151.5 VOL=205.04
```

### CCXT Futures: `fetch_ohlcv('BTC/USDT', '1h', {'defaultType': 'future'})`

```python
binance.fetch_ohlcv('BTC/USDT', '1h', {'defaultType': 'future'}, limit=1)
# Última vela (2026-06-21 17:00):
# O=64122.2 H=64197.1 L=64046.4 C=64114.6 VOL=1339.79
```

### Comparación

| Métrica | Spot | Futures | Ratio F/S |
|---------|------|---------|-----------|
| Open | 64145.3 | 64122.2 | 1.00x |
| Close | 64151.5 | 64114.6 | 1.00x |
| Volume | **205.04** | **1,339.79** | **6.54x** |

### Definición de Volumen en Binance

**Binance Spot** utiliza `@trade` stream → `q` = base asset quantity (BTC).

**Binance Futures** utiliza `@aggTrade` stream → `q` = base asset quantity (contratos BTC).

Ambos representan la cantidad base en BTC, pero el volumen de Futuros es naturalmente mayor (~6.5x para BTC/USDT).

### ¿Podría la Inflación de Volumen Ser por Uso de Futuros?

Si los candles inflados (50K+ BTC/hr) fueran de Futuros, el ratio sería:
```
50,000 / 1,339.79 = 37.3x por encima del volumen de Futuros
```

Esto descarta que el origen sea Futuros. La inflación es **37x superior incluso al volumen de Futuros**.

### Entrenamiento del Modelo: Spot o Futures?

El modelo fue entrenado con `fetch_ohlcv` de CCXT. Según el código de entrenamiento, la fuente de datos es `ccxt.binance()`. Sin especificar `defaultType`, CCXT por defecto usa **Spot**.

Esto significa que tanto el entrenamiento como la inferencia actual usan Spot, y el volumen actual (205 BTC/hr) es consistente.

### Conclusión

| Pregunta | Respuesta |
|----------|-----------|
| Spot usa base volume (BTC)? | ✅ Sí |
| Futures usa base volume (BTC)? | ✅ Sí |
| Ambos usan misma definición de volumen? | ✅ Sí (BTC) |
| Es el bug por usar Futures en vez de Spot? | ❌ No (inflación 37x > Futures volume) |
| Es el entrenamiento consistente con el pipeline actual? | ✅ Sí (ambos Spot) |
