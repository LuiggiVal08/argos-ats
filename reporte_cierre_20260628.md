# Reporte de Cierre — 2026-06-28

## Resumen de trades del día

| Trade | Señal | Confianza | Riesgo | Tamaño | SL | TP | Resultado |
|-------|-------|-----------|--------|--------|----|----|-----------|
| 1 | SELL | 94.6% | 1.0% | 0.0016 BTC | 61416.07 | 60199.91 | Abierto |
| 2 | SELL | 95.5% | bloqueado | — | — | — | No ejecutado (position_exists) |

## Posición abierta

- **ID:** 1d39a08e1836
- **Side:** SELL
- **Entry:** 60807.99
- **Cantidad:** 0.0016 BTC
- **SL:** 61416.07 (+1.0%)
- **TP:** 60199.91 (−1.0%)
- **Current price (estimado):** 60807.99 (no se actualizó — posible problema de data feed)

## Problemas detectados

1. **Current price congelado**: el monitor muestra `current_price: 60807.99` igual al entry. Esto puede ser porque el `MonitorPositionsUseCase` no está refrescando el precio o el data feed no está inyectando ticks nuevos luego del restart.

2. **close_partial faltante**: el `MonitorPositionsUseCase` llama a `close_partial` si el drawdown excede el 50% del SL, pero `CcxtBinanceTestnetAdapter` no lo implementaba. **Ya se agregó** (vende `quantity` del base a market).

3. **Segundo trade bloqueado**: el risk manager detectó posición abierta existente (`position_exists`) y emitió HOLD con riesgo `blocked`. Es correcto — no permite apilar posiciones.

## Inference log (últimas predicciones)

| Timestamp | HOLD | SELL | BUY | Señal final | Riesgo |
|-----------|------|------|-----|-------------|--------|
| 04:40:XX | 5.4% | 94.6% | 0.0% | SELL | blocked (position_exists) |
| 04:20:XX | 4.5% | 95.5% | 0.0% | SELL | ok → posición abierta |
| 04:00:XX | 40.0% | 60.0% | 0.0% | SELL | ok → posición abierta |

## arch_lint aplicado

Se agregó `close_partial(symbol, quantity)` a `CcxtBinanceTestnetAdapter` en `apps/analytics-engine/app/infrastructure/trading/ccxt_binance_adapter.py:317`.
