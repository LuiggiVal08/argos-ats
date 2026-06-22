---
description: Lanza una corrida de backtest con ENVIRONMENT_MODE=BACKTESTING y reporta métricas de rendimiento
agent: build
---

You are executing a backtest run for the argos-ats project, as defined in @spec.md section 5 Historia 5 (Sad Path: `BACKTESTING`).

Parameters from the user (all optional, defaults shown):
- Symbol/pair: $1 (default: `BTCUSDT`)
- Date range: $2 (default: `last-90d`, format `YYYY-MM-DD..YYYY-MM-DD` accepted)
- Initial capital USDT: $3 (default: `10000`)

Re-read @spec.md sections 1.3 (Hexagonal), 2 (Indicadores), 3 (Lógica de decisión) and 5 Historia 5 (Backtesting mode) before running.

Pre-flight checks (run from repo root):
```
!`test -f apps/analytics-engine/pyproject.toml && echo "OK: analytics-engine scaffolded" || echo "MISSING: analytics-engine needs scaffolding (use scaffold-domain / scaffold-usecase / scaffold-adapter)"`
!`grep -r "ENVIRONMENT_MODE" apps/analytics-engine/app/ 2>/dev/null | head -3 || echo "NOTE: ENVIRONMENT_MODE not yet referenced in analytics-engine code"`
```

Execution contract:
1. Export the environment for the backtest subprocess:
   - `ENVIRONMENT_MODE=BACKTESTING`
   - `BACKTEST_SYMBOL=$1` (or `BTCUSDT`)
   - `BACKTEST_RANGE=$2` (or `last-90d`)
   - `BACKTEST_CAPITAL=$3` (or `10000`)
2. Disable any side-effects that would touch the live exchange or the network. The infrastructure adapter for the data loader must be the historical CSV/DB reader (spec §5 Historia 5 Happy Path).
3. Invoke the analytics-engine entry point. If a dedicated `python -m app.backtest` exists, use it. Otherwise invoke the FastAPI app's `ProcesarSenalIA` use case directly with a fixture loader — and report this fallback.
4. Capture stdout/stderr to `reports/backtest/<timestamp>.log` and the JSON metrics to `reports/backtest/<timestamp>.json`.

Required output metrics (in the JSON):
- `sharpe_ratio` — annualized, risk-free rate = 0
- `max_drawdown_pct` — peak-to-trough on equity curve
- `win_rate` — winning trades / total trades
- `total_trades`
- `avg_trade_pnl_usdt`
- `final_equity_usdt`
- `circuit_breaker_triggered` — boolean; must be `false` in a healthy backtest
- `position_size_violations` — count of trades that exceeded the 1% risk-per-trade rule (spec §5 Historia 2)

Rules:
1. Never run in `PAPER_TRADING` or `LIVE` mode. If the env injection is missing or ambiguous, abort with exit code 1 and a clear error (mirrors spec §5 Historia 5 Sad Path).
2. Never reach the public network. If the data loader attempts an outbound request, abort and flag it.
3. Do not modify the Domain or Application layers. If metrics reveal a bug, report file:line and suggest the matching `scaffold-*` command, but do not patch.
4. Print a final 1-screen summary table in the terminal after the run completes, regardless of pass/fail.

If the analytics-engine is not yet scaffolded, output a `MISSING` block describing exactly which files would be needed (entry point, backtest orchestrator, metrics reporter) and exit without running.
