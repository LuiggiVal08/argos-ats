# Forward Test Experiment 001 — Metrics Dashboard

> Referencia completa de métricas disponibles para el experimento FT-001.
> Organizadas por categoría con fuente de datos y código de cómputo.

---

## 1. Modelo

### Inferencias

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `inference_count` | Total de inferencias realizadas | `signals_btc.csv` | `_compute_signal_distribution()` |
| `buy_predictions` | Señales BUY generadas | `signals_btc.csv` | `_compute_signal_distribution()` |
| `sell_predictions` | Señales SELL generadas | `signals_btc.csv` | `_compute_signal_distribution()` |
| `hold_predictions` | Señales HOLD generadas | `signals_btc.csv` | `_compute_signal_distribution()` |

### Confianza

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `confidence_mean` | Media de probabilidades del modelo | `signals_btc.csv` | `_compute_confidence_stats()` |
| `confidence_std` | Desviación estándar de probabilidades | `signals_btc.csv` | `_compute_confidence_stats()` |
| `confidence_min` | Probabilidad mínima | `signals_btc.csv` | `_compute_confidence_stats()` |
| `confidence_max` | Probabilidad máxima | `signals_btc.csv` | `_compute_confidence_stats()` |

### Distribución de Señales

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `signal_distribution.buy_pct` | % de señales BUY | `signals_btc.csv` | `_compute_signal_distribution()` |
| `signal_distribution.sell_pct` | % de señales SELL | `signals_btc.csv` | `_compute_signal_distribution()` |
| `signal_distribution.hold_pct` | % de señales HOLD | `signals_btc.csv` | `_compute_signal_distribution()` |

### Calibración

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `calibration.bins` | Bines de calibración (predicted vs actual) | `MetricsTracker` | `compute_calibration()` |
| `calibration.n_evaluable` | Predicciones evaluables | `MetricsTracker` | `compute_calibration()` |

### Estabilidad

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `y_proba_stability.mean` | Media rolling de probabilidades | `MetricsTracker` | `compute_y_proba_stability()` |
| `y_proba_stability.std` | Std rolling de probabilidades | `MetricsTracker` | `compute_y_proba_stability()` |
| `y_proba_stability.recent_mean` | Media de últimos 100 valores | `MetricsTracker` | `compute_y_proba_stability()` |

---

## 2. Trading

### Trades

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `executed_trades` | Trades ejecutados en el día | `trades_btc.csv` | `_compute_trading_metrics()` |
| `wins` | Trades ganadores | `trades_btc.csv` | `_compute_trading_metrics()` |
| `losses` | Trades perdedores | `trades_btc.csv` | `_compute_trading_metrics()` |
| `win_rate` | % de trades ganadores | `trades_btc.csv` | `_compute_trading_metrics()` |

### PnL

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `total_pnl` | PnL total del día | `trades_btc.csv` | `_compute_trading_metrics()` |
| `avg_pnl` | PnL promedio por trade | `trades_btc.csv` | `_compute_trading_metrics()` |
| `max_win` | Trade ganador más grande | `trades_btc.csv` | `_compute_trading_metrics()` |
| `max_loss` | Trade perdedor más grande | `trades_btc.csv` | `_compute_trading_metrics()` |
| `total_gross_pnl` | PnL bruto (sin costos) | `MetricsTracker` | `compute_trading_metrics()` |
| `total_net_pnl` | PnL neto (con costos) | `MetricsTracker` | `compute_trading_metrics()` |

### Expectancy

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `expectancy` | PnL promedio esperado por trade | `MetricsTracker` | `compute_trading_metrics()` |
| `profit_factor` | Ratio avg_win / avg_loss | `MetricsTracker` | `compute_trading_metrics()` |
| `avg_win` | Ganancia promedio | `MetricsTracker` | `compute_trading_metrics()` |
| `avg_loss` | Pérdida promedio | `MetricsTracker` | `compute_trading_metrics()` |

### Turnover

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `turnover_rate` | Ratio de exposición bruta / n_trades | `MetricsTracker` | `compute_trading_metrics()` |
| `cost_ratio` | Ratio de costos / PnL neto | `MetricsTracker` | `compute_trading_metrics()` |
| `max_adverse_excursion` | Peor pérdida bruta | `MetricsTracker` | `compute_trading_metrics()` |

### Exposure

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `exposure_pct` | % de tiempo en posición | `MetricsTracker` | `compute_exposure_metrics()` |
| `idle_pct` | % de tiempo fuera de posición | `MetricsTracker` | `compute_exposure_metrics()` |
| `bars_in_position` | Barras en posición | `MetricsTracker` | `compute_exposure_metrics()` |

---

## 3. Riesgo

### Drawdown

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `drawdown` | Drawdown actual (%) | `equity_btc.csv` | `_compute_equity_metrics()` |
| `peak_equity` | Equity máximo alcanzado | `equity_btc.csv` | `_compute_equity_metrics()` |
| `max_drawdown` | Drawdown máximo histórico | `MetricsTracker` | `compute_drawdown_curve()` |

### Circuit Breaker

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `circuit_breaker_activations` | Veces que se activó el circuit breaker | Logs | Derivable |
| `cooldown_periods` | Períodos de cooldown | Logs | Derivable |

