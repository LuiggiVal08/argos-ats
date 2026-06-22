# P1: price_provider always returns Decimal("0") — Auditoría

**Fecha**: 2026-06-21
**Estado**: Hipótesis CONFIRMADA

## Resumen

`MonitorPositionsUseCase.price_provider` retorna `Decimal("0")` en todos los escenarios
excepto testnet LIVE/PAPER. Esto corrompe todos los cálculos de PnL no realizado,
break-even, trailing stop y stop-loss dinámico.

## Mapeo del flujo

```
composition.py:get_monitor_positions_usecase()
│
├── BACKTESTING → _NoopOrderClient
│   └── no get_price → _fake_price → Decimal("0")          ← BUG
│
├── LIVE/PAPER + testnet → CcxtBinanceTestnetAdapter
│   └── tiene get_price() → fetch_ticker() → precio REAL   ← OK
│
└── LIVE/PAPER + no testnet → CcxtOrderClient
    └── NO tiene get_price → _fake_price → Decimal("0")     ← BUG

main.py:_build_monitor_uc()
└── SIEMPRE _fake_price → Decimal("0")                      ← BUG
```

## Causa raíz

`CcxtOrderClient` (el adapter de producción) no implementa `get_price()`.
La condición en composition.py línea 1237:

```python
if _is_testnet() and hasattr(exchange_client, "get_price"):
    price_provider = exchange_client.get_price
else:
    async def _fake_price(symbol: str) -> Decimal:
        return Decimal("0")
    price_provider = _fake_price
```

Falla a `_fake_price` para cualquier cliente que no tenga `get_price`,
que es el caso de `CcxtOrderClient` en producción real.

## Hallazgo secundario (P1b)

`_position_monitor_loop` en main.py:592-609 acepta `monitor_uc` como parámetro
pero NUNCA llama a `monitor_uc.run()`. El loop solo:
1. Duerme 5s
2. Salta si BACKTESTING
3. Checkea drawdown halt

Sin `run()`, SL/TP nunca se actualizan, PnL no se registra,
y take-profit no se ejecuta. Es un silent no-op.

## Fix requerido

1. Añadir `get_price(symbol: str) -> Decimal` a `CcxtOrderClient` vía `fetch_ticker()`
2. Simplificar composition.py: siempre usar `exchange_client.get_price` si existe,
   o construir un provider inline con `fetch_ticker()` sobre el exchange directo
3. Actualizar `_build_monitor_uc` en main.py para usar precio real
4. Hacer que `_position_monitor_loop` invoque `monitor_uc.run()`
5. Manejar errores de fetch_ticker (rate limit, timeout) con fallback al último precio conocido

## Archivos afectados

| Archivo | Rol |
|---|---|
| `apps/analytics-engine/app/infrastructure/exchange/ccxt_order_client.py` | Añadir `get_price()` |
| `apps/analytics-engine/app/composition.py:1237-1242` | Usar precio real siempre |
| `apps/analytics-engine/app/main.py:849-850` | `_build_monitor_uc` usar precio real |
| `apps/analytics-engine/app/main.py:592-609` | Invocar `monitor_uc.run()` |
| `apps/analytics-engine/app/infrastructure/trading/ccxt_binance_adapter.py` | Referencia: implementación existente de get_price |