### Posiciones

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `open_positions` | Posiciones abiertas actuales | `state_btc.json` | `_read_state()` |
| `closed_positions` | Posiciones cerradas en el día | `trades_btc.csv` | `_compute_trading_metrics()` |

---

## 4. Portfolio Context

### Clusters

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `clusters_detected` | Clusters detectados | Portfolio Context logs | Derivable |
| `clusters_blocked` | Señales bloqueadas por cluster limit | Portfolio Context logs | Derivable |

### Posiciones

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `avg_position_size` | Tamaño promedio de posición | `trades_btc.csv` | Derivable |
| `position_distribution` | Distribución de posiciones por cluster | Portfolio Context logs | Derivable |

### Señales

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `blocked_portfolio_context` | Señales descartadas por PC | ExecutionGuard logs | Derivable |

---

## 5. Mercado

### Régimen

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `trending_count` | Barras en régimen TRENDING | `signals_btc.csv` | `_compute_regime_distribution()` |
| `ranging_count` | Barras en régimen RANGING | `signals_btc.csv` | `_compute_regime_distribution()` |
| `regime_distribution` | Distribución completa de regímenes | `signals_btc.csv` | `_compute_regime_distribution()` |

### Volatilidad

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `volatility_ratio` | ATR / price ratio | Features | Derivable |
| `volatility_spikes` | Veces que volatilidad superó 2x trailing | ExecutionGuard logs | Derivable |

### Drift

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `feature_drift_score` | Score de drift de features | Pendiente | `None` (no disponible aún) |

---

## 6. Observabilidad

### Errores

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `errors_by_type` | Errores agrupados por tipo | Service logs | Derivable |
| `critical_errors` | Errores críticos | Service logs | Derivable |

### Performance

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `inference_latency_p50` | Latencia p50 de inferencia | `SystemHealthCollector` | `snapshot()` |
| `inference_latency_p95` | Latencia p95 de inferencia | `SystemHealthCollector` | `snapshot()` |
| `buffer_occupancy_pct` | % de ocupación del buffer | `SystemHealthCollector` | `snapshot()` |
| `stream_lag_ms` | Lag del stream en ms | `SystemHealthCollector` | `snapshot()` |

### Salud

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `inference_health` | Estado del engine de inferencia | `SystemHealthCollector` | `snapshot()` |
| `candle_rate_ok` | Si el rate de candles es correcto | `SystemHealthCollector` | `snapshot()` |
| `execution_activity` | Nivel de actividad de ejecución | `SystemHealthCollector` | `snapshot()` |
| `missed_candles` | Candles perdidos | `SystemHealthCollector` | `snapshot()` |
| `desync_count` | Veces que se detectó desync | `SystemHealthCollector` | `snapshot()` |

### Runtime

| Métrica | Descripción | Fuente | Código |
|---|---|---|---|
| `runtime_hours` | Horas desde inicio del experimento | Cálculo | `_compute_runtime_hours()` |
| `uptime_seconds` | Segundos de uptime del servicio | `SystemHealthCollector` | `snapshot()` |

---

## 7. Snapshots Diarios

### Archivos generados

| Archivo | Contenido |
|---|---|
| `reports/forward-test/YYYY-MM-DD.json` | Snapshot completo del día |
| `reports/forward-test/daily_report.json` | Reporte generado por MetricsTracker |
| `reports/forward-test/trades.csv` | Trades del día |

### Generación

```bash
# Generar snapshot para hoy
python scripts/generate_daily_snapshot.py

# Generar snapshot para una fecha específica
python scripts/generate_daily_snapshot.py --date 2026-07-15
```

### Estructura del Snapshot

```json
{
    "experiment_id": "FT-001",
    "date": "2026-07-10",
    "git_tag": "v0.9.1-forward-test",
    "git_commit": "619603e",
    "model_id": "qv2_target_spec_v1_reduced_33_primary",
    "model_checksum": "551be07c74d8cba6363a4852fb5b2488",
    "inference_count": 24,
    "buy_predictions": 5,
    "sell_predictions": 3,
    "hold_predictions": 16,
    "executed_trades": 2,
    "blocked_threshold": 0,
    "blocked_portfolio_context": 0,
    "blocked_risk": 0,
    "blocked_execution_guard": 0,
    "confidence_mean": 0.5234,
    "confidence_std": 0.1234,
    "equity": 100500.00,
    "pnl": 500.00,
    "drawdown": 1.23,
    "open_positions": 1,
    "closed_positions": 2,
    "trending_count": 18,
    "ranging_count": 6,
    "feature_drift_score": null,
    "runtime_hours": 24.0
}
```

---

## 8. Generación de Reportes Diarios

### Reporte tipo (stdout)

```
================================================
Forward Test Daily Report
================================================
Versión:     v0.9.1-forward-test
Commit:      619603e
Tag:         v0.9.1-forward-test
Modelo:      qv2_target_spec_v1_reduced_33_primary
Tiempo:      24.0h
Inferencias: 24
Trades:      2 ejecutados / 0 bloqueados
BUY: 5  SELL: 3  HOLD: 16
Confianza:   0.5234 ± 0.1234
PnL:         +500.00
Drawdown:    1.23%
Equity:      100500.00
Régimen:     TRENDING (75%)
Observaciones: Ninguna
Anomalías:   Ninguna
Health:      OK
================================================
```

### Generación

```bash
python scripts/generate_daily_snapshot.py --report
```

---

**Última actualización**: 2026-07-10
